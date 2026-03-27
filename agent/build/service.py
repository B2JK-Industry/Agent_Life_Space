"""
Agent Life Space — Build Service

Channel-independent build workflow orchestrator.
Workspace-first, acceptance-driven, verification-gated.

Flow:
    1. intake -> validate
    2. create workspace
    3. build (execute implementation)
    4. verify (test, lint, typecheck)
    5. evaluate acceptance criteria
    6. produce artifacts (patch, diff, verification, acceptance report)
    7. persist and return result

This service does NOT:
    - format for Telegram
    - call LLM (foundation-grade, deterministic in v1)
    - handle authentication
    - manage channels
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import structlog

from agent.build.capabilities import get_capability, list_capabilities
from agent.build.models import (
    AcceptanceCriterion,
    ArtifactKind,
    BuildArtifact,
    BuildCheckpointPhase,
    BuildIntake,
    BuildJob,
    BuildPhase,
    VerificationKind,
)
from agent.build.storage import BuildStorage
from agent.build.verification import run_verification_suite
from agent.control.models import ExecutionMode, JobStatus

logger = structlog.get_logger(__name__)


class BuildService:
    """Orchestrates build jobs end-to-end.

    Channel-independent — Telegram/API/CLI are just adapters.
    Workspace-first — all mutable work happens in a workspace.
    """

    def __init__(
        self,
        storage: BuildStorage | None = None,
        workspace_manager: Any = None,
        review_service: Any = None,
    ) -> None:
        self._storage = storage or BuildStorage()
        self._workspace_manager = workspace_manager
        self._review_service = review_service
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        self._storage.initialize()
        self._initialized = True

    async def run_build(self, intake: BuildIntake) -> BuildJob:
        """Run a complete build job. Returns the finished job."""
        self.initialize()

        job = BuildJob(
            build_type=intake.build_type,
            requester=intake.requester,
            intake=intake,
        )

        # ── Step 1: Validate ──
        t_validate = job.trace("validate")
        errors = intake.validate()
        if errors:
            t_validate.fail("; ".join(errors))
            job.status = JobStatus.FAILED
            job.error = f"Validation failed: {'; '.join(errors)}"
            self._storage.save_job(job)
            return job
        try:
            capability = self._resolve_capability(intake)
        except ValueError as e:
            t_validate.fail(str(e))
            job.status = JobStatus.FAILED
            job.error = str(e)
            self._storage.save_job(job)
            return job
        job.capability_id = capability.id
        t_validate.complete(f"input valid: {intake.build_type.value}")
        job.status = JobStatus.VALIDATING
        job.record_checkpoint(
            BuildCheckpointPhase.VALIDATED,
            detail=f"capability={capability.id}",
        )

        # ── Step 2: Workspace setup ──
        t_ws = job.trace("workspace")
        job.phase = BuildPhase.WORKSPACE_SETUP

        if self._workspace_manager is None:
            t_ws.fail("No workspace manager — build requires workspace")
            job.status = JobStatus.FAILED
            job.error = "Build jobs require a workspace manager."
            self._storage.save_job(job)
            return job

        try:
            ws = self._workspace_manager.create(
                name=f"build-{job.id[:8]}",
                task_id=job.id,
            )
            self._workspace_manager.activate(ws.id)
            job.workspace_id = ws.id
            job.execution_mode = ExecutionMode.WORKSPACE_BOUND
            t_ws.complete(f"workspace {ws.id} at {ws.path}")
            self._record_checkpoint(
                job,
                BuildCheckpointPhase.WORKSPACE_READY,
                detail=f"workspace={ws.id}",
            )
        except Exception as e:
            t_ws.fail(str(e))
            job.status = JobStatus.FAILED
            job.error = f"Workspace setup failed: {e}"
            self._storage.save_job(job)
            return job

        workspace_path = ws.path

        t_sync = job.trace("repo_sync")
        try:
            self._sync_repo_into_workspace(
                job=job,
                repo_path=intake.repo_path,
                workspace_path=workspace_path,
            )
            t_sync.complete(f"repo synced: {intake.repo_path} -> {workspace_path}")
            self._record_checkpoint(
                job,
                BuildCheckpointPhase.REPO_SYNCED,
                detail=f"workspace={workspace_path}",
            )
        except Exception as e:
            t_sync.fail(str(e))
            job.status = JobStatus.FAILED
            job.error = f"Workspace sync failed: {e}"
            self._finalize(job, workspace_path)
            return job

        job.timing.mark_started()
        return await self._continue_from_workspace(
            job=job,
            workspace_path=workspace_path,
            can_skip_completed_steps=False,
        )

    async def resume_build(self, job_id: str) -> BuildJob | None:
        """Resume a previously interrupted build from its latest checkpoint."""
        self.initialize()
        previous = self.load_job(job_id)
        if previous is None:
            return None
        if previous.status == JobStatus.COMPLETED:
            return previous

        job = BuildJob(
            build_type=previous.build_type,
            capability_id=previous.capability_id,
            requester=previous.requester,
            intake=previous.intake,
            source="resume",
            resumed_from_job_id=previous.id,
            resume_count=previous.resume_count + 1,
            verification_results=list(previous.verification_results),
            acceptance=previous.acceptance,
            checkpoints=list(previous.checkpoints),
        )
        job.trace("resume").complete(
            f"resumed_from={previous.id}; last_checkpoint="
            f"{previous.last_checkpoint.phase.value if previous.last_checkpoint else 'none'}"
        )

        workspace_path = ""
        reusable_workspace = False
        if self._workspace_manager is not None and previous.workspace_id:
            ws = self._workspace_manager.get(previous.workspace_id)
            if ws is not None and Path(ws.path).is_dir():
                self._workspace_manager.activate(ws.id)
                job.workspace_id = ws.id
                job.execution_mode = ExecutionMode.WORKSPACE_BOUND
                workspace_path = ws.path
                reusable_workspace = True

        if not reusable_workspace:
            if self._workspace_manager is None:
                job.status = JobStatus.FAILED
                job.error = "Build jobs require a workspace manager."
                self._storage.save_job(job)
                return job
            ws = self._workspace_manager.create(
                name=f"build-{job.id[:8]}",
                task_id=job.id,
            )
            self._workspace_manager.activate(ws.id)
            job.workspace_id = ws.id
            job.execution_mode = ExecutionMode.WORKSPACE_BOUND
            workspace_path = ws.path
            self._sync_repo_into_workspace(
                job=job,
                repo_path=job.intake.repo_path,
                workspace_path=workspace_path,
            )
            self._record_checkpoint(
                job,
                BuildCheckpointPhase.WORKSPACE_READY,
                detail=f"workspace={ws.id}",
            )
            self._record_checkpoint(
                job,
                BuildCheckpointPhase.REPO_SYNCED,
                detail=f"workspace={workspace_path}",
            )

        job.timing.mark_started()
        return await self._continue_from_workspace(
            job=job,
            workspace_path=workspace_path,
            can_skip_completed_steps=reusable_workspace,
        )

    async def _continue_from_workspace(
        self,
        *,
        job: BuildJob,
        workspace_path: str,
        can_skip_completed_steps: bool,
    ) -> BuildJob:
        # ── Step 3: Build (execute implementation) ──
        job.status = JobStatus.RUNNING
        job.phase = BuildPhase.BUILDING

        if can_skip_completed_steps and self._can_reuse_checkpoint(
            job, BuildCheckpointPhase.BUILT
        ):
            job.trace("resume:build").complete("build checkpoint reused")
        else:
            t_build = job.trace("build")
            build_ok = self._execute_build(job, workspace_path)
            if build_ok:
                t_build.complete("build step completed")
                self._record_checkpoint(
                    job,
                    BuildCheckpointPhase.BUILT,
                    detail=f"capability={job.capability_id}",
                )
            else:
                t_build.fail(job.error or "build failed")
                job.status = JobStatus.FAILED
                self._finalize(job, workspace_path)
                return job

        # ── Step 4: Verify ──
        job.status = JobStatus.VERIFYING
        if can_skip_completed_steps and self._can_reuse_checkpoint(
            job, BuildCheckpointPhase.VERIFIED
        ):
            results = job.verification_results
            passed = all(r.passed for r in results)
            job.trace("resume:verify").complete("verification checkpoint reused")
        else:
            t_verify = job.trace("verify")

            verification_steps = self._get_verification_steps(
                workspace_path=workspace_path,
                capability_id=job.capability_id,
            )
            results = run_verification_suite(
                workspace_path=workspace_path,
                steps=verification_steps,
                timeout_seconds=120,
            )
            job.verification_results = results

            passed = all(r.passed for r in results)
            failed_kinds = [r.kind.value for r in results if not r.passed]
            if passed:
                t_verify.complete(f"{len(results)} checks passed")
            else:
                t_verify.fail(f"failed: {', '.join(failed_kinds)}")
            self._record_checkpoint(
                job,
                BuildCheckpointPhase.VERIFIED,
                detail=f"passed={passed}",
            )

        # ── Step 5: Create verification artifact ──
        verification_artifact = BuildArtifact(
            artifact_kind=ArtifactKind.VERIFICATION_REPORT,
            job_id=job.id,
            content_json={
                "results": [v.to_dict() for v in results],
                "all_passed": passed,
            },
            format="json",
        )
        job.artifacts.append(verification_artifact)
        self._storage.save_artifact(verification_artifact)

        # ── Step 6: Evaluate acceptance criteria ──
        if can_skip_completed_steps and self._can_reuse_checkpoint(
            job, BuildCheckpointPhase.ACCEPTANCE_EVALUATED
        ):
            job.trace("resume:acceptance").complete("acceptance checkpoint reused")
        else:
            t_accept = job.trace("acceptance")
            job.acceptance.criteria = [
                AcceptanceCriterion.from_dict(item.to_dict())
                for item in job.intake.acceptance_criteria
            ]

            if not passed:
                # Verification failed — auto-fail acceptance
                for c in job.acceptance.criteria:
                    if c.status.value == "pending":
                        c.fail("Verification did not pass")
            else:
                # Evaluate each criterion
                self._evaluate_acceptance(job, workspace_path)

            job.acceptance.evaluate()

            if job.acceptance.accepted:
                t_accept.complete(job.acceptance.summary)
            else:
                t_accept.fail(job.acceptance.summary)
            self._record_checkpoint(
                job,
                BuildCheckpointPhase.ACCEPTANCE_EVALUATED,
                detail=f"accepted={job.acceptance.accepted}",
            )

        # Acceptance report artifact
        acceptance_artifact = BuildArtifact(
            artifact_kind=ArtifactKind.ACCEPTANCE_REPORT,
            job_id=job.id,
            content_json=job.acceptance.to_dict(),
            format="json",
        )
        job.artifacts.append(acceptance_artifact)
        self._storage.save_artifact(acceptance_artifact)

        # ── Step 7: Deterministic post-build review (optional) ──
        if job.acceptance.accepted and job.intake.run_post_build_review:
            t_review = job.trace("post_build_review")
            review_job = await self._run_post_build_review(job, workspace_path)
            if review_job is None:
                t_review.fail(job.error or "post-build review unavailable")
                self._finalize(job, workspace_path)
                return job

            job.post_build_review_job_id = review_job.id
            job.post_build_review_verdict = review_job.report.verdict
            job.post_build_review_findings = review_job.report.finding_counts

            review_artifact = BuildArtifact(
                artifact_kind=ArtifactKind.REVIEW_REPORT,
                job_id=job.id,
                content_json={
                    "review_job_id": review_job.id,
                    "verdict": review_job.report.verdict,
                    "finding_counts": review_job.report.finding_counts,
                    "executive_summary": review_job.report.executive_summary,
                    "blocking": bool(
                        job.intake.block_on_review_failure
                        and review_job.report.verdict == "fail"
                    ),
                },
                format="json",
            )
            job.artifacts.append(review_artifact)
            self._storage.save_artifact(review_artifact)

            if review_job.report.findings:
                findings_artifact = BuildArtifact(
                    artifact_kind=ArtifactKind.FINDING_LIST,
                    job_id=job.id,
                    content_json={
                        "review_job_id": review_job.id,
                        "findings": [finding.to_dict() for finding in review_job.report.findings],
                    },
                    format="json",
                )
                job.artifacts.append(findings_artifact)
                self._storage.save_artifact(findings_artifact)

            if (
                job.intake.block_on_review_failure
                and review_job.report.verdict == "fail"
            ):
                job.status = JobStatus.BLOCKED
                job.error = (
                    "Post-build review blocked completion: "
                    f"{review_job.report.finding_counts.get('critical', 0)} critical findings"
                )
                t_review.fail(job.error)
            else:
                t_review.complete(
                    f"review verdict={review_job.report.verdict}"
                )
            self._record_checkpoint(
                job,
                BuildCheckpointPhase.REVIEWED,
                detail=f"verdict={job.post_build_review_verdict or 'skipped'}",
            )

        # ── Step 8: Produce diff/patch artifact ──
        t_artifacts = job.trace("artifacts")
        self._capture_diff_artifact(job, workspace_path)
        t_artifacts.complete(f"{len(job.artifacts)} artifacts created")
        self._record_checkpoint(
            job,
            BuildCheckpointPhase.ARTIFACTS_CAPTURED,
            detail=f"artifacts={len(job.artifacts)}",
        )

        # ── Step 9: Execution trace artifact ──
        trace_artifact = BuildArtifact(
            artifact_kind=ArtifactKind.EXECUTION_TRACE,
            job_id=job.id,
            content_json={"trace": [t.to_dict() for t in job.execution_trace]},
            format="json",
        )
        job.artifacts.append(trace_artifact)
        self._storage.save_artifact(trace_artifact)

        # ── Step 10: Complete ──
        self._finalize(job, workspace_path)
        return job

    def _execute_build(self, job: BuildJob, workspace_path: str) -> bool:
        """Execute the build step.

        In v1 foundation, this remains a placeholder. The repo has
        already been materialized into the workspace, so this step
        only validates that the workspace is writable and leaves an
        audit marker.
        """
        wp = Path(workspace_path)
        if not wp.is_dir():
            job.error = f"Workspace path does not exist: {workspace_path}"
            return False

        # v1: create a marker file to show workspace was used
        marker = wp / ".build_job"
        marker.write_text(f"job_id={job.id}\nbuild_type={job.build_type.value}\n")
        self._workspace_manager.record_file(job.workspace_id, str(marker))
        return True

    def _get_verification_steps(
        self,
        workspace_path: str,
        capability_id: str = "",
    ) -> list[VerificationKind]:
        """Determine which verification steps to run."""
        steps = [VerificationKind.TEST, VerificationKind.LINT]
        if capability_id:
            capability = next(
                (item for item in list_capabilities() if item.id == capability_id),
                None,
            )
            if capability is not None:
                steps = [step for step in capability.verification_defaults if step != VerificationKind.TYPECHECK]
                if VerificationKind.TYPECHECK not in steps:
                    steps.append(VerificationKind.TYPECHECK)
        wp = Path(workspace_path)
        typecheck_markers = (
            "pyproject.toml",
            "mypy.ini",
            ".mypy.ini",
            "setup.cfg",
        )
        if any((wp / marker).exists() for marker in typecheck_markers):
            if VerificationKind.TYPECHECK not in steps:
                steps.append(VerificationKind.TYPECHECK)
        else:
            steps = [step for step in steps if step != VerificationKind.TYPECHECK]
        deduped: list[VerificationKind] = []
        for step in steps:
            if step not in deduped:
                deduped.append(step)
        return deduped

    def _evaluate_acceptance(
        self, job: BuildJob, workspace_path: str
    ) -> None:
        """Evaluate acceptance criteria against build result.

        Supported semantics:
            - 'verify: <command>' executes a deterministic command
              inside the workspace without a shell.
            - text mentioning tests/lint/typecheck is bound to the
              matching verification result.
            - text mentioning build/workspace is bound to the
              placeholder build step completing.
            - any other criterion fails closed instead of being
              auto-marked met.
        """
        for criterion in job.acceptance.criteria:
            if criterion.status.value != "pending":
                continue
            description = criterion.description.strip()
            normalized = description.lower()

            if normalized.startswith("verify:"):
                self._evaluate_verify_command(
                    job=job,
                    criterion=criterion,
                    workspace_path=workspace_path,
                )
                continue

            if "typecheck" in normalized or "type check" in normalized or "mypy" in normalized:
                self._evaluate_verification_backed_criterion(
                    criterion=criterion,
                    result=self._find_verification_result(job, VerificationKind.TYPECHECK),
                    label="Typecheck",
                )
                continue

            if "lint" in normalized or "ruff" in normalized:
                self._evaluate_verification_backed_criterion(
                    criterion=criterion,
                    result=self._find_verification_result(job, VerificationKind.LINT),
                    label="Lint",
                )
                continue

            if "test" in normalized or "pytest" in normalized:
                self._evaluate_verification_backed_criterion(
                    criterion=criterion,
                    result=self._find_verification_result(job, VerificationKind.TEST),
                    label="Tests",
                )
                continue

            if "build" in normalized or "workspace" in normalized:
                criterion.meet("Workspace synchronized and build step completed")
                continue

            criterion.fail(f"No evaluator available for criterion: {description}")

    def _sync_repo_into_workspace(
        self,
        job: BuildJob,
        repo_path: str,
        workspace_path: str,
    ) -> None:
        """Materialize the requested repo into the managed workspace."""
        source = Path(repo_path).resolve()
        workspace = Path(workspace_path).resolve()

        if not source.exists():
            raise FileNotFoundError(f"repo_path does not exist: {repo_path}")
        if not source.is_dir():
            raise NotADirectoryError(f"repo_path must be a directory: {repo_path}")
        if source == workspace:
            return

        shutil.copytree(
            source,
            workspace,
            dirs_exist_ok=True,
            ignore=self._build_workspace_copy_ignore(workspace),
        )

        if self._workspace_manager is not None and job.workspace_id:
            self._workspace_manager.record_command(
                job.workspace_id,
                f"sync_repo {source} -> {workspace}",
            )

    def _build_workspace_copy_ignore(self, workspace: Path):
        skip_names = {
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "venv",
        }

        def _ignore(directory: str, names: list[str]) -> set[str]:
            ignored: set[str] = set()
            for name in names:
                if name in skip_names:
                    ignored.add(name)
                    continue
                candidate = (Path(directory) / name).resolve()
                if workspace.is_relative_to(candidate):
                    ignored.add(name)
            return ignored

        return _ignore

    def _find_verification_result(
        self,
        job: BuildJob,
        kind: VerificationKind,
    ):
        return next((result for result in job.verification_results if result.kind == kind), None)

    def _evaluate_verification_backed_criterion(
        self,
        criterion,
        result,
        label: str,
    ) -> None:
        if result is None:
            criterion.fail(f"{label} verification was not run")
            return
        evidence = self._format_verification_evidence(label, result)
        if result.passed:
            criterion.meet(evidence)
        else:
            criterion.fail(evidence)

    def _evaluate_verify_command(
        self,
        job: BuildJob,
        criterion,
        workspace_path: str,
    ) -> None:
        command_text = criterion.description.split(":", 1)[1].strip()
        if not command_text:
            criterion.fail("verify: requires a command")
            return

        try:
            command = shlex.split(command_text)
        except ValueError as e:
            criterion.fail(f"Invalid verify command: {e}")
            return

        if self._workspace_manager is not None and job.workspace_id:
            self._workspace_manager.record_command(
                job.workspace_id,
                f"acceptance:{command_text}",
            )

        try:
            result = subprocess.run(  # noqa: S603
                command,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            criterion.fail(f"verify command timed out after 60s: {command_text}")
            return
        except Exception as e:
            criterion.fail(f"verify command failed to start: {e}")
            return

        evidence = self._format_command_evidence(command_text, result)
        if result.returncode == 0:
            criterion.meet(evidence)
        else:
            criterion.fail(evidence)

    def _format_verification_evidence(
        self,
        label: str,
        result,
    ) -> str:
        summary = (
            f"{label}: {'passed' if result.passed else 'failed'}; "
            f"command={result.command}; exit={result.exit_code}"
        )
        output = (result.stderr or result.stdout).strip()
        if output:
            compact = " ".join(output.split())[:200]
            return f"{summary}; output={compact}"
        return summary

    def _format_command_evidence(self, command_text: str, result) -> str:
        summary = f"verify command={command_text}; exit={result.returncode}"
        output = (result.stderr or result.stdout).strip()
        if output:
            compact = " ".join(output.split())[:200]
            return f"{summary}; output={compact}"
        return summary

    def _capture_diff_artifact(
        self, job: BuildJob, workspace_path: str
    ) -> None:
        """Capture a diff artifact showing what changed in workspace."""
        wp = Path(workspace_path)
        try:
            result = subprocess.run(  # noqa: S603, S607
                ["git", "diff", "--stat"],
                cwd=str(wp),
                capture_output=True,
                text=True,
                timeout=30,
            )
            diff_content = result.stdout or "(no diff)"

            full_diff = subprocess.run(  # noqa: S603, S607
                ["git", "diff"],
                cwd=str(wp),
                capture_output=True,
                text=True,
                timeout=30,
            )

            diff_artifact = BuildArtifact(
                artifact_kind=ArtifactKind.DIFF,
                job_id=job.id,
                content=full_diff.stdout[:50000] if full_diff.stdout else "(no diff)",
                content_json={
                    "stat": diff_content,
                    "files_changed": diff_content.count("|"),
                },
                format="text",
            )
            job.artifacts.append(diff_artifact)
            self._storage.save_artifact(diff_artifact)
        except Exception:
            # Git not initialized or not available — skip diff
            pass

    async def _run_post_build_review(
        self,
        job: BuildJob,
        workspace_path: str,
    ):
        """Run deterministic review over the built workspace."""
        if self._review_service is None:
            job.status = JobStatus.FAILED
            job.error = (
                "Post-build review requested but no ReviewService is configured."
            )
            return None

        from agent.review.models import ReviewIntake, ReviewJobType

        review_intake = ReviewIntake(
            repo_path=workspace_path,
            review_type=ReviewJobType.REPO_AUDIT,
            include_patterns=job.intake.target_files,
            requester=job.requester,
            context=f"Post-build review for build {job.id}: {job.intake.description}",
        )
        return await self._review_service.run_review(review_intake)

    def _resolve_capability(self, intake: BuildIntake):
        capability = get_capability(intake.build_type)
        if intake.capability_id and intake.capability_id != capability.id:
            msg = (
                f"Unsupported capability '{intake.capability_id}' for "
                f"build_type '{intake.build_type.value}'"
            )
            raise ValueError(msg)
        return capability

    def _record_checkpoint(
        self,
        job: BuildJob,
        phase: BuildCheckpointPhase,
        detail: str,
    ) -> None:
        job.checkpoints = [
            checkpoint
            for checkpoint in job.checkpoints
            if checkpoint.phase != phase
        ]
        job.record_checkpoint(phase, detail=detail)

    def _can_reuse_checkpoint(
        self,
        job: BuildJob,
        phase: BuildCheckpointPhase,
    ) -> bool:
        if not job.has_checkpoint(phase):
            return False
        if phase == BuildCheckpointPhase.BUILT:
            return True
        if phase == BuildCheckpointPhase.VERIFIED:
            return job.verification_passed
        if phase == BuildCheckpointPhase.ACCEPTANCE_EVALUATED:
            return job.acceptance.accepted
        if phase == BuildCheckpointPhase.REVIEWED:
            if not job.intake.run_post_build_review:
                return True
            return bool(job.post_build_review_verdict) and not (
                job.intake.block_on_review_failure
                and job.post_build_review_verdict == "fail"
            )
        if phase == BuildCheckpointPhase.COMPLETED:
            return job.status == JobStatus.COMPLETED
        return True

    def _finalize(self, job: BuildJob, workspace_path: str) -> None:
        """Finalize job — set status, persist, complete workspace."""
        if job.status not in {JobStatus.FAILED, JobStatus.BLOCKED}:
            if job.acceptance.accepted:
                job.status = JobStatus.COMPLETED
            else:
                job.status = JobStatus.FAILED
                if not job.error:
                    job.error = (
                        f"Acceptance criteria not met: "
                        f"{job.acceptance.unmet_count} unmet"
                    )

        job.timing.mark_completed()
        self._record_checkpoint(
            job,
            BuildCheckpointPhase.COMPLETED,
            detail=f"status={job.status.value}",
        )
        self._storage.save_job(job)

        # Complete or fail workspace
        if self._workspace_manager and job.workspace_id:
            if job.status == JobStatus.COMPLETED:
                self._workspace_manager.complete(
                    job.workspace_id, output=f"Build {job.id} completed"
                )
            else:
                self._workspace_manager.fail(
                    job.workspace_id, error=job.error or "build failed"
                )

        logger.info(
            "build_completed",
            job_id=job.id,
            status=job.status.value,
            verification_passed=job.verification_passed,
            acceptance=job.acceptance.accepted,
            review_verdict=job.post_build_review_verdict,
            artifacts=len(job.artifacts),
        )

    # ── Query methods ──

    def load_job(self, job_id: str) -> BuildJob | None:
        """Reconstruct a full BuildJob from storage. Recovery-safe."""
        self.initialize()
        data = self._storage.load_job(job_id)
        if data is None:
            return None
        return BuildJob.from_dict(data)

    def list_jobs(
        self, status: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        """List build jobs."""
        self.initialize()
        return self._storage.list_jobs(status=status, limit=limit)

    def get_stats(self) -> dict[str, Any]:
        """Summarize build service state for orchestrator status/reporting."""
        self.initialize()
        stats = self._storage.get_stats()
        return {
            "initialized": self._initialized,
            "capabilities": [capability.id for capability in list_capabilities()],
            **stats,
        }
