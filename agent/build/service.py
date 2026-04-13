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
import json
import re
import shlex
import shutil
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import structlog

from agent.build.capabilities import get_capability, list_capabilities
from agent.build.models import (
    AcceptanceCriterion,
    ArtifactKind,
    BuildArtifact,
    BuildCheckpointPhase,
    BuildImplementationMode,
    BuildIntake,
    BuildJob,
    BuildJobType,
    BuildOperation,
    BuildOperationResult,
    BuildOperationStatus,
    BuildOperationType,
    BuildPhase,
    CriterionEvaluator,
    CriterionKind,
    CriterionStatus,
    VerificationKind,
    VerificationResult,
)
from agent.build.storage import BuildStorage
from agent.build.verification import DEFAULT_COMMANDS, run_verification_suite
from agent.control.denials import make_denial
from agent.control.models import (
    DeliveryLifecycleStatus,
    DeliveryPackage,
    ExecutionMode,
    JobStatus,
    TraceRecordKind,
)
from agent.control.policy import (
    evaluate_build_capability_guardrails,
    get_build_execution_policy,
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
            source=intake.source or "manual",
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
            job.denial = make_denial(
                code="build_validation_failed",
                summary="Build intake validation failed",
                detail="; ".join(errors),
                scope=intake.repo_path,
                suggested_action="Fix the build intake and rerun the request.",
            ).to_dict()
            self._save_job(job)
            return job
        try:
            capability = self._resolve_capability(intake)
        except ValueError as e:
            t_validate.fail(str(e))
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.denial = make_denial(
                code="build_capability_resolution_failed",
                summary="Build capability resolution failed",
                detail=str(e),
                scope=intake.repo_path,
                suggested_action="Choose a supported build capability or build type.",
            ).to_dict()
            self._save_job(job)
            return job
        job.capability_id = capability.id
        capability_errors = self._validate_capability_plan(
            intake=intake,
            capability=capability,
        )
        if capability_errors:
            detail = "; ".join(capability_errors)
            t_validate.fail(detail)
            job.status = JobStatus.FAILED
            job.error = f"Capability constraints failed: {detail}"
            job.denial = make_denial(
                code="build_capability_plan_invalid",
                summary="Build implementation plan violates capability constraints",
                detail=detail,
                scope=intake.repo_path,
                suggested_action=(
                    "Reduce the operation count, keep mutations inside the declared "
                    "target files, or choose a capability that matches the requested plan."
                ),
                metadata={
                    "capability_id": capability.id,
                    "operation_count": len(intake.implementation_plan),
                    "operation_mix": self._operation_mix(intake.implementation_plan),
                    "target_files": list(intake.target_files),
                },
            ).to_dict()
            self._save_job(job)
            return job
        execution_policy = self._build_execution_policy(job)
        self._record_control_trace(
            trace_kind=TraceRecordKind.EXECUTION,
            title="Build execution policy",
            detail=execution_policy.description,
            job_id=job.id,
            metadata={
                "policy_id": execution_policy.id,
                "build_type": job.build_type.value,
                "source": job.source,
                "environment_profile_id": execution_policy.environment_profile_id,
                "allowed": execution_policy.allow_workspace_mutation,
            },
        )
        if intake.implementation_plan:
            self._record_control_trace(
                trace_kind=TraceRecordKind.CAPABILITY,
                title="Build capability guardrails",
                detail=(
                    f"{len(intake.implementation_plan)} structured operation(s) "
                    f"fit capability {capability.id}."
                ),
                job_id=job.id,
                metadata={
                    "capability_id": capability.id,
                    "max_operation_count": capability.max_operation_count,
                    "supported_operation_types": [
                        item.value for item in capability.supported_operation_types
                    ],
                    "operation_mix": self._operation_mix(intake.implementation_plan),
                    "target_files": list(intake.target_files),
                },
            )
        if not execution_policy.allow_workspace_mutation:
            t_validate.fail(execution_policy.description)
            job.status = JobStatus.BLOCKED
            job.error = execution_policy.description
            job.denial = make_denial(
                code="build_execution_blocked",
                summary="Build execution denied by policy",
                detail=execution_policy.description,
                scope=intake.repo_path,
                policy_id=execution_policy.id,
                environment_profile_id=execution_policy.environment_profile_id,
                suggested_action="Use a supported build source/type or relax the build execution policy intentionally.",
            ).to_dict()
            self._save_job(job)
            return job
        t_validate.complete(f"input valid: {intake.build_type.value}")
        job.status = JobStatus.VALIDATING
        job.record_checkpoint(
            BuildCheckpointPhase.VALIDATED,
            detail=f"capability={capability.id}; policy={execution_policy.id}",
        )

        # ── Step 2: Workspace setup ──
        t_ws = job.trace("workspace")
        job.phase = BuildPhase.WORKSPACE_SETUP

        if self._workspace_manager is None:
            t_ws.fail("No workspace manager — build requires workspace")
            job.status = JobStatus.FAILED
            job.error = "Build jobs require a workspace manager."
            job.denial = make_denial(
                code="build_workspace_required",
                summary="Build execution blocked",
                detail="Build jobs require a workspace manager.",
                scope=intake.repo_path,
                policy_id=execution_policy.id,
                environment_profile_id=execution_policy.environment_profile_id,
                suggested_action="Configure the workspace manager before running build jobs.",
            ).to_dict()
            self._save_job(job)
            return job

        try:
            ws = self._workspace_manager.create(
                name=f"build-{job.id[:8]}",
                task_id=job.id,
                project_id=getattr(job.intake, "project_id", ""),
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
            job.denial = make_denial(
                code="build_workspace_setup_failed",
                summary="Build workspace setup failed",
                detail=str(e),
                scope=intake.repo_path,
                policy_id=execution_policy.id,
                environment_profile_id=execution_policy.environment_profile_id,
                suggested_action="Inspect the workspace configuration and rerun the build.",
            ).to_dict()
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
            job.denial = make_denial(
                code="build_workspace_sync_failed",
                summary="Build workspace sync failed",
                detail=str(e),
                scope=intake.repo_path,
                policy_id=execution_policy.id,
                environment_profile_id=execution_policy.environment_profile_id,
                suggested_action="Fix repository sync into the managed workspace and rerun the build.",
            ).to_dict()
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
            # Inherit the implementation mode from the previous attempt
            # so the guard for AUDIT_MARKER_ONLY doesn't reject a resume
            # that already executed BOUNDED_LOCAL_ENGINE.
            implementation_mode=previous.implementation_mode,
            implementation_results=list(previous.implementation_results),
        )
        job.trace("resume").complete(
            f"resumed_from={previous.id}; last_checkpoint="
            f"{previous.last_checkpoint.phase.value if previous.last_checkpoint else 'none'}"
        )
        execution_policy = self._build_execution_policy(job)
        self._record_control_trace(
            trace_kind=TraceRecordKind.EXECUTION,
            title="Build execution policy",
            detail=execution_policy.description,
            job_id=job.id,
            metadata={
                "policy_id": execution_policy.id,
                "build_type": job.build_type.value,
                "source": job.source,
                "environment_profile_id": execution_policy.environment_profile_id,
                "allowed": execution_policy.allow_workspace_mutation,
                "resume_of": previous.id,
            },
        )
        if not execution_policy.allow_workspace_mutation:
            job.status = JobStatus.BLOCKED
            job.error = execution_policy.description
            job.denial = make_denial(
                code="build_execution_blocked",
                summary="Build execution denied by policy",
                detail=execution_policy.description,
                scope=job.intake.repo_path,
                policy_id=execution_policy.id,
                environment_profile_id=execution_policy.environment_profile_id,
                suggested_action="Use a supported build source/type or relax the build execution policy intentionally.",
            ).to_dict()
            self._save_job(job)
            return job

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
                project_id=getattr(job.intake, "project_id", ""),
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
        # ── Step 2.5: LLM Code Generation (if no explicit plan) ──
        codegen_produced = False
        if not job.intake.implementation_plan and job.intake.description:
            t_codegen = job.trace("codegen")
            try:
                from agent.build.codegen import generate_build_operations
                generated_ops = await generate_build_operations(
                    description=job.intake.description,
                    max_operations=20,
                )
                job.intake.implementation_plan = generated_ops
                codegen_produced = True
                t_codegen.complete(
                    f"LLM generated {len(generated_ops)} file operations"
                )
                logger.info(
                    "build_codegen_complete",
                    job_id=job.id,
                    operations=len(generated_ops),
                    files=[op.path for op in generated_ops],
                )
            except Exception as e:
                t_codegen.fail(str(e))
                job.metadata["codegen_fallback"] = True
                job.metadata["codegen_error"] = str(e)[:500]
                logger.warning(
                    "build_codegen_fallback",
                    job_id=job.id,
                    error=str(e)[:500],
                    hint=(
                        "continuing without structured operations; "
                        "builder will stay in audit_marker_only mode"
                    ),
                )
                self._record_control_trace(
                    trace_kind=TraceRecordKind.EXECUTION,
                    title="Code generation fallback",
                    detail=(
                        "LLM code generation failed; continuing without "
                        "structured operations."
                    ),
                    job_id=job.id,
                    workspace_id=job.workspace_id,
                    metadata={
                        "error": str(e)[:500],
                        "fallback_mode": BuildImplementationMode.AUDIT_MARKER_ONLY.value,
                    },
                )

        # ── Step 3: Build (execute implementation) ──
        job.status = JobStatus.RUNNING
        job.phase = BuildPhase.BUILDING

        # Codegen-produced plans run in Docker isolation.
        # No silent fallback to host — if Docker fails, the build fails.
        if codegen_produced and job.intake.implementation_plan:
            t_docker = job.trace("docker_build")
            try:
                from agent.build.docker_executor import run_project_in_docker
                docker_result = await run_project_in_docker(
                    operations=job.intake.implementation_plan,
                    description=job.intake.description,
                    retry_on_failure=True,
                    max_retries=2,
                )
                # Also write files to workspace for artifact capture
                self._execute_build(job, workspace_path)

                job.metadata["docker_result"] = docker_result.to_dict()
                if docker_result.success:
                    t_docker.complete(
                        f"Docker build+test OK: {docker_result.summary}"
                    )
                    job.status = JobStatus.COMPLETED
                    job.metadata["verification_passed"] = True
                else:
                    t_docker.fail(
                        f"Docker build failed: {docker_result.summary}\n"
                        f"{docker_result.test_output[:500]}"
                    )
                    job.status = JobStatus.FAILED
                    job.error = f"Docker build failed: {docker_result.summary}"
                    job.metadata["verification_passed"] = False
                self._finalize(job, workspace_path)
                return job
            except Exception as e:
                # Docker unavailable = hard fail, no silent fallback
                t_docker.fail(str(e))
                logger.error("docker_executor_failed", error=str(e),
                             job_id=job.id)
                job.status = JobStatus.FAILED
                job.error = f"Docker execution failed: {e}"
                job.metadata["verification_passed"] = False
                self._finalize(job, workspace_path)
                return job

        if can_skip_completed_steps and self._can_reuse_checkpoint(
            job, BuildCheckpointPhase.BUILT
        ):
            job.trace("resume:build").complete("build checkpoint reused")
        else:
            t_build = job.trace("build")
            build_ok = self._execute_build(job, workspace_path)
            if build_ok:
                build_summary = self._build_step_summary(job)
                t_build.complete(build_summary)
                self._record_checkpoint(
                    job,
                    BuildCheckpointPhase.BUILT,
                    detail=build_summary,
                )
            else:
                t_build.fail(job.error or "build failed")
                job.status = JobStatus.FAILED
                self._finalize(job, workspace_path)
                return job

        # ── Guard: a build that produced zero changes must not pass ──
        # AUDIT_MARKER_ONLY means the builder wrote a `.build_job` marker
        # file but applied no operations. We treat this as fail-open in
        # two specific situations:
        #   1. Codegen failed and we fell back to marker-only mode.
        #   2. The operator submitted a build with no implementation_plan
        #      AND no test override of `_execute_build` (i.e. nothing
        #      ran). In production this matches the codegen-fallback
        #      scenario; in tests with a monkeypatched _execute_build
        #      the implementation_plan is set, so the guard skips.
        codegen_failed = bool(job.metadata.get("codegen_fallback"))
        no_real_plan = not job.intake.implementation_plan
        if (
            job.implementation_mode == BuildImplementationMode.AUDIT_MARKER_ONLY
            and (codegen_failed or no_real_plan)
        ):
            if codegen_failed:
                reason = (
                    "Build rejected: code generation failed and no implementation "
                    f"was produced (codegen_error={job.metadata.get('codegen_error', 'unknown')!r}). "
                    "Cannot accept a build with zero changes."
                )
            else:
                reason = (
                    "Build rejected: no implementation plan was supplied and "
                    "no operations were applied. AUDIT_MARKER_ONLY mode "
                    "cannot be delivered as a real build."
                )
            job.trace("codegen_fallback_guard").fail(reason)
            job.status = JobStatus.FAILED
            job.error = reason
            job.acceptance.accepted = False
            job.acceptance.summary = reason
            self._record_control_trace(
                trace_kind=TraceRecordKind.EXECUTION,
                title="Codegen fallback guard — build rejected",
                detail=reason,
                job_id=job.id,
                workspace_id=job.workspace_id,
                metadata={
                    "codegen_error": job.metadata.get("codegen_error"),
                    "implementation_mode": job.implementation_mode.value,
                },
            )
            self._finalize(job, workspace_path)
            return job

        # ── Step 4: Verify ──
        job.status = JobStatus.VERIFYING
        verification_plan: dict[str, Any] = {
            "steps": [],
            "capability_id": job.capability_id,
            "default_steps": [],
            "step_details": {},
            "commands": {},
            "reused": False,
        }
        if can_skip_completed_steps and self._can_reuse_checkpoint(
            job, BuildCheckpointPhase.VERIFIED
        ):
            results = job.verification_results
            passed = all(r.passed for r in results)
            verification_plan["steps"] = [result.kind.value for result in results]
            verification_plan["reused"] = True
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

        # ── Step 5: Create verification artifacts ──
        self._capture_verification_artifacts(
            job=job,
            results=results,
            passed=passed,
            verification_plan=verification_plan,
        )

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
                job.denial = make_denial(
                    code="build_review_gate_blocked",
                    summary="Build completion blocked by review gate policy",
                    detail=block_reason,
                    scope=job.id,
                    policy_id=self._review_gate_policy(job).id,
                    environment_profile_id=self._build_environment_profile_id(job),
                    suggested_action="Address the post-build review findings or relax the review gate policy intentionally.",
                ).to_dict()
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
            content_json=self._build_acceptance_report(
                job=job,
                verification_passed=passed,
            ),
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
        """Execute the mutable build step inside the managed workspace."""
        wp = Path(workspace_path)
        if not wp.is_dir():
            job.error = f"Workspace path does not exist: {workspace_path}"
            return False

        marker = wp / ".build_job"
        marker.write_text(
            (
                f"job_id={job.id}\n"
                f"build_type={job.build_type.value}\n"
                f"capability_id={job.capability_id}\n"
            ),
            encoding="utf-8",
        )
        self._record_workspace_file(job, marker)

        if not job.intake.implementation_plan:
            job.implementation_mode = BuildImplementationMode.AUDIT_MARKER_ONLY
            job.implementation_results = []
            self._record_control_trace(
                trace_kind=TraceRecordKind.EXECUTION,
                title="Build implementation mode",
                detail="No structured implementation plan supplied; audit marker only.",
                job_id=job.id,
                workspace_id=job.workspace_id,
                metadata=self._implementation_metadata(job),
            )
            return True

        job.implementation_mode = BuildImplementationMode.BOUNDED_LOCAL_ENGINE
        job.implementation_results = []

        for operation in job.intake.implementation_plan:
            operation_trace = job.trace(
                f"implement:{operation.operation_type.value}:{operation.path}"
            )
            try:
                result = self._apply_build_operation(
                    job=job,
                    workspace_root=wp,
                    operation=operation,
                )
            except Exception as e:
                result = BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.FAILED,
                    changed=False,
                    detail=str(e),
                )
                job.implementation_results.append(result)
                detail = (
                    f"{operation.operation_type.value} {operation.path}: {e}"
                )
                operation_trace.fail(detail)
                job.error = f"Implementation step failed: {detail}"
                job.denial = make_denial(
                    code="build_implementation_failed",
                    summary="Build implementation step failed",
                    detail=job.error,
                    scope=job.id,
                    policy_id=job.intake.execution_policy_id or "workspace_local_mutation",
                    environment_profile_id=self._build_environment_profile_id(job),
                    suggested_action=(
                        "Fix the structured implementation plan or workspace content and rerun the build."
                    ),
                    metadata=self._implementation_metadata(job),
                ).to_dict()
                self._record_control_trace(
                    trace_kind=TraceRecordKind.EXECUTION,
                    title="Build implementation failure",
                    detail=detail,
                    job_id=job.id,
                    workspace_id=job.workspace_id,
                    metadata=self._implementation_metadata(job),
                )
                return False

            job.implementation_results.append(result)
            operation_trace.complete(result.detail)

        self._record_control_trace(
            trace_kind=TraceRecordKind.EXECUTION,
            title="Structured build implementation plan",
            detail=self._build_step_summary(job),
            job_id=job.id,
            workspace_id=job.workspace_id,
            metadata=self._implementation_metadata(job),
        )
        return True

    def _apply_build_operation(
        self,
        *,
        job: BuildJob,
        workspace_root: Path,
        operation: BuildOperation,
    ) -> BuildOperationResult:
        target = self._resolve_workspace_operation_path(workspace_root, operation.path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if operation.operation_type == BuildOperationType.WRITE_FILE:
            existing = (
                target.read_text(encoding="utf-8")
                if target.exists()
                else None
            )
            if existing == operation.content:
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail="Target file already matches the requested content.",
                )
            target.write_text(operation.content, encoding="utf-8")
            self._record_workspace_file(job, target)
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Wrote {operation.path}.",
            )

        if operation.operation_type == BuildOperationType.APPEND_TEXT:
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            if operation.content and operation.content in existing:
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail="Requested content is already present.",
                )
            updated = f"{existing}{operation.content}"
            target.write_text(updated, encoding="utf-8")
            self._record_workspace_file(job, target)
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Appended content to {operation.path}.",
            )

        if operation.operation_type == BuildOperationType.REPLACE_TEXT:
            if not target.exists():
                raise ValueError(f"Target file does not exist: {operation.path}")
            existing = target.read_text(encoding="utf-8")
            if operation.match_text not in existing:
                if operation.replacement_text and operation.replacement_text in existing:
                    return BuildOperationResult(
                        operation_id=operation.id,
                        operation_type=operation.operation_type,
                        path=operation.path,
                        status=BuildOperationStatus.NOOP,
                        changed=False,
                        detail="Replacement text is already present.",
                    )
                raise ValueError(f"match_text not found in {operation.path}")
            updated = existing.replace(
                operation.match_text,
                operation.replacement_text,
                1,
            )
            if updated == existing:
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail="Requested replacement produced no file change.",
                )
            target.write_text(updated, encoding="utf-8")
            self._record_workspace_file(job, target)
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Replaced text in {operation.path}.",
            )

        if operation.operation_type == BuildOperationType.INSERT_BEFORE_TEXT:
            if not target.exists():
                raise ValueError(f"Target file does not exist: {operation.path}")
            existing = target.read_text(encoding="utf-8")
            combined = f"{operation.content}{operation.match_text}"
            if combined and combined in existing:
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail="Requested content is already inserted before the anchor.",
                )
            if operation.match_text not in existing:
                raise ValueError(f"match_text not found in {operation.path}")
            updated = existing.replace(
                operation.match_text,
                f"{operation.content}{operation.match_text}",
                1,
            )
            target.write_text(updated, encoding="utf-8")
            self._record_workspace_file(job, target)
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Inserted content before anchor in {operation.path}.",
            )

        if operation.operation_type == BuildOperationType.INSERT_AFTER_TEXT:
            if not target.exists():
                raise ValueError(f"Target file does not exist: {operation.path}")
            existing = target.read_text(encoding="utf-8")
            combined = f"{operation.match_text}{operation.content}"
            if combined and combined in existing:
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail="Requested content is already inserted after the anchor.",
                )
            if operation.match_text not in existing:
                raise ValueError(f"match_text not found in {operation.path}")
            updated = existing.replace(
                operation.match_text,
                f"{operation.match_text}{operation.content}",
                1,
            )
            target.write_text(updated, encoding="utf-8")
            self._record_workspace_file(job, target)
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Inserted content after anchor in {operation.path}.",
            )

        if operation.operation_type == BuildOperationType.DELETE_TEXT:
            if not target.exists():
                raise ValueError(f"Target file does not exist: {operation.path}")
            existing = target.read_text(encoding="utf-8")
            if operation.match_text not in existing:
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail="Requested text is already absent.",
                )
            updated = existing.replace(operation.match_text, "", 1)
            target.write_text(updated, encoding="utf-8")
            self._record_workspace_file(job, target)
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Deleted text from {operation.path}.",
            )

        if operation.operation_type == BuildOperationType.COPY_FILE:
            source = self._resolve_workspace_operation_path(
                workspace_root,
                operation.source_path,
            )
            if not source.exists():
                raise ValueError(
                    f"Source file does not exist: {operation.source_path}"
                )
            if source.is_dir():
                raise ValueError(
                    f"copy_file does not support directories: {operation.source_path}"
                )
            source_content = source.read_text(encoding="utf-8")
            existing = (
                target.read_text(encoding="utf-8")
                if target.exists() and target.is_file()
                else None
            )
            if existing == source_content:
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail=(
                        f"{operation.path} already matches source "
                        f"{operation.source_path}."
                    ),
                )
            target.write_text(source_content, encoding="utf-8")
            self._record_workspace_file(job, target)
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Copied {operation.source_path} to {operation.path}.",
            )

        if operation.operation_type == BuildOperationType.MOVE_FILE:
            source = self._resolve_workspace_operation_path(
                workspace_root,
                operation.source_path,
            )
            if not source.exists():
                if target.exists():
                    return BuildOperationResult(
                        operation_id=operation.id,
                        operation_type=operation.operation_type,
                        path=operation.path,
                        status=BuildOperationStatus.NOOP,
                        changed=False,
                        detail=(
                            f"Source {operation.source_path} is already absent and "
                            f"{operation.path} exists."
                        ),
                    )
                raise ValueError(
                    f"Source file does not exist: {operation.source_path}"
                )
            if source.is_dir():
                raise ValueError(
                    f"move_file does not support directories: {operation.source_path}"
                )
            if source.resolve() == target.resolve():
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail="Source and target already point to the same file.",
                )
            if target.exists() and target.is_file():
                target.unlink()
            source.replace(target)
            self._record_workspace_file(job, target)
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Moved {operation.source_path} to {operation.path}.",
            )

        if operation.operation_type == BuildOperationType.JSON_SET:
            if target.exists():
                try:
                    document = json.loads(target.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSON in {operation.path}: {e}") from e
            else:
                document = {}
            if not isinstance(document, dict):
                raise ValueError(
                    f"json_set requires a JSON object at {operation.path}"
                )
            current: Any = document
            for part in operation.json_path[:-1]:
                nested = current.get(part)
                if nested is None:
                    nested = {}
                    current[part] = nested
                if not isinstance(nested, dict):
                    raise ValueError(
                        f"json_path {'/'.join(operation.json_path)} crosses a non-object node"
                    )
                current = nested
            final_key = operation.json_path[-1]
            if current.get(final_key) == operation.value:
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail=(
                        f"JSON path {'.'.join(operation.json_path)} already matches the requested value."
                    ),
                )
            current[final_key] = operation.value
            target.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self._record_workspace_file(job, target)
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Updated JSON path {'.'.join(operation.json_path)} in {operation.path}.",
            )

        if operation.operation_type == BuildOperationType.DELETE_FILE:
            if not target.exists():
                return BuildOperationResult(
                    operation_id=operation.id,
                    operation_type=operation.operation_type,
                    path=operation.path,
                    status=BuildOperationStatus.NOOP,
                    changed=False,
                    detail=f"{operation.path} already absent.",
                )
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return BuildOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                path=operation.path,
                status=BuildOperationStatus.APPLIED,
                changed=True,
                detail=f"Deleted {operation.path}.",
            )

        raise ValueError(f"Unsupported build operation: {operation.operation_type.value}")

    def _resolve_workspace_operation_path(
        self,
        workspace_root: Path,
        relative_path: str,
    ) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ValueError("Build operation paths must be relative to the workspace")
        if ".." in candidate.parts:
            raise ValueError("Build operation paths must not contain '..'")
        resolved = (workspace_root / candidate).resolve()
        try:
            resolved.relative_to(workspace_root.resolve())
        except ValueError as e:
            raise ValueError(
                "Build operation path escapes the managed workspace"
            ) from e
        return resolved

    def _record_workspace_file(self, job: BuildJob, path: Path) -> None:
        if self._workspace_manager is None or not job.workspace_id:
            return
        self._workspace_manager.record_file(job.workspace_id, str(path))

    def _build_step_summary(self, job: BuildJob) -> str:
        summary = self._implementation_summary(job)
        if job.implementation_mode == BuildImplementationMode.AUDIT_MARKER_ONLY:
            return "No structured implementation plan supplied; audit marker recorded."
        operation_mix = ", ".join(
            f"{kind}={count}"
            for kind, count in self._operation_mix(job.intake.implementation_plan).items()
        )
        return (
            "Structured implementation plan executed via bounded local engine: "
            f"{summary['operation_count']} operations, "
            f"{summary['applied_operations']} applied, "
            f"{summary['noop_operations']} noop, "
            f"{summary['changed_paths_count']} changed path(s), "
            f"mix={operation_mix or 'none'}."
        )

    def _workspace_execution_evidence(self, job: BuildJob) -> str:
        if job.implementation_mode == BuildImplementationMode.AUDIT_MARKER_ONLY:
            return "Workspace synchronized; no structured implementation plan supplied."
        implementation = self._implementation_summary(job)
        summary = ", ".join(implementation["changed_paths"][:5]) or "none"
        return (
            f"Workspace synchronized; bounded local engine ran "
            f"{implementation['operation_count']} operations; changed_paths={summary}"
        )

    def _implementation_metadata(self, job: BuildJob) -> dict[str, Any]:
        summary = self._implementation_summary(job)
        return {
            "mode": job.implementation_mode.value,
            "operation_count": summary["operation_count"],
            "operation_mix": self._operation_mix(job.intake.implementation_plan),
            "changed_operations": summary["changed_operations"],
            "applied_operations": summary["applied_operations"],
            "noop_operations": summary["noop_operations"],
            "failed_operations": summary["failed_operations"],
            "changed_paths": summary["changed_paths"],
            "result_status_counts": dict(summary["result_status_counts"]),
            "plan": [operation.to_dict() for operation in job.intake.implementation_plan],
            "results": [result.to_dict() for result in job.implementation_results],
        }

    def _implementation_summary(self, job: BuildJob) -> dict[str, Any]:
        changed_paths = sorted(
            {
                result.path
                for result in job.implementation_results
                if result.changed
            }
        )
        status_counts = {
            BuildOperationStatus.APPLIED.value: sum(
                1
                for result in job.implementation_results
                if result.status == BuildOperationStatus.APPLIED
            ),
            BuildOperationStatus.NOOP.value: sum(
                1
                for result in job.implementation_results
                if result.status == BuildOperationStatus.NOOP
            ),
            BuildOperationStatus.FAILED.value: sum(
                1
                for result in job.implementation_results
                if result.status == BuildOperationStatus.FAILED
            ),
        }
        return {
            "mode": job.implementation_mode.value,
            "operation_count": len(job.intake.implementation_plan),
            "changed_operations": sum(
                1 for result in job.implementation_results if result.changed
            ),
            "applied_operations": status_counts[BuildOperationStatus.APPLIED.value],
            "noop_operations": status_counts[BuildOperationStatus.NOOP.value],
            "failed_operations": status_counts[BuildOperationStatus.FAILED.value],
            "changed_paths": changed_paths,
            "changed_paths_count": len(changed_paths),
            "result_status_counts": status_counts,
        }

    def _operation_mix(
        self,
        operations: list[BuildOperation],
    ) -> dict[str, int]:
        mix: dict[str, int] = {}
        for operation in operations:
            key = operation.operation_type.value
            mix[key] = mix.get(key, 0) + 1
        return dict(sorted(mix.items()))

    def _validate_capability_plan(
        self,
        *,
        intake: BuildIntake,
        capability: Any,
    ) -> list[str]:
        decision = evaluate_build_capability_guardrails(
            capability=capability,
            operations=intake.implementation_plan,
            target_files=intake.target_files,
        )
        return list(decision["errors"])

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
        test_details = self._test_surface_details(wp)
        lint_details = self._lint_surface_details(wp)
        typecheck_details = self._typecheck_surface_details(wp)
        step_details: dict[str, list[str]] = {
            VerificationKind.TEST.value: list(test_details),
            VerificationKind.LINT.value: list(lint_details),
            VerificationKind.TYPECHECK.value: list(typecheck_details),
        }

        steps: list[VerificationKind] = []

        if VerificationKind.TEST in default_steps and test_details:
            steps.append(VerificationKind.TEST)

        if VerificationKind.LINT in default_steps and lint_details:
            steps.append(VerificationKind.LINT)

        if typecheck_details:
            steps.append(VerificationKind.TYPECHECK)

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
        plan = self._discover_verification_plan(
            workspace_path=workspace_path,
            capability_id=capability_id,
        )
        return cast("list[VerificationKind]", plan["steps"])

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
        node_tool_roots = [
            repo_root / "node_modules" / ".bin",
            workspace_root / "node_modules" / ".bin",
        ]
        node_tool_root = next((root for root in node_tool_roots if root.is_dir()), None)
        package_scripts = self._package_scripts(workspace_root)
        package_manager = self._detect_package_manager(workspace_root, repo_root)

        for step in steps:
            if step == VerificationKind.TEST:
                if self._python_test_markers(workspace_root) and tool_root is not None:
                    custom[step] = self._tool_command(
                        tool_root=tool_root,
                        binary_name="pytest",
                        module_name="pytest",
                        fallback=DEFAULT_COMMANDS[step],
                        args=["tests/", "-q", "--tb=short"],
                    )
                    continue
                package_script = self._select_package_script(
                    VerificationKind.TEST,
                    package_scripts,
                )
                if package_script:
                    custom[step] = self._package_manager_command(
                        package_manager,
                        package_script,
                    )
                    continue
                make_target = self._select_make_target(
                    workspace_root,
                    ("test", "check", "ci-test"),
                )
                if make_target:
                    custom[step] = ["make", make_target]
                    continue
                if tool_root is not None:
                    custom[step] = self._tool_command(
                        tool_root=tool_root,
                        binary_name="pytest",
                        module_name="pytest",
                        fallback=DEFAULT_COMMANDS[step],
                        args=["tests/", "-q", "--tb=short"],
                    )
            elif step == VerificationKind.LINT:
                if self._python_lint_markers(workspace_root) and tool_root is not None:
                    custom[step] = self._tool_command(
                        tool_root=tool_root,
                        binary_name="ruff",
                        module_name="ruff",
                        fallback=DEFAULT_COMMANDS[step],
                        args=["check", "."],
                    )
                    continue
                package_script = self._select_package_script(
                    VerificationKind.LINT,
                    package_scripts,
                )
                if package_script:
                    custom[step] = self._package_manager_command(
                        package_manager,
                        package_script,
                    )
                    continue
                eslint = (
                    node_tool_root / "eslint"
                    if node_tool_root is not None
                    else None
                )
                if eslint is not None and eslint.exists():
                    custom[step] = [str(eslint), "."]
                    continue
                make_target = self._select_make_target(
                    workspace_root,
                    ("lint", "check-lint"),
                )
                if make_target:
                    custom[step] = ["make", make_target]
                    continue
                if tool_root is not None:
                    custom[step] = self._tool_command(
                        tool_root=tool_root,
                        binary_name="ruff",
                        module_name="ruff",
                        fallback=DEFAULT_COMMANDS[step],
                        args=["check", "."],
                    )
            elif step == VerificationKind.TYPECHECK:
                if self._python_typecheck_markers(workspace_root) and tool_root is not None:
                    custom[step] = self._tool_command(
                        tool_root=tool_root,
                        binary_name="mypy",
                        module_name="mypy",
                        fallback=DEFAULT_COMMANDS[step],
                        args=[".", "--ignore-missing-imports"],
                    )
                    continue
                package_script = self._select_package_script(
                    VerificationKind.TYPECHECK,
                    package_scripts,
                )
                if package_script:
                    custom[step] = self._package_manager_command(
                        package_manager,
                        package_script,
                    )
                    continue
                pyright = (
                    node_tool_root / "pyright"
                    if node_tool_root is not None
                    else None
                )
                if pyright is not None and pyright.exists():
                    custom[step] = [str(pyright)]
                    continue
                tsc = (
                    node_tool_root / "tsc"
                    if node_tool_root is not None
                    else None
                )
                if tsc is not None and tsc.exists() and (workspace_root / "tsconfig.json").exists():
                    custom[step] = [str(tsc), "--noEmit"]
                    continue
                make_target = self._select_make_target(
                    workspace_root,
                    ("typecheck", "check-types", "mypy", "pyright"),
                )
                if make_target:
                    custom[step] = ["make", make_target]
                    continue
                if tool_root is not None:
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

    def _package_manager_command(
        self,
        package_manager: str,
        script_name: str,
    ) -> list[str]:
        if package_manager == "yarn":
            return ["yarn", script_name]
        return [package_manager, "run", script_name]

    def _detect_package_manager(
        self,
        workspace_root: Path,
        repo_root: Path,
    ) -> str:
        package_data = self._load_package_json(workspace_root)
        package_manager = str(package_data.get("packageManager", "")).strip().lower()
        if package_manager.startswith("pnpm@"):
            return "pnpm"
        if package_manager.startswith("yarn@"):
            return "yarn"
        if package_manager.startswith("npm@"):
            return "npm"

        lockfile_checks = (
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("package-lock.json", "npm"),
            ("npm-shrinkwrap.json", "npm"),
        )
        for marker, manager in lockfile_checks:
            if (workspace_root / marker).exists() or (repo_root / marker).exists():
                return manager
        return "npm"

    def _package_scripts(self, workspace_root: Path) -> dict[str, str]:
        data = self._load_package_json(workspace_root)
        raw_scripts = data.get("scripts", {})
        if not isinstance(raw_scripts, dict):
            return {}
        return {
            str(name): str(command)
            for name, command in raw_scripts.items()
            if str(name).strip() and str(command).strip()
        }

    def _package_dependencies(self, workspace_root: Path) -> set[str]:
        data = self._load_package_json(workspace_root)
        dependency_keys = (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        )
        names: set[str] = set()
        for key in dependency_keys:
            block = data.get(key, {})
            if isinstance(block, dict):
                names.update(str(name) for name in block if str(name).strip())
        return names

    def _load_package_json(self, workspace_root: Path) -> dict[str, Any]:
        package_path = workspace_root / "package.json"
        if not package_path.exists() or not package_path.is_file():
            return {}
        try:
            raw = json.loads(package_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _select_package_script(
        self,
        step: VerificationKind,
        scripts: dict[str, str],
    ) -> str:
        candidates: tuple[str, ...]
        if step == VerificationKind.TEST:
            candidates = ("test", "test:ci", "ci:test", "test:unit")
        elif step == VerificationKind.LINT:
            candidates = ("lint", "lint:ci", "check:lint")
        elif step == VerificationKind.TYPECHECK:
            candidates = ("typecheck", "check-types", "pyright", "mypy")
        else:
            return ""
        for candidate in candidates:
            if candidate in scripts:
                return candidate
        return ""

    def _make_targets(self, workspace_root: Path) -> set[str]:
        for file_name in ("Makefile", "makefile"):
            candidate = workspace_root / file_name
            if not candidate.exists() or not candidate.is_file():
                continue
            try:
                content = candidate.read_text(encoding="utf-8")
            except Exception:
                return set()
            return {
                match.group(1)
                for match in re.finditer(
                    r"^([A-Za-z0-9_.-]+)\s*:",
                    content,
                    flags=re.MULTILINE,
                )
                if not match.group(1).startswith(".")
            }
        return set()

    def _select_make_target(
        self,
        workspace_root: Path,
        candidates: tuple[str, ...],
    ) -> str:
        targets = self._make_targets(workspace_root)
        for candidate in candidates:
            if candidate in targets:
                return candidate
        return ""

    def _workflow_signal(
        self,
        workspace_root: Path,
        tokens: tuple[str, ...],
    ) -> str:
        candidates: list[Path] = []
        workflow_root = workspace_root / ".github" / "workflows"
        if workflow_root.is_dir():
            candidates.extend(sorted(workflow_root.glob("*.yml")))
            candidates.extend(sorted(workflow_root.glob("*.yaml")))
        gitlab_ci = workspace_root / ".gitlab-ci.yml"
        if gitlab_ci.exists():
            candidates.append(gitlab_ci)

        for candidate in candidates:
            try:
                content = candidate.read_text(encoding="utf-8").casefold()
            except Exception as exc:
                logger.debug("build_workflow_signal_read_failed", path=str(candidate), error=str(exc))
                continue
            for token in tokens:
                if token.casefold() in content:
                    relative = candidate.relative_to(workspace_root)
                    return f"{relative}:{token}"
        return ""

    def _load_toml(self, path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            return {}
        try:
            content = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return content if isinstance(content, dict) else {}

    def _test_surface_details(self, workspace_path: Path) -> list[str]:
        details = list(self._python_test_markers(workspace_path))
        scripts = self._package_scripts(workspace_path)
        package_script = self._select_package_script(VerificationKind.TEST, scripts)
        if package_script:
            details.append(f"package.json:scripts.{package_script}")
        make_target = self._select_make_target(workspace_path, ("test", "check", "ci-test"))
        if make_target:
            details.append(f"Makefile:{make_target}")
        workflow_signal = self._workflow_signal(
            workspace_path,
            ("pytest", "npm test", "pnpm test", "yarn test", "vitest", "jest"),
        )
        if workflow_signal:
            details.append(workflow_signal)
        if not details and self._workspace_has_glob(
            workspace_path,
            ("test_*.py", "*_test.py", "*.spec.ts", "*.test.ts", "*.test.js"),
        ):
            details.append("test-like files present")
        return self._dedupe_details(details)

    def _lint_surface_details(self, workspace_path: Path) -> list[str]:
        details = list(self._python_lint_markers(workspace_path))
        scripts = self._package_scripts(workspace_path)
        package_script = self._select_package_script(VerificationKind.LINT, scripts)
        if package_script:
            details.append(f"package.json:scripts.{package_script}")
        make_target = self._select_make_target(workspace_path, ("lint", "check-lint"))
        if make_target:
            details.append(f"Makefile:{make_target}")
        workflow_signal = self._workflow_signal(
            workspace_path,
            ("ruff", "flake8", "eslint", "npm run lint", "pnpm run lint", "yarn lint"),
        )
        if workflow_signal:
            details.append(workflow_signal)
        if not details and self._workspace_has_glob(
            workspace_path,
            ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx"),
        ):
            details.append("source files present")
        return self._dedupe_details(details)

    def _typecheck_surface_details(self, workspace_path: Path) -> list[str]:
        details = list(self._python_typecheck_markers(workspace_path))
        scripts = self._package_scripts(workspace_path)
        package_script = self._select_package_script(VerificationKind.TYPECHECK, scripts)
        if package_script:
            details.append(f"package.json:scripts.{package_script}")
        package_dependencies = self._package_dependencies(workspace_path)
        if "typescript" in package_dependencies:
            details.append("package.json:dependencies.typescript")
        if "pyright" in package_dependencies:
            details.append("package.json:dependencies.pyright")
        make_target = self._select_make_target(
            workspace_path,
            ("typecheck", "check-types", "mypy", "pyright"),
        )
        if make_target:
            details.append(f"Makefile:{make_target}")
        workflow_signal = self._workflow_signal(
            workspace_path,
            (
                "mypy",
                "pyright",
                "tsc --noemit",
                "npm run typecheck",
                "pnpm run typecheck",
                "yarn typecheck",
            ),
        )
        if workflow_signal:
            details.append(workflow_signal)
        return self._dedupe_details(details)

    def _python_test_markers(self, workspace_path: Path) -> list[str]:
        details = [
            marker
            for marker in ("tests/", "pytest.ini", "tox.ini", "noxfile.py")
            if self._workspace_has_marker(workspace_path, marker)
        ]
        pyproject = self._load_toml(workspace_path / "pyproject.toml")
        if "tool" in pyproject and "pytest" in pyproject.get("tool", {}):
            details.append("pyproject.toml:tool.pytest")
        if self._workspace_has_glob(workspace_path, ("test_*.py", "*_test.py")):
            details.append("python test files present")
        return self._dedupe_details(details)

    def _python_lint_markers(self, workspace_path: Path) -> list[str]:
        details = [
            marker
            for marker in (
                "pyproject.toml",
                "ruff.toml",
                ".ruff.toml",
                ".flake8",
                "eslint.config.js",
                ".eslintrc",
            )
            if self._workspace_has_marker(workspace_path, marker)
        ]
        pyproject = self._load_toml(workspace_path / "pyproject.toml")
        tool_section = pyproject.get("tool", {})
        if "ruff" in tool_section:
            details.append("pyproject.toml:tool.ruff")
        if self._file_contains(workspace_path / "setup.cfg", ("[flake8]", "ruff")):
            details.append("setup.cfg:lint")
        return self._dedupe_details(details)

    def _python_typecheck_markers(self, workspace_path: Path) -> list[str]:
        details = [
            marker
            for marker in ("mypy.ini", ".mypy.ini", "pyrightconfig.json", "tsconfig.json")
            if self._workspace_has_marker(workspace_path, marker)
        ]
        pyproject = self._load_toml(workspace_path / "pyproject.toml")
        tool_section = pyproject.get("tool", {})
        if "mypy" in tool_section:
            details.append("pyproject.toml:tool.mypy")
        if "pyright" in tool_section:
            details.append("pyproject.toml:tool.pyright")
        if self._file_contains(workspace_path / "setup.cfg", ("[mypy]", "mypy", "pyright")):
            details.append("setup.cfg:typecheck")
        return self._dedupe_details(details)

    def _dedupe_details(self, details: list[str]) -> list[str]:
        deduped: list[str] = []
        for detail in details:
            if detail and detail not in deduped:
                deduped.append(detail)
        return deduped

    def _capture_verification_artifacts(
        self,
        *,
        job: BuildJob,
        results: list[Any],
        passed: bool,
        verification_plan: dict[str, Any],
    ) -> None:
        suite_artifact = BuildArtifact(
            artifact_kind=ArtifactKind.VERIFICATION_REPORT,
            job_id=job.id,
            content_json={
                "report_kind": "suite",
                "all_passed": passed,
                "results": [result.to_dict() for result in results],
                "total_steps": len(results),
                "passed_steps": sum(1 for result in results if result.passed),
                "failed_steps": [result.kind.value for result in results if not result.passed],
                "verification_plan": verification_plan,
            },
            format="json",
        )
        job.artifacts.append(suite_artifact)
        self._save_artifact(job, suite_artifact)

        for index, result in enumerate(results):
            step_artifact = BuildArtifact(
                artifact_kind=ArtifactKind.VERIFICATION_REPORT,
                job_id=job.id,
                content=self._format_verification_artifact_content(result),
                content_json={
                    "report_kind": "step",
                    "step_index": index,
                    "verification_kind": result.kind.value,
                    "passed": result.passed,
                    "command": result.command,
                    "exit_code": result.exit_code,
                    "duration_ms": result.duration_ms,
                    "stdout_length": len(result.stdout),
                    "stderr_length": len(result.stderr),
                },
                format="text",
            )
            job.artifacts.append(step_artifact)
            self._save_artifact(job, step_artifact)

    def _format_verification_artifact_content(self, result: Any) -> str:
        sections = [
            f"# Verification Step: {result.kind.value}",
            "",
            f"- passed: {result.passed}",
            f"- command: {result.command}",
            f"- exit_code: {result.exit_code}",
            f"- duration_ms: {result.duration_ms}",
            "",
            "## stdout",
            "",
            result.stdout or "(empty)",
            "",
            "## stderr",
            "",
            result.stderr or "(empty)",
            "",
        ]
        return "\n".join(sections)

    def _build_acceptance_report(
        self,
        *,
        job: BuildJob,
        verification_passed: bool,
    ) -> dict[str, Any]:
        implementation_summary = self._implementation_summary(job)
        by_evaluator: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        structured_count = 0
        for criterion in job.acceptance.criteria:
            by_evaluator[criterion.evaluator.value] = (
                by_evaluator.get(criterion.evaluator.value, 0) + 1
            )
            by_kind[criterion.kind.value] = by_kind.get(criterion.kind.value, 0) + 1
            if criterion.metadata:
                structured_count += 1
        criteria_by_status = {
            "met": [
                criterion.to_dict()
                for criterion in job.acceptance.criteria
                if criterion.status == CriterionStatus.MET
            ],
            "unmet": [
                criterion.to_dict()
                for criterion in job.acceptance.criteria
                if criterion.status == CriterionStatus.UNMET
            ],
            "skipped": [
                criterion.to_dict()
                for criterion in job.acceptance.criteria
                if criterion.status == CriterionStatus.SKIPPED
            ],
            "pending": [
                criterion.to_dict()
                for criterion in job.acceptance.criteria
                if criterion.status == CriterionStatus.PENDING
            ],
        }
        return {
            **job.acceptance.to_dict(),
            "report_kind": "delivery_acceptance",
            "verification_passed": verification_passed,
            "review_verdict": job.post_build_review_verdict or "not_requested",
            "implementation_summary": implementation_summary,
            "criteria_by_status": criteria_by_status,
            "met_criteria": criteria_by_status["met"],
            "unmet_criteria": criteria_by_status["unmet"],
            "blocking_unmet_criteria": [
                criterion.to_dict()
                for criterion in job.acceptance.criteria
                if criterion.required and criterion.status == CriterionStatus.UNMET
            ],
            "optional_unmet_criteria": [
                criterion.to_dict()
                for criterion in job.acceptance.criteria
                if not criterion.required and criterion.status == CriterionStatus.UNMET
            ],
            "delivery_summary": {
                "accepted": job.acceptance.accepted,
                "summary": job.acceptance.summary,
                "met_count": job.acceptance.met_count,
                "unmet_count": job.acceptance.unmet_count,
                "total": job.acceptance.total,
                "required_total": job.acceptance.required_total,
                "required_met_count": job.acceptance.required_met_count,
                "required_unmet_count": job.acceptance.required_unmet_count,
                "optional_total": job.acceptance.optional_total,
                "optional_met_count": job.acceptance.optional_met_count,
                "optional_unmet_count": job.acceptance.optional_unmet_count,
                "structured_count": structured_count,
                "by_evaluator": by_evaluator,
                "by_kind": by_kind,
                "implementation": implementation_summary,
            },
        }

    def _has_test_surface(self, workspace_path: Path) -> bool:
        return bool(self._test_surface_details(workspace_path))

    def _has_lint_surface(self, workspace_path: Path) -> bool:
        return bool(self._lint_surface_details(workspace_path))

    def _has_typecheck_surface(self, workspace_path: Path) -> bool:
        return bool(self._typecheck_surface_details(workspace_path))

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

            if self._evaluate_structured_acceptance_criterion(
                job=job,
                criterion=criterion,
                normalized=normalized,
                workspace_path=workspace_path,
                change_set=change_set,
            ):
                continue

            if criterion.evaluator == CriterionEvaluator.VERIFY_COMMAND:
                self._evaluate_verify_command(
                    job=job,
                    criterion=criterion,
                    workspace_path=workspace_path,
                )
                continue

            if criterion.evaluator == CriterionEvaluator.VERIFICATION:
                self._evaluate_explicit_verification_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                )
                continue

            if criterion.evaluator == CriterionEvaluator.REVIEW:
                self._evaluate_review_backed_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                )
                continue

            if criterion.evaluator == CriterionEvaluator.CHANGE_SET:
                self._evaluate_change_backed_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                    change_set=change_set,
                )
                continue

            if criterion.evaluator == CriterionEvaluator.WORKSPACE:
                self._evaluate_workspace_backed_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                    workspace_path=workspace_path,
                    change_set=change_set,
                )
                continue

            if criterion.evaluator == CriterionEvaluator.IMPLEMENTATION:
                self._evaluate_implementation_backed_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                )
                continue

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
                self._evaluate_workspace_backed_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                    workspace_path=workspace_path,
                    change_set=change_set,
                )
                continue

            if (
                "implementation" in normalized
                or "mutation" in normalized
                or "operation" in normalized
                or "engine" in normalized
            ):
                self._evaluate_implementation_backed_criterion(
                    job=job,
                    criterion=criterion,
                    normalized=normalized,
                )
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

    def _evaluate_structured_acceptance_criterion(
        self,
        *,
        job: BuildJob,
        criterion: AcceptanceCriterion,
        normalized: str,
        workspace_path: str,
        change_set: dict[str, Any],
    ) -> bool:
        metadata = criterion.metadata or {}
        if not metadata:
            return False

        workspace_keys = {
            "path",
            "paths",
            "must_exist",
            "must_change",
            "contains_text",
            "not_contains_text",
            "json_path",
            "expected_value",
            "value",
        }
        change_keys = {
            "required_paths",
            "minimum_changed_files",
            "docs_required",
            "forbid_internal_changes",
        }
        review_keys = {
            "max_critical",
            "max_high",
            "max_total_findings",
            "review_verdict",
            "verdict",
        }
        implementation_keys = {
            "minimum_changed_operations",
            "maximum_noop_operations",
            "required_operation_types",
            "required_changed_paths",
            "implementation_mode",
        }

        if criterion.evaluator == CriterionEvaluator.VERIFICATION or "verification_kind" in metadata:
            self._evaluate_explicit_verification_criterion(
                job=job,
                criterion=criterion,
                normalized=normalized,
            )
            return True
        if criterion.evaluator == CriterionEvaluator.REVIEW or any(
            key in metadata for key in review_keys
        ):
            self._evaluate_review_backed_criterion(
                job=job,
                criterion=criterion,
                normalized=normalized,
            )
            return True
        if criterion.evaluator == CriterionEvaluator.CHANGE_SET or any(
            key in metadata for key in change_keys
        ):
            self._evaluate_change_backed_criterion(
                job=job,
                criterion=criterion,
                normalized=normalized,
                change_set=change_set,
            )
            return True
        if criterion.evaluator == CriterionEvaluator.WORKSPACE or any(
            key in metadata for key in workspace_keys
        ):
            self._evaluate_workspace_backed_criterion(
                job=job,
                criterion=criterion,
                normalized=normalized,
                workspace_path=workspace_path,
                change_set=change_set,
            )
            return True
        if criterion.evaluator == CriterionEvaluator.IMPLEMENTATION or any(
            key in metadata for key in implementation_keys
        ):
            self._evaluate_implementation_backed_criterion(
                job=job,
                criterion=criterion,
                normalized=normalized,
            )
            return True
        return False

    def _evaluate_explicit_verification_criterion(
        self,
        *,
        job: BuildJob,
        criterion: AcceptanceCriterion,
        normalized: str,
    ) -> None:
        metadata = criterion.metadata or {}
        verification_kind = metadata.get("verification_kind", "")
        if verification_kind:
            try:
                kind = VerificationKind(str(verification_kind))
            except ValueError:
                criterion.fail(f"Unsupported verification_kind: {verification_kind}")
                return
            label = kind.value.capitalize()
            self._evaluate_verification_backed_criterion(
                criterion=criterion,
                result=self._find_verification_result(job, kind),
                label=label,
            )
            return
        if "typecheck" in normalized or "type check" in normalized or "mypy" in normalized:
            self._evaluate_verification_backed_criterion(
                criterion=criterion,
                result=self._find_verification_result(job, VerificationKind.TYPECHECK),
                label="Typecheck",
            )
            return
        if "lint" in normalized or "ruff" in normalized:
            self._evaluate_verification_backed_criterion(
                criterion=criterion,
                result=self._find_verification_result(job, VerificationKind.LINT),
                label="Lint",
            )
            return
        if "test" in normalized or "pytest" in normalized:
            self._evaluate_verification_backed_criterion(
                criterion=criterion,
                result=self._find_verification_result(job, VerificationKind.TEST),
                label="Tests",
            )
            return
        self._evaluate_quality_backed_criterion(job=job, criterion=criterion)

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

    def _build_workspace_copy_ignore(self, workspace: Path) -> Any:
        skip_names = {
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "venv",
            # Large dirs that should never be copied into workspaces
            ".git",
            "node_modules",
            ".idea",
            ".vscode",
            ".tools",
            ".claude",
            "workspaces",
            "data",
            ".tox",
            "dist",
            "build",
            ".eggs",
            "*.egg-info",
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
    ) -> VerificationResult | None:
        return next((result for result in job.verification_results if result.kind == kind), None)

    def _build_execution_policy(self, job: BuildJob) -> Any:
        from agent.control.policy import RuntimeActionRequest, evaluate_runtime_action

        decision = evaluate_runtime_action(RuntimeActionRequest(
            action_type="build",
            build_type=job.build_type.value if hasattr(job.build_type, "value") else str(job.build_type),
            source=job.source or job.intake.source or "manual",
            policy_overrides={
                "build_execution_policy_id": job.intake.execution_policy_id or "workspace_local_mutation",
            },
        ))
        return decision.resolved_policy

    def _build_environment_profile_id(self, job: BuildJob) -> str:
        return self._build_execution_policy(job).environment_profile_id or get_build_execution_policy().environment_profile_id

    def _review_gate_policy(self, job: BuildJob) -> Any:
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
            duration_ms=job.timing.duration_ms,
            retry_count=job.resume_count,
            failure_count=1 if job.status in {JobStatus.FAILED, JobStatus.BLOCKED} else 0,
            usage=job.usage,
            metadata={
                "build_type": job.build_type.value,
                "capability_id": job.capability_id,
                "build_execution_policy_id": self._build_execution_policy(job).id,
                "environment_profile_id": self._build_environment_profile_id(job),
                "phase": job.phase.value,
                "timing": job.timing.to_dict(),
                "checkpoints": [checkpoint.to_dict() for checkpoint in job.checkpoints],
                "verification_passed": job.verification_passed,
                "verification_results": [item.to_dict() for item in job.verification_results],
                "acceptance": job.acceptance.to_dict(),
                "implementation": self._implementation_metadata(job),
                "post_build_review": {
                    "requested": job.intake.run_post_build_review,
                    "job_id": job.post_build_review_job_id,
                    "verdict": job.post_build_review_verdict,
                    "finding_counts": job.post_build_review_findings,
                    "review_gate_policy_id": job.intake.review_gate_policy_id,
                },
                "delivery_policy_id": job.intake.delivery_policy_id,
                "error": job.error,
                "last_error": job.error,
                "codegen_fallback": bool(job.metadata.get("codegen_fallback")),
                "codegen_error": str(job.metadata.get("codegen_error", "")),
                "denial": dict(job.denial),
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

    def _evaluate_workspace_backed_criterion(
        self,
        *,
        job: BuildJob,
        criterion: AcceptanceCriterion,
        normalized: str,
        workspace_path: str,
        change_set: dict[str, Any],
    ) -> None:
        metadata = criterion.metadata or {}
        if not metadata:
            criterion.meet(self._workspace_execution_evidence(job))
            return

        workspace_root = Path(workspace_path)
        changed_lookup = {
            path.casefold()
            for path in change_set.get("deliverable_changed_files", [])
        }
        paths = self._metadata_paths(metadata)
        if not paths and job.intake.target_files:
            paths = list(job.intake.target_files)

        issues: list[str] = []
        evidence_parts: list[str] = [self._workspace_execution_evidence(job)]

        if metadata.get("must_change"):
            changed_targets = paths or list(change_set.get("deliverable_changed_files", []))
            missing_changes = [
                path for path in changed_targets if path.casefold() not in changed_lookup
            ]
            if missing_changes:
                issues.append(f"required changed path(s) missing: {', '.join(missing_changes)}")
            else:
                evidence_parts.append("required workspace path changes captured")

        if metadata.get("must_exist"):
            missing_paths = []
            for path in paths:
                try:
                    target = self._resolve_workspace_operation_path(workspace_root, path)
                except ValueError as e:
                    issues.append(str(e))
                    continue
                if not target.exists():
                    missing_paths.append(path)
            if missing_paths:
                issues.append(f"required path(s) missing: {', '.join(missing_paths)}")
            elif paths:
                evidence_parts.append("required workspace paths exist")

        contains_text = self._criterion_text_values(metadata.get("contains_text"))
        not_contains_text = self._criterion_text_values(metadata.get("not_contains_text"))
        if contains_text or not_contains_text:
            if not paths:
                issues.append("contains_text checks require path or paths metadata")
            else:
                for path in paths:
                    try:
                        target = self._resolve_workspace_operation_path(workspace_root, path)
                    except ValueError as e:
                        issues.append(str(e))
                        continue
                    if not target.exists():
                        issues.append(f"path does not exist for text checks: {path}")
                        continue
                    content = target.read_text(encoding="utf-8")
                    lowered = content.casefold()
                    missing_needles = [
                        needle for needle in contains_text if needle.casefold() not in lowered
                    ]
                    forbidden_needles = [
                        needle for needle in not_contains_text if needle.casefold() in lowered
                    ]
                    if missing_needles:
                        issues.append(
                            f"{path} is missing required text: {', '.join(missing_needles)}"
                        )
                    if forbidden_needles:
                        issues.append(
                            f"{path} still contains forbidden text: {', '.join(forbidden_needles)}"
                        )
                if not any("text" in issue for issue in issues):
                    evidence_parts.append("workspace text checks passed")

        json_path = metadata.get("json_path")
        if json_path:
            if len(paths) != 1:
                issues.append("json_path checks require exactly one path")
            else:
                try:
                    target = self._resolve_workspace_operation_path(workspace_root, paths[0])
                except ValueError as e:
                    issues.append(str(e))
                else:
                    if not target.exists():
                        issues.append(f"JSON path target does not exist: {paths[0]}")
                    else:
                        try:
                            payload = json.loads(target.read_text(encoding="utf-8"))
                        except json.JSONDecodeError as e:
                            issues.append(f"invalid JSON in {paths[0]}: {e}")
                        else:
                            value, found = self._resolve_json_value(
                                payload,
                                self._criterion_path_segments(json_path),
                            )
                            expected = metadata.get("expected_value", metadata.get("value"))
                            if not found:
                                issues.append(
                                    f"JSON path {'.'.join(self._criterion_path_segments(json_path))} not found"
                                )
                            elif value != expected:
                                issues.append(
                                    f"JSON path value mismatch for {paths[0]}: expected {expected!r}, got {value!r}"
                                )
                            else:
                                evidence_parts.append("workspace JSON check passed")

        evidence = "; ".join(evidence_parts)
        if issues:
            criterion.fail(f"{evidence}; {'; '.join(issues)}")
        else:
            criterion.meet(evidence)

    def _metadata_paths(self, metadata: dict[str, Any]) -> list[str]:
        raw = metadata.get("paths")
        if raw is None:
            raw = metadata.get("required_paths")
        if raw is None:
            raw = metadata.get("path", [])
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, list):
            return [str(item) for item in raw]
        return []

    def _criterion_text_values(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    def _criterion_path_segments(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [segment for segment in value.split(".") if segment]
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    def _resolve_json_value(self, payload: Any, path: list[str]) -> tuple[Any, bool]:
        current = payload
        for segment in path:
            if not isinstance(current, dict) or segment not in current:
                return None, False
            current = current[segment]
        return current, True

    def _evaluate_verification_backed_criterion(
        self,
        criterion: AcceptanceCriterion,
        result: Any,
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

    def _evaluate_quality_backed_criterion(self, *, job: BuildJob, criterion: AcceptanceCriterion) -> None:
        if not job.verification_results:
            criterion.fail("Quality verification did not run")
            return
        failed = [result.kind.value for result in job.verification_results if not result.passed]
        if failed:
            criterion.fail(f"Quality gates failed: {', '.join(failed)}")
            return
        passed = ", ".join(result.kind.value for result in job.verification_results)
        criterion.meet(f"Quality gates passed: {passed}")

    def _evaluate_implementation_backed_criterion(
        self,
        *,
        job: BuildJob,
        criterion: AcceptanceCriterion,
        normalized: str,
    ) -> None:
        summary = self._implementation_summary(job)
        metadata = criterion.metadata or {}
        issues: list[str] = []
        evidence_parts = [
            (
                f"implementation mode={summary['mode']}; "
                f"operations={summary['operation_count']}; "
                f"changed={summary['changed_operations']}; "
                f"noop={summary['noop_operations']}; "
                f"failed={summary['failed_operations']}"
            )
        ]

        minimum_changed = metadata.get("minimum_changed_operations")
        if minimum_changed is not None and summary["changed_operations"] < int(minimum_changed):
            issues.append(
                f"changed operations below threshold: {summary['changed_operations']} < {int(minimum_changed)}"
            )

        maximum_noop = metadata.get("maximum_noop_operations")
        if maximum_noop is not None and summary["noop_operations"] > int(maximum_noop):
            issues.append(
                f"noop operations exceed threshold: {summary['noop_operations']} > {int(maximum_noop)}"
            )

        required_operation_types = metadata.get("required_operation_types", [])
        if isinstance(required_operation_types, str):
            required_operation_types = [required_operation_types]
        operation_types = {
            result.operation_type.value
            for result in job.implementation_results
        }
        if required_operation_types:
            missing_types = [
                str(item)
                for item in required_operation_types
                if str(item) not in operation_types
            ]
            if missing_types:
                issues.append(
                    "required implementation operation type(s) missing: "
                    + ", ".join(missing_types)
                )
            else:
                evidence_parts.append("required operation types observed")

        required_changed_paths = metadata.get("required_changed_paths", [])
        if isinstance(required_changed_paths, str):
            required_changed_paths = [required_changed_paths]
        changed_lookup = {path.casefold() for path in summary["changed_paths"]}
        if required_changed_paths:
            missing_paths = [
                str(path)
                for path in required_changed_paths
                if str(path).casefold() not in changed_lookup
            ]
            if missing_paths:
                issues.append(
                    "required changed path(s) missing: " + ", ".join(missing_paths)
                )
            else:
                evidence_parts.append("required changed paths captured")

        required_mode = metadata.get("implementation_mode")
        if required_mode and summary["mode"] != str(required_mode):
            issues.append(
                f"implementation mode mismatch: expected {required_mode}, got {summary['mode']}"
            )

        if not metadata:
            if job.implementation_mode == BuildImplementationMode.AUDIT_MARKER_ONLY:
                criterion.meet("No structured implementation plan supplied; audit marker only.")
                return
            criterion.meet(
                f"Structured implementation engine ran {summary['operation_count']} operation(s) "
                f"with {summary['changed_operations']} changed operation(s)."
            )
            return

        if summary["failed_operations"] > 0:
            issues.append(
                f"{summary['failed_operations']} implementation operation(s) failed"
            )

        if "no noop" in normalized and summary["noop_operations"] > 0:
            issues.append(
                f"noop operations present: {summary['noop_operations']}"
            )
        if (
            "changed path" in normalized
            or "implementation path" in normalized
        ) and summary["changed_paths"]:
            evidence_parts.append(
                "changed paths=" + ", ".join(summary["changed_paths"][:5])
            )

        evidence = "; ".join(evidence_parts)
        if issues:
            criterion.fail(f"{evidence}; {'; '.join(issues)}")
        else:
            criterion.meet(evidence)

    def _evaluate_review_backed_criterion(
        self,
        *,
        job: BuildJob,
        criterion: AcceptanceCriterion,
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
        metadata = criterion.metadata or {}

        if metadata:
            max_critical = metadata.get("max_critical")
            max_high = metadata.get("max_high")
            max_total = metadata.get("max_total_findings")
            required_verdict = metadata.get("review_verdict", metadata.get("verdict"))
            conditions: list[bool] = []
            evidence = (
                f"review verdict={verdict}; critical={critical}; "
                f"high={high}; total_findings={total_findings}"
            )
            if max_critical is not None:
                conditions.append(critical <= int(max_critical))
            if max_high is not None:
                conditions.append(high <= int(max_high))
            if max_total is not None:
                conditions.append(total_findings <= int(max_total))
            if required_verdict:
                conditions.append(verdict == str(required_verdict))
            if conditions:
                if all(conditions):
                    criterion.meet(evidence)
                else:
                    criterion.fail(evidence)
                return

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
        criterion: AcceptanceCriterion,
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

        metadata = criterion.metadata or {}
        if metadata:
            required_paths = self._metadata_paths(metadata)
            minimum_changed = metadata.get("minimum_changed_files")
            docs_required = bool(metadata.get("docs_required", False))
            forbid_internal_changes = bool(metadata.get("forbid_internal_changes", False))
            changed_lookup = {path.casefold() for path in deliverable_changed}
            issues: list[str] = []
            if required_paths:
                missing = [
                    path for path in required_paths if path.casefold() not in changed_lookup
                ]
                if missing:
                    issues.append(f"missing changed path(s): {', '.join(missing)}")
            if minimum_changed is not None and len(deliverable_changed) < int(minimum_changed):
                issues.append(
                    f"expected at least {int(minimum_changed)} deliverable change(s), got {len(deliverable_changed)}"
                )
            if docs_required:
                docs_changed = any(
                    path.casefold().startswith("docs/") or path.casefold().endswith(".md")
                    for path in deliverable_changed
                )
                if not docs_changed:
                    issues.append("expected documentation changes")
            if forbid_internal_changes and internal_changed:
                issues.append(
                    f"unexpected internal workspace changes: {', '.join(internal_changed[:3])}"
                )
            if issues:
                criterion.fail(_evidence("; ".join(issues)))
            else:
                criterion.meet(_evidence("Structured change-set checks passed"))
            return

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
        criterion: AcceptanceCriterion,
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
        result: Any,
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

    def _format_command_evidence(self, command_text: str, result: Any) -> str:
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
    ) -> Any:
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

    def _resolve_capability(self, intake: BuildIntake) -> Any:
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
                detail = self._acceptance_failure_detail(job)
                job.error = detail
                job.denial = make_denial(
                    code="build_acceptance_unmet",
                    summary="Build failed acceptance evaluation",
                    detail=detail,
                    scope=job.id,
                    policy_id=job.intake.delivery_policy_id,
                    environment_profile_id=self._build_environment_profile_id(job),
                    suggested_action=(
                        "Adjust the workspace changes or acceptance criteria and rerun the build."
                    ),
                    metadata=self._acceptance_failure_metadata(job),
                ).to_dict()

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

    def _verification_artifact_summary(
        self,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        payload = artifact.get("content_json") or {}
        return {
            "artifact_id": artifact.get("id", ""),
            "report_kind": payload.get("report_kind", ""),
            "verification_kind": payload.get("verification_kind", ""),
            "passed": payload.get("passed"),
            "command": payload.get("command", ""),
            "exit_code": payload.get("exit_code"),
            "duration_ms": payload.get("duration_ms"),
            "step_index": payload.get("step_index"),
        }

    def _delivery_acceptance_summary(
        self,
        job: BuildJob,
        acceptance_payload: dict[str, Any],
    ) -> dict[str, Any]:
        delivery_summary = acceptance_payload.get("delivery_summary") or {}
        criteria_by_status = acceptance_payload.get("criteria_by_status") or {}
        return {
            "accepted": delivery_summary.get("accepted", job.acceptance.accepted),
            "summary": delivery_summary.get("summary", job.acceptance.summary),
            "met_count": delivery_summary.get("met_count", job.acceptance.met_count),
            "unmet_count": delivery_summary.get("unmet_count", job.acceptance.unmet_count),
            "total": delivery_summary.get("total", job.acceptance.total),
            "required_total": delivery_summary.get("required_total", job.acceptance.required_total),
            "required_met_count": delivery_summary.get(
                "required_met_count",
                job.acceptance.required_met_count,
            ),
            "required_unmet_count": delivery_summary.get(
                "required_unmet_count",
                job.acceptance.required_unmet_count,
            ),
            "optional_total": delivery_summary.get("optional_total", job.acceptance.optional_total),
            "optional_met_count": delivery_summary.get(
                "optional_met_count",
                job.acceptance.optional_met_count,
            ),
            "optional_unmet_count": delivery_summary.get(
                "optional_unmet_count",
                job.acceptance.optional_unmet_count,
            ),
            "structured_count": delivery_summary.get("structured_count", 0),
            "by_evaluator": delivery_summary.get("by_evaluator", {}),
            "by_kind": delivery_summary.get("by_kind", {}),
            "implementation": delivery_summary.get(
                "implementation",
                acceptance_payload.get("implementation_summary", {}),
            ),
            "verification_passed": acceptance_payload.get(
                "verification_passed",
                job.verification_passed,
            ),
            "review_verdict": acceptance_payload.get(
                "review_verdict",
                job.post_build_review_verdict or "not_requested",
            ),
            "criteria_by_status": criteria_by_status,
            "blocking_unmet_criteria": acceptance_payload.get(
                "blocking_unmet_criteria",
                [],
            ),
            "optional_unmet_criteria": acceptance_payload.get(
                "optional_unmet_criteria",
                [],
            ),
        }

    def _acceptance_failure_detail(self, job: BuildJob) -> str:
        blocking_unmet = [
            criterion
            for criterion in job.acceptance.criteria
            if criterion.required and criterion.status == CriterionStatus.UNMET
        ]
        if not blocking_unmet:
            return job.acceptance.summary

        details: list[str] = []
        for criterion in blocking_unmet[:3]:
            evidence = criterion.evidence or "no evidence captured"
            details.append(f"{criterion.description} ({evidence})")
        if len(blocking_unmet) > 3:
            details.append(f"+{len(blocking_unmet) - 3} more unmet required criteria")
        return "Required acceptance criteria unmet: " + "; ".join(details)

    def _acceptance_failure_metadata(self, job: BuildJob) -> dict[str, Any]:
        return {
            "required_unmet_count": job.acceptance.required_unmet_count,
            "optional_unmet_count": job.acceptance.optional_unmet_count,
            "criteria": [criterion.to_dict() for criterion in job.acceptance.criteria],
            "blocking_unmet_criteria": [
                criterion.to_dict()
                for criterion in job.acceptance.criteria
                if criterion.required and criterion.status == CriterionStatus.UNMET
            ],
            "optional_unmet_criteria": [
                criterion.to_dict()
                for criterion in job.acceptance.criteria
                if not criterion.required and criterion.status == CriterionStatus.UNMET
            ],
        }

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
        verification_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.get("artifact_kind") == ArtifactKind.VERIFICATION_REPORT.value
        ]
        verification_artifact = next(
            (
                artifact
                for artifact in verification_artifacts
                if (artifact.get("content_json") or {}).get("report_kind") == "suite"
            ),
            verification_artifacts[0] if verification_artifacts else None,
        )
        verification_step_artifacts = [
            artifact
            for artifact in verification_artifacts
            if (artifact.get("content_json") or {}).get("report_kind") == "step"
        ]
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
        acceptance_payload = (
            acceptance_artifact or {}
        ).get(
            "content_json",
            self._build_acceptance_report(
                job=job,
                verification_passed=job.verification_passed,
            ),
        )
        acceptance_summary = self._delivery_acceptance_summary(job, acceptance_payload)
        verification_payload = (
            verification_artifact or {}
        ).get("content_json", {"results": [result.to_dict() for result in job.verification_results]})
        verification_summaries = [
            self._verification_artifact_summary(artifact)
            for artifact in verification_artifacts
        ]

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
                "implementation_mode": job.implementation_mode.value,
                "implementation_operations": len(job.intake.implementation_plan),
                "operation_mix": self._operation_mix(job.intake.implementation_plan),
                "changed_operations": sum(
                    1 for result in job.implementation_results if result.changed
                ),
                "implementation_summary": self._implementation_summary(job),
                "verification_passed": job.verification_passed,
                "acceptance_accepted": job.acceptance.accepted,
                "structured_acceptance_criteria": acceptance_summary["structured_count"],
                "review_requested": job.intake.run_post_build_review,
                "review_verdict": job.post_build_review_verdict or "not_requested",
                "verification_artifacts": len(verification_artifacts),
                "verification_steps": len(verification_step_artifacts),
                "acceptance_unmet": acceptance_summary["unmet_count"],
                "files_changed": (patch_artifact or {}).get("content_json", {}).get("files_changed", 0),
                "deliverable_files_changed": (patch_artifact or {}).get("content_json", {}).get(
                    "deliverable_files_changed",
                    0,
                ),
            },
            payload={
                "verification_report": verification_payload,
                "verification_artifact_ids": [
                    artifact.get("id", "")
                    for artifact in verification_artifacts
                ],
                "verification_artifacts": verification_summaries,
                "acceptance_report": acceptance_payload,
                "acceptance_summary": acceptance_summary,
                "implementation": self._implementation_metadata(job),
                "implementation_summary": self._implementation_summary(job),
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
            denial = make_denial(
                code="build_delivery_job_missing",
                summary="Build delivery approval blocked",
                detail=f"Job '{job_id}' not found",
                scope=job_id,
                suggested_action="Check the build job id and rerun the delivery request.",
            )
            return {"error": denial.message, "denial": denial.to_dict()}
        if job.status != JobStatus.COMPLETED:
            denial = make_denial(
                code="build_delivery_not_completed",
                summary="Build delivery approval blocked",
                detail=f"Job '{job_id}' is {job.status.value}, not completed",
                scope=job_id,
                suggested_action="Wait for the build job to complete before requesting delivery approval.",
            )
            return {"error": denial.message, "denial": denial.to_dict()}

        bundle = self.get_delivery_bundle(job_id)
        if bundle is None:
            denial = make_denial(
                code="build_delivery_bundle_missing",
                summary="Build delivery approval blocked",
                detail=f"Delivery package for '{job_id}' could not be assembled",
                scope=job_id,
                suggested_action="Inspect the build artifacts and rerun the build if the delivery package is missing.",
            )
            return {"error": denial.message, "denial": denial.to_dict()}

        if self._approval_queue is None:
            # Deny-by-default: no approval queue → no external delivery.
            # AGENT_DEV_MODE bypass removed — policy enforcement must not
            # be environment-dependent. See v1.30.0 deployment hardening.
            denial = make_denial(
                code="build_delivery_denied_by_default",
                summary="Build delivery blocked by default",
                detail="No approval queue is configured for external delivery.",
                scope=bundle["bundle_id"],
                policy_id=job.intake.delivery_policy_id,
                environment_profile_id="delivery_export_only",
                suggested_action="Configure the approval queue before requesting external build delivery.",
            )
            return {
                "error": denial.message,
                "denial": denial.to_dict(),
                "job_id": job_id,
                "bundle_id": bundle["bundle_id"],
                "delivery_ready": False,
            }

        from agent.core.approval import ApprovalCategory

        delivery_policy = get_delivery_policy(job.intake.delivery_policy_id)
        required_approvals = self._delivery_required_approvals(job, bundle=bundle)
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
            required_approvals=required_approvals,
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
            "required_approvals": req.required_approvals,
            "delivery_ready": False,
        }

    def get_delivery_record(self, job_id: str) -> dict[str, Any] | None:
        """Return persisted delivery lifecycle state for a build job."""
        bundle_id = self._delivery_bundle_id(job_id)
        record = self._refresh_delivery_record(bundle_id)
        if record is None:
            return None
        return cast("dict[str, Any] | None", record.to_dict())

    def mark_delivery_handed_off(self, job_id: str, *, note: str = "") -> dict[str, Any]:
        """Record final handoff after approval."""
        bundle_id = self._delivery_bundle_id(job_id)
        record = self._refresh_delivery_record(bundle_id)
        if record is None:
            denial = make_denial(
                code="build_handoff_record_missing",
                summary="Build handoff blocked",
                detail=f"Delivery record not found for job '{job_id}'",
                scope=bundle_id,
                suggested_action="Rebuild the delivery bundle before marking handoff.",
            )
            return {"error": denial.message, "bundle_id": bundle_id, "denial": denial.to_dict()}
        if record.status not in {
            DeliveryLifecycleStatus.APPROVED,
            DeliveryLifecycleStatus.HANDED_OFF,
        }:
            denial = make_denial(
                code="build_handoff_not_approved",
                summary="Build handoff blocked",
                detail=(
                    f"Delivery record '{bundle_id}' is {record.status.value}, "
                    "not approved for handoff"
                ),
                scope=bundle_id,
                policy_id=self._delivery_record_policy_id(record, job_id=job_id),
                environment_profile_id="delivery_export_only",
                suggested_action="Approve the build delivery request before recording handoff.",
            )
            return {
                "error": denial.message,
                "bundle_id": bundle_id,
                "denial": denial.to_dict(),
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
        return cast("dict[str, Any]", record.to_dict())

    def _delivery_bundle_id(self, job_id: str) -> str:
        return f"build-delivery-{job_id}"

    def _delivery_record_policy_id(self, record: Any, *, job_id: str) -> str:
        for event in reversed(record.events):
            policy_id = str(event.metadata.get("delivery_policy_id", "")).strip()
            if policy_id:
                return policy_id
        job = self.load_job(job_id)
        if job is not None:
            return job.intake.delivery_policy_id
        return ""

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

    def _delivery_required_approvals(
        self,
        job: BuildJob,
        *,
        bundle: dict[str, Any],
    ) -> int:
        deliverable_files_changed = int(
            bundle.get("summary", {}).get("deliverable_files_changed", 0)
        )
        critical = int(job.post_build_review_findings.get("critical", 0))
        high = int(job.post_build_review_findings.get("high", 0))
        if job.build_type in {BuildJobType.INTEGRATION, BuildJobType.DEVOPS}:
            return 2
        if critical > 0 or high > 0:
            return 2
        if deliverable_files_changed >= 5:
            return 2
        return 1

    def _refresh_delivery_record(self, bundle_id: str) -> Any:
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
