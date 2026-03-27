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

import difflib
import shlex
import shutil
import subprocess
from datetime import UTC, datetime
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
    CriterionKind,
    VerificationKind,
)
from agent.build.storage import BuildStorage
from agent.build.verification import DEFAULT_COMMANDS, run_verification_suite
from agent.control.models import (
    DeliveryLifecycleStatus,
    DeliveryPackage,
    ExecutionMode,
    JobStatus,
    TraceRecordKind,
)
from agent.control.policy import (
    get_delivery_policy,
    get_review_gate_policy,
    list_delivery_policies,
    list_review_gate_policies,
)

logger = structlog.get_logger(__name__)

_INTERNAL_WORKSPACE_FILES = {".build_job"}


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
        approval_queue: Any = None,
        control_plane_state: Any = None,
    ) -> None:
        self._storage = storage or BuildStorage()
        self._workspace_manager = workspace_manager
        self._review_service = review_service
        self._approval_queue = approval_queue
        self._control_plane_state = control_plane_state
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
            self._save_job(job)
            return job
        try:
            capability = self._resolve_capability(intake)
        except ValueError as e:
            t_validate.fail(str(e))
            job.status = JobStatus.FAILED
            job.error = str(e)
            self._save_job(job)
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
            self._save_job(job)
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
            self._save_job(job)
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
                self._save_job(job)
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

            verification_plan = self._discover_verification_plan(
                workspace_path=workspace_path,
                capability_id=job.capability_id,
            )
            verification_steps = verification_plan["steps"]
            verification_commands = self._resolve_verification_commands(
                repo_path=job.intake.repo_path,
                workspace_path=workspace_path,
                steps=verification_steps,
            )
            verification_plan["commands"] = {
                step.value: " ".join(verification_commands.get(step, []))
                for step in verification_steps
                if verification_commands.get(step)
            }
            self._record_control_trace(
                trace_kind=TraceRecordKind.VERIFICATION_DISCOVERY,
                title="Verification discovery",
                detail=(
                    "Discovered verification steps: "
                    + ", ".join(step.value for step in verification_steps)
                ),
                job_id=job.id,
                workspace_id=job.workspace_id,
                metadata=verification_plan,
            )
            results = run_verification_suite(
                workspace_path=workspace_path,
                steps=verification_steps,
                custom_commands=verification_commands,
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
        self._save_artifact(job, verification_artifact)

        change_set = self._collect_workspace_changes(job, workspace_path)

        # ── Step 6: Deterministic post-build review (optional) ──
        if passed and job.intake.run_post_build_review:
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
                    "review_gate_policy_id": self._review_gate_policy(job).id,
                },
                format="json",
            )
            job.artifacts.append(review_artifact)
            self._save_artifact(job, review_artifact)

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
                self._save_artifact(job, findings_artifact)

            blocked_by_policy, block_reason = self._apply_review_gate_policy(job)
            review_artifact.content_json["blocking"] = blocked_by_policy
            review_artifact.content_json["blocking_reason"] = block_reason
            self._save_artifact(job, review_artifact)
            self._record_control_trace(
                trace_kind=TraceRecordKind.REVIEW_POLICY,
                title="Post-build review gate policy",
                detail=block_reason,
                job_id=job.id,
                workspace_id=job.workspace_id,
                metadata={
                    "policy_id": self._review_gate_policy(job).id,
                    "verdict": review_job.report.verdict,
                    "finding_counts": review_job.report.finding_counts,
                    "blocked": blocked_by_policy,
                },
            )

            if blocked_by_policy:
                job.status = JobStatus.BLOCKED
                job.error = block_reason
                t_review.fail(job.error)
            else:
                t_review.complete(
                    f"review verdict={review_job.report.verdict}; policy={self._review_gate_policy(job).id}"
                )
            self._record_checkpoint(
                job,
                BuildCheckpointPhase.REVIEWED,
                detail=f"verdict={job.post_build_review_verdict or 'skipped'}",
            )

        # ── Step 7: Evaluate acceptance criteria ──
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
                self._evaluate_acceptance(
                    job,
                    workspace_path,
                    change_set=change_set,
                )

            if not job.acceptance.criteria:
                job.acceptance.accepted = passed
                job.acceptance.evaluated_at = datetime.now(UTC).isoformat()
                job.acceptance.summary = (
                    "No explicit acceptance criteria supplied; "
                    f"verification outcome used as acceptance proxy. "
                    f"Verdict: {'accepted' if passed else 'rejected'}."
                )
            else:
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
        self._save_artifact(job, acceptance_artifact)

        # ── Step 8: Produce diff/patch artifact ──
        t_artifacts = job.trace("artifacts")
        self._capture_diff_artifact(job, change_set)
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
        self._save_artifact(job, trace_artifact)

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

    def _discover_verification_plan(
        self,
        workspace_path: str,
        capability_id: str = "",
    ) -> dict[str, Any]:
        """Determine which verification steps to run and why."""
        wp = Path(workspace_path)
        capability = next(
            (item for item in list_capabilities() if item.id == capability_id),
            None,
        )
        default_steps = (
            list(capability.verification_defaults)
            if capability is not None
            else [VerificationKind.TEST, VerificationKind.LINT]
        )
        step_details: dict[str, list[str]] = {
            VerificationKind.TEST.value: [],
            VerificationKind.LINT.value: [],
            VerificationKind.TYPECHECK.value: [],
        }

        tests_markers = [
            "tests/",
            "pytest.ini",
            "tox.ini",
            "noxfile.py",
        ]
        lint_markers = [
            "pyproject.toml",
            "ruff.toml",
            ".ruff.toml",
            ".flake8",
            "eslint.config.js",
            ".eslintrc",
        ]
        typecheck_markers = [
            "mypy.ini",
            ".mypy.ini",
            "pyrightconfig.json",
            "tsconfig.json",
            "setup.cfg",
        ]

        steps: list[VerificationKind] = []

        if VerificationKind.TEST in default_steps and self._has_test_surface(wp):
            steps.append(VerificationKind.TEST)
            step_details[VerificationKind.TEST.value] = [
                marker
                for marker in tests_markers
                if self._workspace_has_marker(wp, marker)
            ] or ["test-like files present"]

        if VerificationKind.LINT in default_steps and self._has_lint_surface(wp):
            steps.append(VerificationKind.LINT)
            step_details[VerificationKind.LINT.value] = [
                marker
                for marker in lint_markers
                if self._workspace_has_marker(wp, marker)
            ] or ["source files present"]

        if self._has_typecheck_surface(wp):
            steps.append(VerificationKind.TYPECHECK)
            step_details[VerificationKind.TYPECHECK.value] = [
                marker
                for marker in typecheck_markers
                if self._workspace_has_marker(wp, marker)
            ] or ["typed source files present"]

        deduped: list[VerificationKind] = []
        for step in steps:
            if step not in deduped:
                deduped.append(step)

        if not deduped and default_steps:
            deduped.append(default_steps[0])
            step_details[default_steps[0].value] = ["default capability fallback"]

        return {
            "steps": deduped,
            "capability_id": capability_id,
            "default_steps": [step.value for step in default_steps],
            "step_details": step_details,
        }

    def _get_verification_steps(
        self,
        workspace_path: str,
        capability_id: str = "",
    ) -> list[VerificationKind]:
        """Backward-compatible wrapper over verification discovery."""
        return self._discover_verification_plan(
            workspace_path=workspace_path,
            capability_id=capability_id,
        )["steps"]

    def _workspace_has_marker(self, workspace_path: Path, marker: str) -> bool:
        candidate = workspace_path / marker
        if marker.endswith("/"):
            return candidate.is_dir()
        return candidate.exists()

    def _resolve_verification_commands(
        self,
        *,
        repo_path: str,
        workspace_path: str,
        steps: list[VerificationKind],
    ) -> dict[VerificationKind, list[str]]:
        """Prefer repo-local toolchains when the workspace excludes virtualenvs."""
        repo_root = Path(repo_path).resolve()
        workspace_root = Path(workspace_path).resolve()
        custom: dict[VerificationKind, list[str]] = {}

        tool_roots = [
            repo_root / ".venv" / "bin",
            workspace_root / ".venv" / "bin",
            repo_root / "venv" / "bin",
            workspace_root / "venv" / "bin",
        ]
        tool_root = next((root for root in tool_roots if root.is_dir()), None)
        if tool_root is None:
            return custom

        for step in steps:
            if step == VerificationKind.TEST:
                custom[step] = self._tool_command(
                    tool_root=tool_root,
                    binary_name="pytest",
                    module_name="pytest",
                    fallback=DEFAULT_COMMANDS[step],
                    args=["tests/", "-q", "--tb=short"],
                )
            elif step == VerificationKind.LINT:
                custom[step] = self._tool_command(
                    tool_root=tool_root,
                    binary_name="ruff",
                    module_name="ruff",
                    fallback=DEFAULT_COMMANDS[step],
                    args=["check", "."],
                )
            elif step == VerificationKind.TYPECHECK:
                custom[step] = self._tool_command(
                    tool_root=tool_root,
                    binary_name="mypy",
                    module_name="mypy",
                    fallback=DEFAULT_COMMANDS[step],
                    args=[".", "--ignore-missing-imports"],
                )
        return custom

    def _tool_command(
        self,
        *,
        tool_root: Path,
        binary_name: str,
        module_name: str,
        fallback: list[str],
        args: list[str],
    ) -> list[str]:
        binary = tool_root / binary_name
        if binary.exists():
            return [str(binary), *args]
        python = tool_root / "python"
        if python.exists():
            return [str(python), "-m", module_name, *args]
        return list(fallback)

    def _has_test_surface(self, workspace_path: Path) -> bool:
        if self._workspace_has_marker(workspace_path, "tests/"):
            return True
        return self._workspace_has_glob(
            workspace_path,
            ("test_*.py", "*_test.py", "*.spec.ts", "*.test.ts", "*.test.js"),
        )

    def _has_lint_surface(self, workspace_path: Path) -> bool:
        markers = (
            "pyproject.toml",
            "ruff.toml",
            ".ruff.toml",
            ".flake8",
            "eslint.config.js",
            ".eslintrc",
        )
        if any(self._workspace_has_marker(workspace_path, marker) for marker in markers):
            return True
        return self._workspace_has_glob(
            workspace_path,
            ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx"),
        )

    def _has_typecheck_surface(self, workspace_path: Path) -> bool:
        markers = (
            "mypy.ini",
            ".mypy.ini",
            "pyrightconfig.json",
            "tsconfig.json",
        )
        if any(self._workspace_has_marker(workspace_path, marker) for marker in markers):
            return True
        if self._file_contains(
            workspace_path / "pyproject.toml",
            ("[tool.mypy]", "mypy", "pyright"),
        ):
            return True
        return self._file_contains(
            workspace_path / "setup.cfg",
            ("[mypy]", "mypy", "pyright"),
        )

    def _workspace_has_glob(
        self,
        workspace_path: Path,
        patterns: tuple[str, ...],
    ) -> bool:
        for pattern in patterns:
            if next(workspace_path.rglob(pattern), None) is not None:
                return True
        return False

    def _file_contains(self, path: Path, needles: tuple[str, ...]) -> bool:
        if not path.exists() or not path.is_file():
            return False
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return False
        normalized = content.casefold()
        return any(needle.casefold() in normalized for needle in needles)

    def _evaluate_acceptance(
        self,
        job: BuildJob,
        workspace_path: str,
        *,
        change_set: dict[str, Any],
    ) -> None:
        """Evaluate acceptance criteria against build result.

        Supported semantics:
            - 'verify: <command>' executes a deterministic command
              inside the workspace without a shell.
            - text mentioning tests/lint/typecheck is bound to the
              matching verification result.
            - text mentioning review/security/finding is bound to the
              deterministic post-build review result.
            - text mentioning docs/patch/diff/target files is bound to
              the captured workspace change set.
            - quality/security criterion kinds reuse verification/review
              signals when no explicit text rule is present.
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

            if (
                "review" in normalized
                or "security" in normalized
                or "audit" in normalized
                or "finding" in normalized
            ):
                self._evaluate_review_backed_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                )
                continue

            if (
                "patch" in normalized
                or "diff" in normalized
                or "change" in normalized
                or "target file" in normalized
                or "docs" in normalized
                or "documentation" in normalized
                or "readme" in normalized
            ):
                self._evaluate_change_backed_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                    change_set=change_set,
                )
                continue

            if "build" in normalized or "workspace" in normalized:
                criterion.meet("Workspace synchronized and build step completed")
                continue

            if criterion.kind == CriterionKind.QUALITY:
                self._evaluate_quality_backed_criterion(job=job, criterion=criterion)
                continue

            if criterion.kind == CriterionKind.SECURITY:
                self._evaluate_review_backed_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                )
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

    def _review_gate_policy(self, job: BuildJob):
        policy_id = job.intake.review_gate_policy_id or "critical_findings"
        if not job.intake.block_on_review_failure and policy_id == "critical_findings":
            policy_id = "advisory"
        return get_review_gate_policy(policy_id)

    def _apply_review_gate_policy(self, job: BuildJob) -> tuple[bool, str]:
        policy = self._review_gate_policy(job)
        counts = job.post_build_review_findings
        critical = counts.get("critical", 0)
        high = counts.get("high", 0)
        verdict = job.post_build_review_verdict or "unknown"

        if policy.advisory_only:
            return False, f"Review gate policy {policy.id} recorded findings without blocking completion"
        if policy.block_fail_verdict and verdict == "fail":
            return True, (
                "Post-build review blocked completion: "
                f"{critical} critical findings; verdict={verdict}; "
                f"high={high}; policy={policy.id}"
            )
        if critical > policy.max_critical:
            return True, (
                "Post-build review blocked completion: "
                f"{critical} critical findings exceed policy {policy.id}"
            )
        if high > policy.max_high:
            return True, (
                "Post-build review blocked completion: "
                f"{high} high findings exceed policy {policy.id}"
            )
        return False, (
            f"Review gate passed under policy {policy.id}: "
            f"verdict={verdict}; critical={critical}; high={high}"
        )

    def _save_job(self, job: BuildJob) -> None:
        self._storage.save_job(job)
        self._sync_product_job(job)

    def _save_artifact(self, job: BuildJob, artifact: BuildArtifact) -> None:
        self._storage.save_artifact(artifact)
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_retained_artifact(
            record_id=artifact.id,
            artifact_id=artifact.id,
            job_id=job.id,
            job_kind=job.job_kind,
            artifact_kind=artifact.artifact_kind,
            source_type="build_artifact",
            title=artifact.artifact_kind.value,
            artifact_format=artifact.format,
            created_at=artifact.created_at,
            content=artifact.content,
            content_json=artifact.content_json,
            metadata={
                "workspace_id": job.workspace_id,
                "build_type": job.build_type.value,
                "capability_id": job.capability_id,
            },
        )

    def _sync_product_job(self, job: BuildJob) -> None:
        if self._control_plane_state is None:
            return
        outcome = "accepted" if job.acceptance.accepted else "rejected"
        if job.post_build_review_verdict:
            outcome = f"{outcome}; review={job.post_build_review_verdict}"
        self._control_plane_state.record_product_job(
            job_id=job.id,
            job_kind=job.job_kind,
            title=job.intake.description or job.build_type.value,
            status=job.status.value,
            subkind="build_job",
            requester=job.requester,
            source=job.source,
            execution_mode=job.execution_mode.value,
            workspace_id=job.workspace_id,
            scope=", ".join(job.intake.target_files[:3]),
            outcome=outcome,
            blocked_reason=job.error if job.status == JobStatus.BLOCKED else "",
            artifact_ids=[artifact.id for artifact in job.artifacts],
            created_at=job.timing.created_at,
            completed_at=job.timing.completed_at,
            usage=job.usage,
            metadata={
                "build_type": job.build_type.value,
                "capability_id": job.capability_id,
                "phase": job.phase.value,
                "timing": job.timing.to_dict(),
                "checkpoints": [checkpoint.to_dict() for checkpoint in job.checkpoints],
                "verification_passed": job.verification_passed,
                "verification_results": [item.to_dict() for item in job.verification_results],
                "acceptance": job.acceptance.to_dict(),
                "post_build_review": {
                    "requested": job.intake.run_post_build_review,
                    "job_id": job.post_build_review_job_id,
                    "verdict": job.post_build_review_verdict,
                    "finding_counts": job.post_build_review_findings,
                    "review_gate_policy_id": job.intake.review_gate_policy_id,
                },
                "delivery_policy_id": job.intake.delivery_policy_id,
                "error": job.error,
            },
        )

    def _record_delivery_bundle_retention(
        self,
        *,
        job: BuildJob,
        bundle: dict[str, Any],
    ) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_retained_artifact(
            record_id=bundle["bundle_id"],
            bundle_id=bundle["bundle_id"],
            job_id=job.id,
            job_kind=job.job_kind,
            artifact_kind=ArtifactKind.DELIVERY_BUNDLE,
            source_type="build_delivery_bundle",
            title=bundle["title"],
            artifact_format="json",
            created_at=bundle["created_at"],
            content_json=bundle,
            metadata={
                "workspace_id": job.workspace_id,
                "artifact_ids": list(bundle.get("artifact_ids", [])),
                "delivery_ready": bundle.get("delivery_ready", False),
            },
        )

    def _record_control_trace(
        self,
        *,
        trace_kind: TraceRecordKind,
        title: str,
        detail: str,
        job_id: str = "",
        workspace_id: str = "",
        bundle_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_trace(
            trace_kind=trace_kind,
            title=title,
            detail=detail,
            job_id=job_id,
            workspace_id=workspace_id,
            bundle_id=bundle_id,
            metadata=metadata or {},
        )

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

    def _evaluate_quality_backed_criterion(self, *, job: BuildJob, criterion) -> None:
        if not job.verification_results:
            criterion.fail("Quality verification did not run")
            return
        failed = [result.kind.value for result in job.verification_results if not result.passed]
        if failed:
            criterion.fail(f"Quality gates failed: {', '.join(failed)}")
            return
        passed = ", ".join(result.kind.value for result in job.verification_results)
        criterion.meet(f"Quality gates passed: {passed}")

    def _evaluate_review_backed_criterion(
        self,
        *,
        job: BuildJob,
        criterion,
        normalized: str,
    ) -> None:
        if not job.intake.run_post_build_review:
            criterion.fail(
                "Criterion requires post-build review, but run_post_build_review is disabled"
            )
            return
        if not job.post_build_review_verdict:
            criterion.fail("Post-build review did not complete")
            return

        counts = job.post_build_review_findings
        critical = counts.get("critical", 0)
        high = counts.get("high", 0)
        total_findings = sum(counts.values())
        verdict = job.post_build_review_verdict

        if "no findings" in normalized or "zero findings" in normalized:
            passed = total_findings == 0
            evidence = f"review verdict={verdict}; total_findings={total_findings}"
        elif "no critical" in normalized:
            passed = critical == 0
            evidence = f"review verdict={verdict}; critical={critical}"
        elif "no high" in normalized or "no severe" in normalized:
            passed = critical == 0 and high == 0
            evidence = f"review verdict={verdict}; critical={critical}; high={high}"
        else:
            passed = verdict != "fail"
            evidence = (
                f"review verdict={verdict}; critical={critical}; "
                f"high={high}; total_findings={total_findings}"
            )

        if passed:
            criterion.meet(evidence)
        else:
            criterion.fail(evidence)

    def _evaluate_change_backed_criterion(
        self,
        *,
        job: BuildJob,
        criterion,
        normalized: str,
        change_set: dict[str, Any],
    ) -> None:
        deliverable_changed = list(change_set.get("deliverable_changed_files", []))
        all_changed = list(change_set.get("changed_files", []))
        internal_changed = list(change_set.get("internal_files", []))

        def _evidence(prefix: str) -> str:
            changed_summary = ", ".join(deliverable_changed[:5]) or "none"
            internal_summary = ", ".join(internal_changed[:3]) or "none"
            return (
                f"{prefix}; deliverable_changed={changed_summary}; "
                f"internal_changed={internal_summary}; files_changed={len(all_changed)}"
            )

        if "target file" in normalized:
            if not job.intake.target_files:
                criterion.fail("Criterion references target files, but intake.target_files is empty")
                return
            changed_lookup = {path.casefold() for path in deliverable_changed}
            missing = [
                path for path in job.intake.target_files
                if path.casefold() not in changed_lookup
            ]
            if missing:
                criterion.fail(_evidence(f"Missing target file changes: {', '.join(missing)}"))
            else:
                criterion.meet(_evidence("All target files changed"))
            return

        if "readme" in normalized:
            passed = any("readme" in path.casefold() for path in deliverable_changed)
            if passed:
                criterion.meet(_evidence("README changed"))
            else:
                criterion.fail(_evidence("README was not changed"))
            return

        if "docs" in normalized or "documentation" in normalized:
            passed = any(
                path.casefold().startswith("docs/") or path.casefold().endswith(".md")
                for path in deliverable_changed
            )
            if passed:
                criterion.meet(_evidence("Documentation changed"))
            else:
                criterion.fail(_evidence("No documentation changes captured"))
            return

        if deliverable_changed:
            criterion.meet(_evidence("Workspace changes captured"))
            return

        criterion.fail(_evidence("No deliverable file changes captured"))

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

    def _collect_workspace_changes(
        self,
        job: BuildJob,
        workspace_path: str,
    ) -> dict[str, Any]:
        source_root = Path(job.intake.repo_path).resolve()
        workspace_root = Path(workspace_path).resolve()
        relative_paths = sorted(
            set(self._list_relative_files(source_root))
            | set(self._list_relative_files(workspace_root))
        )

        patch_chunks: list[str] = []
        stat_lines: list[str] = []
        changed_files: list[str] = []
        deliverable_changed_files: list[str] = []
        internal_files: list[str] = []
        added_files: list[str] = []
        removed_files: list[str] = []
        modified_files: list[str] = []
        binary_files: list[str] = []
        insertions = 0
        deletions = 0

        for relative_path in relative_paths:
            source_file = source_root / relative_path
            workspace_file = workspace_root / relative_path
            source_exists = source_file.exists()
            workspace_exists = workspace_file.exists()

            source_bytes = source_file.read_bytes() if source_exists else b""
            workspace_bytes = workspace_file.read_bytes() if workspace_exists else b""

            if source_exists and workspace_exists and source_bytes == workspace_bytes:
                continue

            changed_files.append(relative_path)
            if relative_path in _INTERNAL_WORKSPACE_FILES:
                internal_files.append(relative_path)
            else:
                deliverable_changed_files.append(relative_path)

            if not source_exists:
                added_files.append(relative_path)
                change_code = "A"
            elif not workspace_exists:
                removed_files.append(relative_path)
                change_code = "D"
            else:
                modified_files.append(relative_path)
                change_code = "M"

            source_text = self._decode_patch_text(source_bytes)
            workspace_text = self._decode_patch_text(workspace_bytes)

            if source_text is None or workspace_text is None:
                binary_files.append(relative_path)
                patch_chunks.append(
                    f"diff --binary a/{relative_path} b/{relative_path}\n"
                    f"Binary files differ: {relative_path}"
                )
                stat_lines.append(f"{change_code} {relative_path} (binary)")
                continue

            diff_lines = list(
                difflib.unified_diff(
                    source_text.splitlines(),
                    workspace_text.splitlines(),
                    fromfile=f"a/{relative_path}",
                    tofile=f"b/{relative_path}",
                    lineterm="",
                )
            )
            file_insertions = sum(
                1 for line in diff_lines
                if line.startswith("+") and not line.startswith("+++")
            )
            file_deletions = sum(
                1 for line in diff_lines
                if line.startswith("-") and not line.startswith("---")
            )
            insertions += file_insertions
            deletions += file_deletions
            if diff_lines:
                patch_chunks.append("\n".join(diff_lines))
            stat_lines.append(
                f"{change_code} {relative_path} (+{file_insertions} -{file_deletions})"
            )

        stat_header = (
            f"{len(changed_files)} files changed, "
            f"{insertions} insertions(+), {deletions} deletions(-)"
        )
        if binary_files:
            stat_header += f", {len(binary_files)} binary"

        return {
            "patch": "\n\n".join(chunk for chunk in patch_chunks if chunk) or "(no workspace changes)",
            "stat": "\n".join([stat_header, *stat_lines]) if stat_lines else stat_header,
            "changed_files": changed_files,
            "deliverable_changed_files": deliverable_changed_files,
            "internal_files": internal_files,
            "added_files": added_files,
            "removed_files": removed_files,
            "modified_files": modified_files,
            "binary_files": binary_files,
            "files_changed": len(changed_files),
            "deliverable_files_changed": len(deliverable_changed_files),
            "insertions": insertions,
            "deletions": deletions,
        }

    def _list_relative_files(self, root: Path) -> list[str]:
        if not root.exists():
            return []
        relative_paths: list[str] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative_path = path.relative_to(root).as_posix()
            if self._should_skip_change_path(relative_path):
                continue
            relative_paths.append(relative_path)
        return relative_paths

    def _should_skip_change_path(self, relative_path: str) -> bool:
        skip_parts = {
            ".git",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "venv",
        }
        return any(part in skip_parts for part in Path(relative_path).parts)

    def _decode_patch_text(self, data: bytes) -> str | None:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _capture_diff_artifact(
        self,
        job: BuildJob,
        change_set: dict[str, Any],
    ) -> None:
        """Capture deterministic patch and diff artifacts for workspace output."""
        patch_artifact = BuildArtifact(
            artifact_kind=ArtifactKind.PATCH,
            job_id=job.id,
            content=change_set.get("patch", "(no workspace changes)"),
            content_json={
                "files_changed": change_set.get("files_changed", 0),
                "deliverable_files_changed": change_set.get("deliverable_files_changed", 0),
                "changed_files": change_set.get("changed_files", []),
                "internal_files": change_set.get("internal_files", []),
                "binary_files": change_set.get("binary_files", []),
                "insertions": change_set.get("insertions", 0),
                "deletions": change_set.get("deletions", 0),
            },
            format="diff",
        )
        diff_artifact = BuildArtifact(
            artifact_kind=ArtifactKind.DIFF,
            job_id=job.id,
            content=change_set.get("stat", "(no workspace changes)"),
            content_json={
                "files_changed": change_set.get("files_changed", 0),
                "deliverable_files_changed": change_set.get("deliverable_files_changed", 0),
                "added_files": change_set.get("added_files", []),
                "removed_files": change_set.get("removed_files", []),
                "modified_files": change_set.get("modified_files", []),
            },
            format="text",
        )
        job.artifacts.extend([patch_artifact, diff_artifact])
        self._save_artifact(job, patch_artifact)
        self._save_artifact(job, diff_artifact)

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
        self._save_job(job)

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

    def get_delivery_bundle(self, job_id: str) -> dict[str, Any] | None:
        """Assemble a build delivery package preview.

        delivery_ready is always False here. External delivery still requires
        explicit approval via request_delivery_approval().
        """
        self.initialize()
        job = self.load_job(job_id)
        if job is None:
            return None

        artifacts = self._storage.get_artifacts(job_id)
        patch_artifact = next(
            (artifact for artifact in artifacts if artifact.get("artifact_kind") == ArtifactKind.PATCH.value),
            None,
        )
        diff_artifact = next(
            (artifact for artifact in artifacts if artifact.get("artifact_kind") == ArtifactKind.DIFF.value),
            None,
        )
        verification_artifact = next(
            (
                artifact for artifact in artifacts
                if artifact.get("artifact_kind") == ArtifactKind.VERIFICATION_REPORT.value
            ),
            None,
        )
        acceptance_artifact = next(
            (
                artifact for artifact in artifacts
                if artifact.get("artifact_kind") == ArtifactKind.ACCEPTANCE_REPORT.value
            ),
            None,
        )
        review_artifact = next(
            (
                artifact for artifact in artifacts
                if artifact.get("artifact_kind") == ArtifactKind.REVIEW_REPORT.value
            ),
            None,
        )
        findings_artifact = next(
            (
                artifact for artifact in artifacts
                if artifact.get("artifact_kind") == ArtifactKind.FINDING_LIST.value
            ),
            None,
        )
        workspace = (
            self._workspace_manager.get(job.workspace_id)
            if self._workspace_manager is not None and job.workspace_id
            else None
        )

        package = DeliveryPackage(
            bundle_id=self._delivery_bundle_id(job.id),
            job_id=job.id,
            job_kind=job.job_kind,
            package_type="build_delivery",
            title=f"Build delivery package for {job.id[:8]}",
            status=job.status.value,
            requester=job.requester,
            workspace_id=job.workspace_id,
            artifact_ids=[artifact.get("id", "") for artifact in artifacts],
            artifact_count=len(artifacts),
            delivery_ready=False,
            created_at=job.timing.created_at,
            completed_at=job.timing.completed_at,
            summary={
                "build_type": job.build_type.value,
                "capability_id": job.capability_id,
                "verification_passed": job.verification_passed,
                "acceptance_accepted": job.acceptance.accepted,
                "review_requested": job.intake.run_post_build_review,
                "review_verdict": job.post_build_review_verdict or "not_requested",
                "files_changed": (patch_artifact or {}).get("content_json", {}).get("files_changed", 0),
                "deliverable_files_changed": (patch_artifact or {}).get("content_json", {}).get(
                    "deliverable_files_changed",
                    0,
                ),
            },
            payload={
                "verification_report": (
                    verification_artifact or {}
                ).get("content_json", {"results": [result.to_dict() for result in job.verification_results]}),
                "acceptance_report": (
                    acceptance_artifact or {}
                ).get("content_json", job.acceptance.to_dict()),
                "patch": {
                    "artifact_id": (patch_artifact or {}).get("id", ""),
                    "content": (patch_artifact or {}).get("content", ""),
                    "metadata": (patch_artifact or {}).get("content_json", {}),
                },
                "diff": {
                    "artifact_id": (diff_artifact or {}).get("id", ""),
                    "content": (diff_artifact or {}).get("content", ""),
                    "metadata": (diff_artifact or {}).get("content_json", {}),
                },
                "post_build_review": (
                    review_artifact or {}
                ).get(
                    "content_json",
                    {
                        "review_job_id": job.post_build_review_job_id,
                        "verdict": job.post_build_review_verdict,
                        "finding_counts": job.post_build_review_findings,
                    },
                ),
                "findings": (findings_artifact or {}).get("content_json", {}).get("findings", []),
                "workspace": (
                    workspace.to_dict()
                    if workspace is not None
                    else {"id": job.workspace_id, "status": ""}
                ),
                "error": job.error,
            },
        )
        self._sync_delivery_record(
            bundle=package,
            status=DeliveryLifecycleStatus.PREPARED,
            event_type="prepared",
            detail="Build delivery package assembled",
        )
        package_dict = package.to_dict()
        self._record_delivery_bundle_retention(job=job, bundle=package_dict)
        return package_dict

    def request_delivery_approval(self, job_id: str) -> dict[str, Any]:
        """Gate build delivery through the approval queue."""
        self.initialize()
        job = self.load_job(job_id)
        if job is None:
            return {"error": f"Job '{job_id}' not found"}
        if job.status != JobStatus.COMPLETED:
            return {"error": f"Job '{job_id}' is {job.status.value}, not completed"}

        bundle = self.get_delivery_bundle(job_id)
        if bundle is None:
            return {"error": f"Delivery package for '{job_id}' could not be assembled"}

        if self._approval_queue is None:
            import os

            if os.environ.get("AGENT_DEV_MODE") == "1":
                logger.warning("build_delivery_approval_dev_bypass", job_id=job_id)
                return {
                    "job_id": job_id,
                    "bundle_id": bundle["bundle_id"],
                    "delivery_ready": True,
                    "approval_bypassed": True,
                    "warning": "DEV MODE: approval bypassed. Not safe for production.",
                }
            return {
                "error": "Delivery blocked: no approval queue configured. "
                "External delivery requires approval gating.",
                "job_id": job_id,
                "bundle_id": bundle["bundle_id"],
                "delivery_ready": False,
            }

        from agent.core.approval import ApprovalCategory

        delivery_policy = get_delivery_policy(job.intake.delivery_policy_id)
        req = self._approval_queue.propose(
            category=ApprovalCategory.EXTERNAL,
            description=f"Deliver build package for job {job_id[:8]} ({job.build_type.value})",
            risk_level="medium",
            reason=(
                f"Build of {job.intake.repo_path} — "
                f"{bundle['summary'].get('deliverable_files_changed', 0)} deliverable files changed"
            ),
            context={
                "job_id": job_id,
                "job_kind": job.job_kind.value,
                "workspace_id": job.workspace_id,
                "bundle_id": bundle["bundle_id"],
                "artifact_ids": bundle["artifact_ids"],
                "requester": job.requester,
                "capability_id": job.capability_id,
                "delivery_policy_id": delivery_policy.id,
            },
        )
        self._sync_delivery_record(
            bundle=DeliveryPackage(
                bundle_id=bundle["bundle_id"],
                job_id=job.id,
                job_kind=job.job_kind,
                package_type=bundle["package_type"],
                title=bundle["title"],
                status=job.status.value,
                requester=job.requester,
                workspace_id=job.workspace_id,
                artifact_ids=list(bundle["artifact_ids"]),
                artifact_count=bundle["artifact_count"],
                delivery_ready=False,
                created_at=bundle["created_at"],
                completed_at=bundle["completed_at"],
                summary=dict(bundle["summary"]),
                payload=dict(bundle["payload"]),
            ),
            status=DeliveryLifecycleStatus.AWAITING_APPROVAL,
            event_type="approval_requested",
            detail=f"Approval requested under delivery policy {delivery_policy.id}",
            approval_request_id=req.id,
            metadata={"delivery_policy_id": delivery_policy.id},
        )
        logger.info(
            "build_delivery_approval_requested",
            job_id=job_id,
            approval_id=req.id,
            bundle_id=bundle["bundle_id"],
        )
        return {
            "job_id": job_id,
            "bundle_id": bundle["bundle_id"],
            "approval_request_id": req.id,
            "approval_status": "pending",
            "delivery_ready": False,
        }

    def get_delivery_record(self, job_id: str) -> dict[str, Any] | None:
        """Return persisted delivery lifecycle state for a build job."""
        bundle_id = self._delivery_bundle_id(job_id)
        record = self._refresh_delivery_record(bundle_id)
        if record is None:
            return None
        return record.to_dict()

    def mark_delivery_handed_off(self, job_id: str, *, note: str = "") -> dict[str, Any]:
        """Record final handoff after approval."""
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
        if (
            self._approval_queue is not None
            and record.approval_request_id
        ):
            self._approval_queue.mark_executed(record.approval_request_id)
        record = self._control_plane_state.mark_delivery_handed_off(
            bundle_id,
            detail=note or f"Build delivery for job {job_id[:8]} handed off",
        ) if self._control_plane_state is not None else record
        if record is None:
            return {"error": f"Delivery record not found for bundle '{bundle_id}'"}
        return record.to_dict()

    def _delivery_bundle_id(self, job_id: str) -> str:
        return f"build-delivery-{job_id}"

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

    def list_artifacts(
        self,
        *,
        job_id: str = "",
        artifact_kind: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List persisted build artifacts for shared query/recovery."""
        self.initialize()
        return self._storage.list_artifacts(
            job_id=job_id,
            artifact_kind=artifact_kind,
            limit=limit,
        )

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        """Load one persisted build artifact for shared query/recovery."""
        self.initialize()
        return self._storage.get_artifact(artifact_id)

    def get_stats(self) -> dict[str, Any]:
        """Summarize build service state for orchestrator status/reporting."""
        self.initialize()
        stats = self._storage.get_stats()
        return {
            "initialized": self._initialized,
            "approval_queue_configured": self._approval_queue is not None,
            "capabilities": [capability.id for capability in list_capabilities()],
            "review_gate_policies": [
                policy.id for policy in list_review_gate_policies()
            ],
            "delivery_policies": [
                policy.id for policy in list_delivery_policies()
            ],
            **stats,
        }
