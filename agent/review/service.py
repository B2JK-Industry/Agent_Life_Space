"""
Agent Life Space — Review Service

First-class review workflow. Channel-independent, job-centric.

Flow:
    1. intake → validate input
    2. create job → persist
    3. prepare workspace (if needed)
    4. analyze → deterministic analyzers
    5. verify → false-positive reduction, consistency
    6. report → structured output (Markdown + JSON)
    7. store artifacts → persist
    8. return result

This service does NOT:
    - format for Telegram
    - call LLM (deterministic analysis only in v1)
    - handle authentication
    - manage channels
"""

from __future__ import annotations

from typing import Any

import structlog

from agent.control.models import (
    ArtifactKind,
    DeliveryLifecycleStatus,
    DeliveryPackage,
    ExecutionMode,
    JobKind,
    JobStatus,
    TraceRecordKind,
)
from agent.control.policy import get_delivery_policy, select_review_execution_policy
from agent.review.analyzers import (
    analyze_diff,
    analyze_repo_structure,
    analyze_security,
)
from agent.review.models import (
    ArtifactType,
    Confidence,
    ReviewArtifact,
    ReviewFinding,
    ReviewIntake,
    ReviewJob,
    ReviewJobStatus,
    ReviewJobType,
    ReviewPhase,
    ReviewReport,
    Severity,
)
from agent.review.storage import ReviewStorage
from agent.review.verifier import verify_report

logger = structlog.get_logger(__name__)


class ReviewService:
    """
    Orchestrates review jobs end-to-end.
    Channel-independent — Telegram/API/CLI are just adapters.
    """

    def __init__(
        self,
        storage: ReviewStorage | None = None,
        workspace_manager: Any = None,
        approval_queue: Any = None,
        control_plane_state: Any = None,
    ) -> None:
        self._storage = storage or ReviewStorage()
        self._workspace_manager = workspace_manager
        self._approval_queue = approval_queue
        self._control_plane_state = control_plane_state
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        self._storage.initialize()
        self._initialized = True

    async def run_review(self, intake: ReviewIntake) -> ReviewJob:
        """Run a complete review job. Returns the finished job with report."""
        self.initialize()

        # ── Step 1: Validate ──
        job = ReviewJob(
            job_type=intake.review_type,
            source=intake.source or "manual",
            requester=intake.requester,
            intake=intake,
        )
        t_validate = job.trace("validate")

        errors = intake.validate()
        if errors:
            t_validate.fail("; ".join(errors))
            job.status = JobStatus.FAILED
            job.error = f"Validation failed: {'; '.join(errors)}"
            self._save_job(job)
            return job

        t_validate.complete(f"input valid: {intake.review_type.value}")
        job.status = JobStatus.VALIDATING
        job.phase = ReviewPhase.VALIDATING
        self._save_job(job)

        # ── Step 1b: Workspace + execution mode ──
        # Workspace is created for lifecycle tracking, but v1 analyzers
        # always read from host via intake.repo_path. Execution mode
        # must reflect reality — READ_ONLY_HOST until analyzers actually
        # read from workspace path (v2 scope).
        job.execution_mode = ExecutionMode.READ_ONLY_HOST

        if self._workspace_manager is not None:
            t_ws = job.trace("workspace")
            try:
                ws = self._workspace_manager.create(
                    name=f"review-{job.id[:8]}",
                    task_id=job.id,
                )
                self._workspace_manager.activate(ws.id)
                job.workspace_id = ws.id
                t_ws.complete(
                    f"workspace {ws.id} (lifecycle only, "
                    f"analysis reads host path)"
                )
            except Exception as e:
                t_ws.fail(str(e))
                logger.warning("review_workspace_failed", error=str(e))

        # ── Step 1c: Execution policy audit ──
        t_policy = job.trace("execution_policy")
        analysis_path = self._get_analysis_path(job)
        review_policy = select_review_execution_policy(
            review_type=intake.review_type,
            diff_spec=intake.diff_spec,
            source=job.source,
        )
        if not review_policy.allow_host_read:
            detail = (
                f"review execution blocked by policy {review_policy.id}: "
                f"{review_policy.description}"
            )
            t_policy.fail(detail)
            job.status = JobStatus.BLOCKED
            job.error = detail
            self._record_review_policy_trace(
                job=job,
                policy=review_policy,
                analysis_path=analysis_path,
                allowed=False,
            )
            self._save_job(job)
            return job
        if intake.diff_spec and not review_policy.allow_git_subprocess:
            detail = (
                f"review diff access blocked by policy {review_policy.id}: "
                "git subprocess execution is not allowed"
            )
            t_policy.fail(detail)
            job.status = JobStatus.BLOCKED
            job.error = detail
            self._record_review_policy_trace(
                job=job,
                policy=review_policy,
                analysis_path=analysis_path,
                allowed=False,
            )
            self._save_job(job)
            return job
        t_policy.complete(
            f"mode={job.execution_mode.value}, "
            f"analysis_path={analysis_path}, "
            f"source={job.source}, "
            f"policy={review_policy.id}, "
            f"host_access={'read_only' if review_policy.allow_host_read else 'blocked'}, "
            f"git_subprocess={'yes' if review_policy.allow_git_subprocess else 'no'}"
        )
        self._record_review_policy_trace(
            job=job,
            policy=review_policy,
            analysis_path=analysis_path,
            allowed=True,
        )

        # ── Step 2: Analyze ──
        job.status = JobStatus.RUNNING
        job.phase = ReviewPhase.ANALYZING
        job.timing.mark_started()

        if intake.review_type == ReviewJobType.REPO_AUDIT:
            report = await self._run_repo_audit(job)
        elif intake.review_type == ReviewJobType.PR_REVIEW:
            report = await self._run_pr_review(job)
        elif intake.review_type == ReviewJobType.RELEASE_REVIEW:
            report = await self._run_release_review(job)
        else:
            report = ReviewReport(
                executive_summary=f"Unsupported review type: {intake.review_type.value}",
                verdict="error",
            )

        job.report = report

        # ── Step 3: Verify ──
        job.status = JobStatus.VERIFYING
        job.phase = ReviewPhase.VERIFYING
        t_verify = job.trace("verify")
        pre_count = len(report.findings)
        verify_report(report)
        post_count = len(report.findings)
        t_verify.complete(f"verified: {pre_count} → {post_count} findings")

        # ── Step 4: Generate verdict ──
        t_verdict = job.trace("verdict")
        if report.has_critical:
            report.verdict = "fail"
        elif report.findings:
            report.verdict = "pass_with_findings"
        else:
            report.verdict = "pass"
        t_verdict.complete(f"verdict: {report.verdict}")

        # ── Step 5: Create artifacts ──
        job.phase = ReviewPhase.REPORTING
        t_artifacts = job.trace("artifacts")

        # Markdown report
        md_artifact = ReviewArtifact(
            artifact_type=ArtifactType.REVIEW_REPORT,
            job_id=job.id,
            content=report.to_markdown(),
            format="markdown",
        )
        job.artifacts.append(md_artifact)
        self._save_artifact(job, md_artifact)

        # JSON report
        json_artifact = ReviewArtifact(
            artifact_type=ArtifactType.REVIEW_REPORT,
            job_id=job.id,
            content="",
            content_json=report.to_dict(),
            format="json",
        )
        job.artifacts.append(json_artifact)
        self._save_artifact(job, json_artifact)

        # Execution trace artifact
        trace_artifact = ReviewArtifact(
            artifact_type=ArtifactType.EXECUTION_TRACE,
            job_id=job.id,
            content="",
            content_json={"trace": [t.to_dict() for t in job.execution_trace]},
            format="json",
        )
        job.artifacts.append(trace_artifact)
        self._save_artifact(job, trace_artifact)

        # Findings-only export (if there are findings)
        if report.findings:
            findings_artifact = ReviewArtifact(
                artifact_type=ArtifactType.FINDING_LIST,
                job_id=job.id,
                content="",
                content_json={"findings": [f.to_dict() for f in report.findings]},
                format="json",
            )
            job.artifacts.append(findings_artifact)
            self._save_artifact(job, findings_artifact)

        t_artifacts.complete(f"{len(job.artifacts)} artifacts created")

        # ── Step 6: Complete ──
        job.status = JobStatus.COMPLETED
        job.phase = ReviewPhase.COMPLETED
        job.timing.mark_completed()
        self._save_job(job)

        logger.info(
            "review_completed",
            job_id=job.id,
            job_type=job.job_type.value,
            findings=len(report.findings),
            verdict=report.verdict,
        )

        return job

    async def _run_repo_audit(self, job: ReviewJob) -> ReviewReport:
        """Full repository audit: structure + security + quality."""
        intake = job.intake
        analysis_path = self._get_analysis_path(job)
        report = ReviewReport()
        all_findings: list = []

        # Structure analysis
        t_structure = job.trace("analyze:structure")
        metrics, structure_findings = analyze_repo_structure(
            analysis_path,
            max_files=intake.max_files,
            include_patterns=intake.include_patterns or None,
            exclude_patterns=intake.exclude_patterns or None,
        )
        all_findings.extend(structure_findings)
        report.files_analyzed = metrics.total_files
        report.total_lines = metrics.total_lines
        t_structure.complete(f"{metrics.total_files} files, {metrics.total_lines} lines")

        # Security analysis
        t_security = job.trace("analyze:security")
        security_findings = analyze_security(
            analysis_path,
            max_files=intake.max_files,
            include_patterns=intake.include_patterns or None,
        )
        all_findings.extend(security_findings)
        t_security.complete(f"{len(security_findings)} security findings")

        # Build report
        report.findings = all_findings
        report.scope_description = (
            f"Repo audit of {intake.repo_path}. "
            f"{metrics.total_files} files, {metrics.total_lines} lines. "
            f"Languages: {', '.join(list(metrics.languages.keys())[:5])}."
        )

        # Executive summary
        counts = report.finding_counts
        report.executive_summary = self._build_executive_summary(metrics, counts, intake)

        # Assumptions
        report.assumptions = [
            "Analysis is static only — no runtime behavior tested.",
            "Secret detection uses pattern matching — may produce false positives.",
        ]
        if intake.focus_areas:
            report.assumptions.append(f"Focus areas: {', '.join(intake.focus_areas)}")

        return report

    async def _run_pr_review(self, job: ReviewJob) -> ReviewReport:
        """PR/diff review: diff analysis + security check on changed files."""
        intake = job.intake
        analysis_path = self._get_analysis_path(job)
        report = ReviewReport()
        all_findings: list = []

        # Diff analysis
        t_diff = job.trace("analyze:diff")
        diff_summary, diff_findings, raw_diff = analyze_diff(
            analysis_path, intake.diff_spec,
        )
        all_findings.extend(diff_findings)
        report.files_analyzed = diff_summary.files_changed
        t_diff.complete(
            f"{diff_summary.files_changed} files, "
            f"+{diff_summary.insertions}/-{diff_summary.deletions}"
        )

        # Security on changed files
        t_security = job.trace("analyze:security")
        changed_paths = [f["path"] for f in diff_summary.changed_files]
        if changed_paths:
            security_findings = analyze_security(
                analysis_path,
                max_files=len(changed_paths),
                include_patterns=[f"*{p.strip()}" for p in changed_paths[:50]],
            )
            all_findings.extend(security_findings)
        t_security.complete(f"{len(all_findings)} total findings")

        report.findings = all_findings
        report.scope_description = (
            f"PR review: {intake.diff_spec}. "
            f"{diff_summary.files_changed} files changed, "
            f"+{diff_summary.insertions}/-{diff_summary.deletions} lines."
        )

        counts = report.finding_counts
        report.executive_summary = (
            f"PR review pre `{intake.diff_spec}`. "
            f"{diff_summary.files_changed} súborov zmenených. "
            f"Nájdené: {counts['critical']} critical, {counts['high']} high, "
            f"{counts['medium']} medium, {counts['low']} low."
        )

        if diff_summary.has_security_relevant:
            report.open_questions.append(
                "Diff touches security-relevant files — manual review recommended."
            )

        return report

    async def _run_release_review(self, job: ReviewJob) -> ReviewReport:
        """Release readiness review: repo audit + config/CI checks."""
        # Release review = repo audit + additional release-specific checks
        report = await self._run_repo_audit(job)

        t_release = job.trace("analyze:release")

        # Additional release checks
        from pathlib import Path
        root = Path(self._get_analysis_path(job))

        if not (root / "CHANGELOG.md").exists():
            report.findings.append(ReviewFinding(
                severity=Severity.MEDIUM,
                title="No CHANGELOG.md",
                category="release",
                recommendation="Add changelog for release tracking.",
                confidence=Confidence.HIGH,
            ))

        if not (root / "pyproject.toml").exists() and not (root / "setup.py").exists() and not (root / "package.json").exists():
            report.findings.append(ReviewFinding(
                severity=Severity.MEDIUM,
                title="No package configuration found",
                category="release",
                confidence=Confidence.HIGH,
            ))

        report.executive_summary = f"Release readiness review. {report.executive_summary}"
        t_release.complete("release checks done")

        return report

    # Need this import at class level for release review
    def _build_executive_summary(
        self, metrics: Any, counts: dict[str, int], intake: ReviewIntake,
    ) -> str:
        parts = [
            f"Repo audit pre `{intake.repo_path}`.",
            f"{metrics.total_files} súborov, {metrics.total_lines} riadkov kódu.",
        ]
        if sum(counts.values()) == 0:
            parts.append("Žiadne nálezy.")
        else:
            parts.append(
                f"Nájdené: {counts['critical']} critical, {counts['high']} high, "
                f"{counts['medium']} medium, {counts['low']} low."
            )
        if metrics.has_tests:
            parts.append(f"Testy: {metrics.test_files} test súborov.")
        else:
            parts.append("Testy: žiadne.")
        if metrics.has_ci:
            parts.append("CI: áno.")
        return " ".join(parts)

    def _save_job(self, job: ReviewJob) -> None:
        self._storage.save_job(job)
        self._sync_product_job(job)

    def _save_artifact(self, job: ReviewJob, artifact: ReviewArtifact) -> None:
        self._storage.save_artifact(artifact)
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_retained_artifact(
            record_id=artifact.id,
            artifact_id=artifact.id,
            job_id=job.id,
            job_kind=job.job_kind,
            artifact_kind=self._shared_artifact_kind(artifact.artifact_type),
            source_type="review_artifact",
            title=artifact.artifact_type.value,
            artifact_format=artifact.format,
            created_at=artifact.created_at,
            content=artifact.content,
            content_json=artifact.content_json,
            metadata={
                "workspace_id": job.workspace_id,
                "review_type": job.job_type.value,
                "verdict": job.report.verdict,
            },
        )

    def _sync_product_job(self, job: ReviewJob) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_product_job(
            job_id=job.id,
            job_kind=job.job_kind,
            title=job.intake.context or job.job_type.value,
            status=job.status.value,
            subkind="review_job",
            requester=job.requester,
            source=job.source,
            execution_mode=job.execution_mode.value,
            workspace_id=job.workspace_id,
            scope=job.intake.diff_spec or job.intake.repo_path,
            outcome=job.report.verdict,
            blocked_reason=job.error,
            artifact_ids=[artifact.id for artifact in job.artifacts],
            created_at=job.timing.created_at,
            completed_at=job.timing.completed_at,
            duration_ms=job.timing.duration_ms,
            retry_count=0,
            failure_count=1 if job.status in {ReviewJobStatus.FAILED, ReviewJobStatus.BLOCKED} else 0,
            usage=job.usage,
            metadata={
                "review_type": job.job_type.value,
                "phase": job.phase.value,
                "environment_profile_id": "review_host_read_only",
                "timing": job.timing.to_dict(),
                "finding_counts": job.report.finding_counts,
                "focus_areas": list(job.intake.focus_areas),
                "include_patterns": list(job.intake.include_patterns),
                "exclude_patterns": list(job.intake.exclude_patterns),
                "review_execution_policy_id": self._review_policy_id(job),
                "error": job.error,
                "last_error": job.error,
            },
        )

    def _record_review_policy_trace(
        self,
        *,
        job: ReviewJob,
        policy: Any,
        analysis_path: str,
        allowed: bool,
    ) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_trace(
            trace_kind=TraceRecordKind.REVIEW_POLICY,
            title="Review execution policy decision",
            detail=(
                f"policy={policy.id}; allowed={allowed}; source={job.source}; "
                f"review_type={job.job_type.value}; diff={bool(job.intake.diff_spec)}"
            ),
            job_id=job.id,
            workspace_id=job.workspace_id,
            metadata={
                "policy_id": policy.id,
                "policy_label": policy.label,
                "policy_description": policy.description,
                "analysis_path": analysis_path,
                "allow_host_read": policy.allow_host_read,
                "allow_git_subprocess": policy.allow_git_subprocess,
                "source": job.source,
                "review_type": job.job_type.value,
                "diff_spec": job.intake.diff_spec,
                "allowed": allowed,
            },
        )

    def _review_policy_id(self, job: ReviewJob) -> str:
        policy = select_review_execution_policy(
            review_type=job.job_type,
            diff_spec=job.intake.diff_spec,
            source=job.source,
        )
        return policy.id

    def _record_delivery_bundle_retention(
        self,
        *,
        job: ReviewJob,
        bundle: dict[str, Any],
    ) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_retained_artifact(
            record_id=self._delivery_bundle_id(job.id),
            bundle_id=self._delivery_bundle_id(job.id),
            job_id=job.id,
            job_kind=job.job_kind,
            artifact_kind=ArtifactKind.DELIVERY_BUNDLE,
            source_type="review_delivery_bundle",
            title=f"Review delivery bundle for {job.id[:8]}",
            artifact_format="json",
            created_at=job.created_at,
            content_json=bundle,
            metadata={
                "workspace_id": job.workspace_id,
                "verdict": job.report.verdict,
                "artifact_count": bundle.get("artifact_count", 0),
            },
        )

    def _shared_artifact_kind(self, artifact_type: ArtifactType) -> ArtifactKind:
        mapping = {
            ArtifactType.REVIEW_REPORT: ArtifactKind.REVIEW_REPORT,
            ArtifactType.FINDING_LIST: ArtifactKind.FINDING_LIST,
            ArtifactType.EXECUTION_TRACE: ArtifactKind.EXECUTION_TRACE,
            ArtifactType.DIFF_ANALYSIS: ArtifactKind.DIFF_ANALYSIS,
            ArtifactType.SECURITY_REPORT: ArtifactKind.SECURITY_REPORT,
            ArtifactType.EXECUTIVE_SUMMARY: ArtifactKind.EXECUTIVE_SUMMARY,
        }
        return mapping.get(artifact_type, ArtifactKind.REVIEW_REPORT)

    def _get_analysis_path(self, job: ReviewJob) -> str:
        """Single source of truth for the path analyzers read from.

        In v1, this is always intake.repo_path (host filesystem).
        In v2/workspace-bound, this would resolve to the workspace
        copy of the repo.
        """
        # v1: always host path. execution_mode is READ_ONLY_HOST.
        return job.intake.repo_path

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve a stored job by ID (dict form)."""
        self.initialize()
        return self._storage.load_job(job_id)

    def load_job(self, job_id: str) -> ReviewJob | None:
        """Reconstruct a full ReviewJob from storage. Recovery-safe."""
        self.initialize()
        data = self._storage.load_job(job_id)
        if data is None:
            return None
        return ReviewJob.from_dict(data)

    def get_delivery_bundle(self, job_id: str) -> dict[str, Any] | None:
        """Assemble a delivery bundle for a completed review job.

        IMPORTANT: delivery_ready is always False here. Delivery requires
        explicit approval via request_delivery_approval(). The bundle is
        a preview — not a delivery authorization.

        Returns None if job not found.
        """
        self.initialize()
        job = self.load_job(job_id)
        if job is None:
            return None

        artifacts = self._storage.get_artifacts(job_id)
        bundle = self._assemble_delivery_bundle(job=job, artifacts=artifacts)
        self._sync_delivery_record(
            bundle=self._delivery_package_from_bundle(bundle),
            status=DeliveryLifecycleStatus.PREPARED,
            event_type="prepared",
            detail="Review delivery package assembled",
        )
        self._record_delivery_bundle_retention(job=job, bundle=bundle)
        return bundle

    def request_delivery_approval(self, job_id: str) -> dict[str, Any]:
        """Gate review delivery through approval queue.

        Creates an approval request for the delivery of a completed review.
        Delivery must be explicitly approved before external send.

        Returns approval request info or error.
        """
        self.initialize()
        job = self.load_job(job_id)
        if job is None:
            return {"error": f"Job '{job_id}' not found"}
        if job.status != ReviewJobStatus.COMPLETED:
            return {"error": f"Job '{job_id}' is {job.status.value}, not completed"}

        bundle = self.get_delivery_bundle(job_id)
        if bundle is None:
            return {"error": f"Delivery package for '{job_id}' could not be assembled"}

        # Approval queue is REQUIRED for external delivery.
        # Without it, delivery is blocked — not silently bypassed.
        if self._approval_queue is None:
            import os
            if os.environ.get("AGENT_DEV_MODE") == "1":
                # Development-only bypass — never in production
                logger.warning("delivery_approval_dev_bypass", job_id=job_id)
                return {
                    "job_id": job_id,
                    "bundle_id": self._delivery_bundle_id(job_id),
                    "delivery_ready": True,
                    "approval_bypassed": True,
                    "warning": "DEV MODE: approval bypassed. Not safe for production.",
                }
            return {
                "error": "Delivery blocked: no approval queue configured. "
                         "External delivery requires approval gating.",
                "job_id": job_id,
                "bundle_id": self._delivery_bundle_id(job_id),
                "delivery_ready": False,
            }

        from agent.core.approval import ApprovalCategory
        delivery_policy = get_delivery_policy()
        required_approvals = self._delivery_required_approvals(job)
        req = self._approval_queue.propose(
            category=ApprovalCategory.EXTERNAL,
            description=f"Deliver review report for job {job_id[:8]} ({job.report.verdict})",
            risk_level="medium",
            reason=f"Review of {job.intake.repo_path} — {len(job.report.findings)} findings",
            context={
                "job_id": job_id,
                "job_kind": job.job_kind.value,
                "workspace_id": job.workspace_id,
                "bundle_id": bundle["bundle_id"],
                "verdict": job.report.verdict,
                "finding_counts": job.report.finding_counts,
                "requester": job.requester,
                "artifact_ids": [artifact.id for artifact in job.artifacts],
                "delivery_policy_id": delivery_policy.id,
                "review_type": job.job_type.value,
            },
            required_approvals=required_approvals,
        )
        self._sync_delivery_record(
            bundle=self._delivery_package_from_bundle(bundle),
            status=DeliveryLifecycleStatus.AWAITING_APPROVAL,
            event_type="approval_requested",
            detail=f"Approval requested under delivery policy {delivery_policy.id}",
            approval_request_id=req.id,
            metadata={"delivery_policy_id": delivery_policy.id},
        )
        logger.info("review_delivery_approval_requested",
                     job_id=job_id, approval_id=req.id)
        return {
            "job_id": job_id,
            "bundle_id": bundle["bundle_id"],
            "approval_request_id": req.id,
            "approval_status": "pending",
            "required_approvals": req.required_approvals,
            "delivery_ready": False,
        }

    def _delivery_required_approvals(self, job: ReviewJob) -> int:
        counts = job.report.finding_counts
        if counts.get("critical", 0) > 0:
            return 2
        if job.report.verdict == "fail":
            return 2
        return 1

    def get_client_safe_bundle(self, job_id: str) -> dict[str, Any] | None:
        """Export a client-safe delivery bundle with policy-driven redaction.

        Uses agent.review.redaction policy to:
            - redact absolute paths, hostnames, secrets
            - strip execution trace and internal metadata
            - redact evidence in findings

        Returns redacted bundle or None if job not found.
        """
        bundle = self.get_delivery_bundle(job_id)
        if bundle is None:
            return None

        from agent.review.redaction import redact_bundle
        return redact_bundle(bundle)

    def get_delivery_record(self, job_id: str) -> dict[str, Any] | None:
        """Return persisted delivery lifecycle state for a review job."""
        bundle_id = self._delivery_bundle_id(job_id)
        record = self._refresh_delivery_record(bundle_id)
        if record is None:
            return None
        return record.to_dict()

    def mark_delivery_handed_off(self, job_id: str, *, note: str = "") -> dict[str, Any]:
        """Record final review handoff after approval."""
        bundle_id = self._delivery_bundle_id(job_id)
        record = self._refresh_delivery_record(bundle_id)
        if record is None:
            return {"error": f"Delivery record not found for job '{job_id}'"}
        if record.status not in {
            DeliveryLifecycleStatus.APPROVED,
            DeliveryLifecycleStatus.HANDED_OFF,
        }:
            return {
                "error": (
                    f"Delivery record '{bundle_id}' is {record.status.value}, "
                    "not approved for handoff"
                ),
                "bundle_id": bundle_id,
            }
        if self._approval_queue is not None and record.approval_request_id:
            self._approval_queue.mark_executed(record.approval_request_id)
        record = self._control_plane_state.mark_delivery_handed_off(
            bundle_id,
            detail=note or f"Review delivery for job {job_id[:8]} handed off",
        ) if self._control_plane_state is not None else record
        if record is None:
            return {"error": f"Delivery record not found for bundle '{bundle_id}'"}
        return record.to_dict()

    def list_jobs(self, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """List review jobs."""
        self.initialize()
        return self._storage.list_jobs(status=status, limit=limit)

    def list_artifacts(
        self,
        *,
        job_id: str = "",
        artifact_kind: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List persisted review artifacts for shared query/recovery."""
        self.initialize()
        return self._storage.list_artifacts(
            job_id=job_id,
            artifact_type=artifact_kind,
            limit=limit,
        )

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        """Load one persisted review artifact for shared query/recovery."""
        self.initialize()
        return self._storage.get_artifact(artifact_id)

    def get_stats(self) -> dict[str, Any]:
        """Summarize review service state for orchestrator status/reporting."""
        self.initialize()
        stats = self._storage.get_stats()
        return {
            "initialized": self._initialized,
            "approval_queue_configured": self._approval_queue is not None,
            **stats,
        }

    def _assemble_delivery_bundle(
        self,
        *,
        job: ReviewJob,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        md_report = ""
        json_report: dict[str, Any] = {}
        trace_data: list[dict[str, Any]] = []
        findings_data: list[dict[str, Any]] = []

        for artifact in artifacts:
            artifact_type = artifact.get("artifact_type", "")
            if artifact_type == ArtifactType.REVIEW_REPORT.value and artifact.get("content"):
                md_report = artifact["content"]
            if artifact_type == ArtifactType.REVIEW_REPORT.value and artifact.get("content_json"):
                json_report = artifact["content_json"]
            if artifact_type == ArtifactType.EXECUTION_TRACE.value and artifact.get("content_json"):
                trace_data = artifact["content_json"].get("trace", [])
            if artifact_type == ArtifactType.FINDING_LIST.value and artifact.get("content_json"):
                findings_data = artifact["content_json"].get("findings", [])

        package = DeliveryPackage(
            bundle_id=self._delivery_bundle_id(job.id),
            job_id=job.id,
            job_kind=job.job_kind,
            package_type="review_delivery",
            title=f"Review delivery package for {job.id[:8]}",
            status=job.status.value,
            requester=job.requester,
            workspace_id=job.workspace_id,
            artifact_ids=[artifact.get("id", "") for artifact in artifacts],
            artifact_count=len(artifacts),
            delivery_ready=False,
            created_at=job.created_at,
            completed_at=job.completed_at,
            summary={
                "review_type": job.job_type.value,
                "verdict": job.report.verdict,
                "verdict_confidence": job.report.verdict_confidence.value,
                "finding_counts": job.report.finding_counts,
                "client_safe_available": True,
            },
            payload={
                "report_artifact_ids": [
                    artifact.get("id", "")
                    for artifact in artifacts
                    if artifact.get("artifact_type") == ArtifactType.REVIEW_REPORT.value
                ],
                "finding_artifact_ids": [
                    artifact.get("id", "")
                    for artifact in artifacts
                    if artifact.get("artifact_type") == ArtifactType.FINDING_LIST.value
                ],
                "trace_artifact_ids": [
                    artifact.get("id", "")
                    for artifact in artifacts
                    if artifact.get("artifact_type") == ArtifactType.EXECUTION_TRACE.value
                ],
            },
        )
        bundle = package.to_dict()
        bundle.update(
            {
                "job_type": job.job_type.value,
                "execution_mode": job.execution_mode.value,
                "verdict": job.report.verdict,
                "verdict_confidence": job.report.verdict_confidence.value,
                "finding_counts": job.report.finding_counts,
                "markdown_report": md_report,
                "json_report": json_report,
                "findings_only": findings_data or [finding.to_dict() for finding in job.report.findings],
                "execution_trace": trace_data or [trace.to_dict() for trace in job.execution_trace],
                "error": job.error,
            }
        )
        return bundle

    def _delivery_bundle_id(self, job_id: str) -> str:
        return f"review-delivery-{job_id}"

    def _delivery_package_from_bundle(self, bundle: dict[str, Any]) -> DeliveryPackage:
        return DeliveryPackage(
            bundle_id=bundle["bundle_id"],
            job_id=bundle["job_id"],
            job_kind=JobKind.REVIEW,
            package_type=bundle.get("package_type", "review_delivery"),
            title=bundle.get("title", ""),
            status=bundle.get("status", ""),
            requester=bundle.get("requester", ""),
            workspace_id=bundle.get("workspace_id", ""),
            artifact_ids=list(bundle.get("artifact_ids", [])),
            artifact_count=int(bundle.get("artifact_count", 0)),
            delivery_ready=bool(bundle.get("delivery_ready", False)),
            created_at=bundle.get("created_at", ""),
            completed_at=bundle.get("completed_at", ""),
            summary=dict(bundle.get("summary", {})),
            payload=dict(bundle.get("payload", {})),
        )

    def _sync_delivery_record(
        self,
        *,
        bundle: DeliveryPackage,
        status: DeliveryLifecycleStatus,
        event_type: str,
        detail: str,
        approval_request_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_delivery_bundle(
            bundle=bundle,
            status=status,
            event_type=event_type,
            detail=detail,
            approval_request_id=approval_request_id,
            metadata=metadata or {},
        )

    def _refresh_delivery_record(self, bundle_id: str):
        if self._control_plane_state is None:
            return None
        return self._control_plane_state.refresh_delivery_status(
            bundle_id,
            approval_lookup=(
                self._approval_queue.get_request
                if self._approval_queue is not None
                else None
            ),
        )
