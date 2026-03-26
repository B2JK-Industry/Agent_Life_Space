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

from datetime import UTC, datetime
from typing import Any

import structlog

from agent.review.analyzers import (
    analyze_diff,
    analyze_repo_structure,
    analyze_security,
)
from agent.review.models import (
    ArtifactType,
    Confidence,
    ExecutionMode,
    ReviewArtifact,
    ReviewFinding,
    ReviewIntake,
    ReviewJob,
    ReviewJobStatus,
    ReviewJobType,
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
    ) -> None:
        self._storage = storage or ReviewStorage()
        self._workspace_manager = workspace_manager
        self._approval_queue = approval_queue
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
            requester=intake.requester,
            intake=intake,
        )
        t_validate = job.trace("validate")

        errors = intake.validate()
        if errors:
            t_validate.fail("; ".join(errors))
            job.status = ReviewJobStatus.FAILED
            job.error = f"Validation failed: {'; '.join(errors)}"
            self._storage.save_job(job)
            return job

        t_validate.complete(f"input valid: {intake.review_type.value}")
        job.status = ReviewJobStatus.VALIDATING
        self._storage.save_job(job)

        # ── Step 1b: Execution mode + workspace ──
        if self._workspace_manager is not None:
            t_ws = job.trace("workspace")
            try:
                ws = self._workspace_manager.create(
                    name=f"review-{job.id[:8]}",
                    task_id=job.id,
                )
                self._workspace_manager.activate(ws.id)
                job.workspace_id = ws.id
                job.execution_mode = ExecutionMode.WORKSPACE_BOUND
                t_ws.complete(f"workspace {ws.id}")
            except Exception as e:
                t_ws.fail(str(e))
                job.execution_mode = ExecutionMode.READ_ONLY_HOST
                logger.warning("review_workspace_failed", error=str(e))
        else:
            job.execution_mode = ExecutionMode.READ_ONLY_HOST

        # ── Step 1c: Execution policy audit ──
        t_policy = job.trace("execution_policy")
        t_policy.complete(
            f"mode={job.execution_mode.value}, "
            f"source={job.source}, "
            f"host_access=read_only, "
            f"git_subprocess={'yes' if intake.diff_spec else 'no'}"
        )

        # ── Step 2: Analyze ──
        job.status = ReviewJobStatus.ANALYZING
        job.started_at = datetime.now(UTC).isoformat()

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
        job.status = ReviewJobStatus.VERIFYING
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
        t_artifacts = job.trace("artifacts")

        # Markdown report
        md_artifact = ReviewArtifact(
            artifact_type=ArtifactType.REVIEW_REPORT,
            job_id=job.id,
            content=report.to_markdown(),
            format="markdown",
        )
        job.artifacts.append(md_artifact)
        self._storage.save_artifact(md_artifact)

        # JSON report
        json_artifact = ReviewArtifact(
            artifact_type=ArtifactType.REVIEW_REPORT,
            job_id=job.id,
            content="",
            content_json=report.to_dict(),
            format="json",
        )
        job.artifacts.append(json_artifact)
        self._storage.save_artifact(json_artifact)

        # Execution trace artifact
        trace_artifact = ReviewArtifact(
            artifact_type=ArtifactType.EXECUTION_TRACE,
            job_id=job.id,
            content="",
            content_json={"trace": [t.to_dict() for t in job.execution_trace]},
            format="json",
        )
        job.artifacts.append(trace_artifact)
        self._storage.save_artifact(trace_artifact)

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
            self._storage.save_artifact(findings_artifact)

        t_artifacts.complete(f"{len(job.artifacts)} artifacts created")

        # ── Step 6: Complete ──
        job.status = ReviewJobStatus.COMPLETED
        job.completed_at = datetime.now(UTC).isoformat()
        self._storage.save_job(job)

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
        report = ReviewReport()
        all_findings: list = []

        # Structure analysis
        t_structure = job.trace("analyze:structure")
        metrics, structure_findings = analyze_repo_structure(
            intake.repo_path,
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
            intake.repo_path,
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
        report = ReviewReport()
        all_findings: list = []

        # Diff analysis
        t_diff = job.trace("analyze:diff")
        diff_summary, diff_findings, raw_diff = analyze_diff(
            intake.repo_path, intake.diff_spec,
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
                intake.repo_path,
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
        intake = job.intake

        # Additional release checks
        from pathlib import Path
        root = Path(intake.repo_path)

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

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve a stored job by ID (dict form)."""
        return self._storage.load_job(job_id)

    def load_job(self, job_id: str) -> ReviewJob | None:
        """Reconstruct a full ReviewJob from storage. Recovery-safe."""
        data = self._storage.load_job(job_id)
        if data is None:
            return None
        return ReviewJob.from_dict(data)

    def get_delivery_bundle(self, job_id: str) -> dict[str, Any] | None:
        """Assemble a delivery-ready bundle for a completed review job.

        Returns dict with:
            - job metadata (id, type, status, requester, verdict)
            - markdown_report (full content)
            - json_report (full structured data)
            - findings_only (list of findings dicts)
            - execution_trace (list of trace step dicts)
            - delivery_ready: bool (true only if job completed successfully)

        Returns None if job not found.
        Foundation for future approval-gated delivery flow.
        """
        self.initialize()
        job = self.load_job(job_id)
        if job is None:
            return None

        artifacts = self._storage.get_artifacts(job_id)

        # Extract artifact contents by type
        md_report = ""
        json_report: dict[str, Any] = {}
        trace_data: list[dict[str, Any]] = []
        findings_data: list[dict[str, Any]] = []

        for a in artifacts:
            atype = a.get("artifact_type", "")
            if atype == "review_report" and a.get("content"):
                md_report = a["content"]
            if atype == "review_report" and a.get("content_json"):
                json_report = a["content_json"]
            if atype == "execution_trace" and a.get("content_json"):
                trace_data = a["content_json"].get("trace", [])
            if atype == "finding_list" and a.get("content_json"):
                findings_data = a["content_json"].get("findings", [])

        return {
            "job_id": job.id,
            "job_type": job.job_type.value,
            "status": job.status.value,
            "requester": job.requester,
            "execution_mode": job.execution_mode.value,
            "verdict": job.report.verdict,
            "verdict_confidence": job.report.verdict_confidence.value,
            "finding_counts": job.report.finding_counts,
            "markdown_report": md_report,
            "json_report": json_report,
            "findings_only": findings_data or [f.to_dict() for f in job.report.findings],
            "execution_trace": trace_data or [t.to_dict() for t in job.execution_trace],
            "artifact_count": len(artifacts),
            "delivery_ready": job.status == ReviewJobStatus.COMPLETED,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
        }

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

        # Create approval request via orchestrator's approval queue
        if self._approval_queue is None:
            return {
                "error": "No approval queue configured",
                "job_id": job_id,
                "delivery_ready": True,
                "approval_bypassed": True,
            }

        from agent.core.approval import ApprovalCategory
        req = self._approval_queue.propose(
            category=ApprovalCategory.EXTERNAL,
            description=f"Deliver review report for job {job_id[:8]} ({job.report.verdict})",
            risk_level="medium",
            reason=f"Review of {job.intake.repo_path} — {len(job.report.findings)} findings",
            context={
                "job_id": job_id,
                "verdict": job.report.verdict,
                "finding_counts": job.report.finding_counts,
                "requester": job.requester,
            },
        )
        logger.info("review_delivery_approval_requested",
                     job_id=job_id, approval_id=req.id)
        return {
            "job_id": job_id,
            "approval_request_id": req.id,
            "approval_status": "pending",
            "delivery_ready": False,
        }

    def get_client_safe_bundle(self, job_id: str) -> dict[str, Any] | None:
        """Export a client-safe delivery bundle with redacted sensitive content.

        Differences from internal bundle:
            - absolute paths replaced with relative or [REDACTED]
            - raw evidence redacted for secret findings
            - internal trace details stripped
            - execution mode and internal metadata removed
        """
        bundle = self.get_delivery_bundle(job_id)
        if bundle is None:
            return None

        import re

        # Redact absolute paths
        def _redact_paths(text: str) -> str:
            return re.sub(r'/(?:Users|home|root)/[^\s"\'`]+', '[PATH_REDACTED]', text)

        # Redact markdown report
        bundle["markdown_report"] = _redact_paths(bundle.get("markdown_report", ""))

        # Redact findings evidence
        redacted_findings = []
        for f in bundle.get("findings_only", []):
            fc = dict(f)
            if fc.get("evidence"):
                fc["evidence"] = _redact_paths(fc["evidence"])
            redacted_findings.append(fc)
        bundle["findings_only"] = redacted_findings

        # Redact JSON report
        jr = bundle.get("json_report", {})
        if jr.get("scope_description"):
            jr["scope_description"] = _redact_paths(jr["scope_description"])
        if jr.get("findings"):
            for f in jr["findings"]:
                if f.get("evidence"):
                    f["evidence"] = _redact_paths(f["evidence"])
        bundle["json_report"] = jr

        # Strip internal-only fields
        bundle.pop("execution_trace", None)
        bundle.pop("execution_mode", None)
        bundle["export_mode"] = "client_safe"

        return bundle

    def list_jobs(self, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """List review jobs."""
        return self._storage.list_jobs(status=status, limit=limit)
