"""
Agent Life Space — Unified Operator Intake

Shared intake model and routing logic for review/build product entrypoints.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, cast

from agent.build.capabilities import get_capability
from agent.build.models import (
    AcceptanceCriterion,
    BuildIntake,
    BuildJobType,
    BuildOperation,
)
from agent.control.acquisition import inspect_git_url
from agent.control.policy import (
    get_delivery_policy,
    get_review_gate_policy,
    select_review_execution_policy,
)
from agent.finance.budget_policy import BudgetPolicy
from agent.review.models import ReviewIntake, ReviewJobType


class OperatorWorkType(str, Enum):
    """Top-level work type that the operator can request."""

    AUTO = "auto"
    REVIEW = "review"
    BUILD = "build"


class PlanStepStatus(str, Enum):
    """Planner-only status for previewed work."""

    PLANNED = "planned"
    BLOCKED = "blocked"
    OPTIONAL = "optional"


class PlanPhase(str, Enum):
    """High-level phases in a planned execution slice."""

    QUALIFY = "qualify"
    REVIEW = "review"
    BUILD = "build"
    VERIFY = "verify"
    DELIVER = "deliver"


class IntakeSourceKind(str, Enum):
    """Where the intake points to."""

    LOCAL_REPO = "local_repo"
    GIT_URL = "git_url"
    UNKNOWN = "unknown"


@dataclass
class OperatorIntake:
    """Unified intake envelope for routing build/review work."""

    repo_path: str = ""
    git_url: str = ""
    diff_spec: str = ""
    work_type: OperatorWorkType = OperatorWorkType.AUTO
    build_type: BuildJobType = BuildJobType.IMPLEMENTATION
    description: str = ""
    requester: str = ""
    context: str = ""
    focus_areas: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    implementation_plan: list[BuildOperation] = field(default_factory=list)
    acceptance_criteria: list[str | AcceptanceCriterion | dict[str, Any]] = field(
        default_factory=list
    )
    run_post_build_review: bool = True
    max_files: int = 100
    project_id: str = ""  # Link this work to a Project for tracking

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.repo_path and not self.git_url:
            errors.append("repo_path or git_url is required")
        if self.repo_path and ".." in self.repo_path:
            errors.append("repo_path must not contain '..'")
        if self.max_files < 1:
            errors.append("max_files must be >= 1")
        if self.work_type == OperatorWorkType.BUILD and not self.description:
            errors.append("description is required for build routing")
        for operation in self.implementation_plan:
            errors.extend(operation.validate())
        for index, criterion in enumerate(self.acceptance_criteria):
            try:
                AcceptanceCriterion.from_input(criterion)
            except Exception as e:
                errors.append(f"acceptance_criteria[{index}] is invalid: {e}")
        return errors

    @property
    def source_kind(self) -> IntakeSourceKind:
        if self.repo_path:
            return IntakeSourceKind.LOCAL_REPO
        if self.git_url:
            return IntakeSourceKind.GIT_URL
        return IntakeSourceKind.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": self.repo_path,
            "git_url": self.git_url,
            "diff_spec": self.diff_spec,
            "work_type": self.work_type.value,
            "build_type": self.build_type.value,
            "description": self.description,
            "requester": self.requester,
            "context": self.context,
            "focus_areas": list(self.focus_areas),
            "target_files": list(self.target_files),
            "implementation_plan": [
                operation.to_dict() for operation in self.implementation_plan
            ],
            "acceptance_criteria": [
                AcceptanceCriterion.from_input(item).to_dict()
                for item in self.acceptance_criteria
            ],
            "run_post_build_review": self.run_post_build_review,
            "max_files": self.max_files,
            "project_id": self.project_id,
        }

    def normalized_acceptance_criteria(self) -> list[AcceptanceCriterion]:
        return [
            AcceptanceCriterion.from_input(item)
            for item in self.acceptance_criteria
        ]


@dataclass
class JobPlanStep:
    """Single planned step for an operator intake."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    phase: PlanPhase = PlanPhase.QUALIFY
    title: str = ""
    status: PlanStepStatus = PlanStepStatus.PLANNED
    detail: str = ""
    outputs: list[str] = field(default_factory=list)
    capability_ids: list[str] = field(default_factory=list)
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "phase": self.phase.value,
            "title": self.title,
            "status": self.status.value,
            "detail": self.detail,
            "outputs": list(self.outputs),
            "capability_ids": list(self.capability_ids),
            "blocking": self.blocking,
        }


@dataclass
class JobPlanPhase:
    """High-level phase summary for a planned execution."""

    phase: PlanPhase = PlanPhase.QUALIFY
    title: str = ""
    status: PlanStepStatus = PlanStepStatus.PLANNED
    summary: str = ""
    outputs: list[str] = field(default_factory=list)
    capability_ids: list[str] = field(default_factory=list)
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "title": self.title,
            "status": self.status.value,
            "summary": self.summary,
            "outputs": list(self.outputs),
            "capability_ids": list(self.capability_ids),
            "blocking": self.blocking,
        }


@dataclass
class JobPlanCapability:
    """Capability or planner profile assigned to a plan phase."""

    phase: PlanPhase = PlanPhase.QUALIFY
    capability_id: str = ""
    label: str = ""
    source: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "capability_id": self.capability_id,
            "label": self.label,
            "source": self.source,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


@dataclass
class JobPlanBudgetEnvelope:
    """Structured budget envelope for a planned execution."""

    tier: str = "small"
    estimated_cost_usd: float = 0.0
    within_budget: bool = True
    requires_approval: bool = False
    hard_cap_hit: bool = False
    soft_cap_hit: bool = False
    stop_loss_hit: bool = False
    warnings: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    policy_basis: str = "budget_policy"
    daily_remaining_usd: float = 0.0
    monthly_remaining_usd: float = 0.0
    daily_soft_remaining_usd: float = 0.0
    monthly_soft_remaining_usd: float = 0.0
    daily_stop_loss_remaining_usd: float = 0.0
    monthly_stop_loss_remaining_usd: float = 0.0
    single_tx_approval_cap_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "estimated_cost_usd": self.estimated_cost_usd,
            "within_budget": self.within_budget,
            "requires_approval": self.requires_approval,
            "hard_cap_hit": self.hard_cap_hit,
            "soft_cap_hit": self.soft_cap_hit,
            "stop_loss_hit": self.stop_loss_hit,
            "warnings": list(self.warnings),
            "rationale": list(self.rationale),
            "policy_basis": self.policy_basis,
            "daily_remaining_usd": self.daily_remaining_usd,
            "monthly_remaining_usd": self.monthly_remaining_usd,
            "daily_soft_remaining_usd": self.daily_soft_remaining_usd,
            "monthly_soft_remaining_usd": self.monthly_soft_remaining_usd,
            "daily_stop_loss_remaining_usd": self.daily_stop_loss_remaining_usd,
            "monthly_stop_loss_remaining_usd": self.monthly_stop_loss_remaining_usd,
            "single_tx_approval_cap_usd": self.single_tx_approval_cap_usd,
        }


@dataclass
class JobPlan:
    """Planner output for a qualified operator intake."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    resolved_work_type: OperatorWorkType = OperatorWorkType.REVIEW
    title: str = ""
    summary: str = ""
    scope_summary: str = ""
    acceptance_summary: dict[str, Any] = field(default_factory=dict)
    scope_size: str = "small"
    scope_signals: list[str] = field(default_factory=list)
    risk_level: str = "low"
    risk_factors: list[str] = field(default_factory=list)
    budget_envelope: str = "small"
    budget: JobPlanBudgetEnvelope = field(default_factory=JobPlanBudgetEnvelope)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    planned_artifacts: list[str] = field(default_factory=list)
    recommended_next_action: str = ""
    phases: list[JobPlanPhase] = field(default_factory=list)
    capability_assignments: list[JobPlanCapability] = field(default_factory=list)
    steps: list[JobPlanStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "resolved_work_type": self.resolved_work_type.value,
            "title": self.title,
            "summary": self.summary,
            "scope_summary": self.scope_summary,
            "acceptance_summary": dict(self.acceptance_summary),
            "scope_size": self.scope_size,
            "scope_signals": list(self.scope_signals),
            "risk_level": self.risk_level,
            "risk_factors": list(self.risk_factors),
            "budget_envelope": self.budget_envelope,
            "budget": self.budget.to_dict(),
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "planned_artifacts": list(self.planned_artifacts),
            "recommended_next_action": self.recommended_next_action,
            "phases": [phase.to_dict() for phase in self.phases],
            "capability_assignments": [
                assignment.to_dict() for assignment in self.capability_assignments
            ],
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass
class IntakeQualification:
    """Result of qualifying unified operator intake."""

    supported: bool
    requested_work_type: OperatorWorkType
    resolved_work_type: OperatorWorkType
    source_kind: IntakeSourceKind
    normalized_repo_path: str = ""
    scope_size: str = "small"
    scope_signals: list[str] = field(default_factory=list)
    risk_level: str = "low"
    risk_factors: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "requested_work_type": self.requested_work_type.value,
            "resolved_work_type": self.resolved_work_type.value,
            "source_kind": self.source_kind.value,
            "normalized_repo_path": self.normalized_repo_path,
            "scope_size": self.scope_size,
            "scope_signals": list(self.scope_signals),
            "risk_level": self.risk_level,
            "risk_factors": list(self.risk_factors),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
        }


class OperatorIntakeService:
    """Qualify and route unified operator intake into build/review jobs."""

    def __init__(
        self,
        *,
        budget_policy: BudgetPolicy | None = None,
        budget_status_provider: Any = None,
    ) -> None:
        self._budget_policy = budget_policy or BudgetPolicy()
        self._budget_status_provider = budget_status_provider

    def qualify(self, intake: OperatorIntake) -> IntakeQualification:
        self._normalize_intake_enums(intake)
        errors = intake.validate()
        qualification = IntakeQualification(
            supported=not errors,
            requested_work_type=intake.work_type,
            resolved_work_type=intake.work_type,
            source_kind=intake.source_kind,
            blockers=list(errors),
        )

        if intake.repo_path:
            qualification.normalized_repo_path = str(Path(intake.repo_path).resolve())

        if intake.repo_path and intake.git_url:
            qualification.warnings.append(
                "Both repo_path and git_url were provided; local repo_path will be used."
            )

        if intake.source_kind == IntakeSourceKind.GIT_URL and not intake.repo_path:
            preview = inspect_git_url(intake.git_url)
            qualification.supported = preview.supported
            qualification.warnings.extend(preview.warnings)
            qualification.blockers.extend(preview.blockers)
            qualification.risk_level = preview.risk_level
            if not preview.supported:
                return qualification

        if qualification.blockers:
            qualification.supported = False
            return qualification

        resolved = self._resolve_work_type(intake)
        qualification.resolved_work_type = resolved
        scope = self._scope_profile(intake, resolved)
        qualification.scope_size = scope["size"]
        qualification.scope_signals = list(scope["signals"])
        qualification.reasons.extend(self._resolve_reasons(intake, resolved))
        risk_level, risk_factors = self._assess_risk(intake, resolved, scope)
        qualification.risk_level = risk_level
        qualification.risk_factors = risk_factors

        if resolved == OperatorWorkType.BUILD:
            if not intake.acceptance_criteria:
                qualification.warnings.append(
                    "Build route has no acceptance criteria; the job may fail closed."
                )
            if not intake.description:
                qualification.supported = False
                qualification.blockers.append(
                    "Build routing requires a non-empty description."
                )
        return qualification

    def preview(self, intake: OperatorIntake) -> dict[str, Any]:
        """Return qualification plus a first-class JobPlan preview."""
        qualification = self.qualify(intake)
        plan = self.create_plan(intake, qualification=qualification)
        return {
            "accepted": qualification.supported,
            "qualification": qualification.to_dict(),
            "plan": plan.to_dict(),
        }

    def to_review_intake(self, intake: OperatorIntake) -> ReviewIntake:
        self._normalize_intake_enums(intake)
        return ReviewIntake(
            repo_path=intake.repo_path,
            diff_spec=intake.diff_spec,
            review_type=(
                ReviewJobType.PR_REVIEW if intake.diff_spec else ReviewJobType.REPO_AUDIT
            ),
            focus_areas=list(intake.focus_areas),
            max_files=intake.max_files,
            include_patterns=list(intake.target_files),
            requester=intake.requester,
            context=intake.context or intake.description or "",
            source="operator",
            project_id=intake.project_id,
        )

    def to_build_intake(self, intake: OperatorIntake) -> BuildIntake:
        self._normalize_intake_enums(intake)
        build_capability = self._build_phase_capability(intake)
        review_gate_policy = self._build_review_gate_policy(intake)
        delivery_policy = self._build_delivery_policy(intake)
        return BuildIntake(
            repo_path=intake.repo_path,
            build_type=intake.build_type,
            capability_id=build_capability.capability_id,
            execution_policy_id="workspace_local_mutation",
            description=intake.description,
            target_files=list(intake.target_files),
            implementation_plan=[
                BuildOperation.from_dict(operation.to_dict())
                for operation in intake.implementation_plan
            ],
            acceptance_criteria=[
                AcceptanceCriterion.from_input(item)
                for item in intake.acceptance_criteria
            ],
            run_post_build_review=intake.run_post_build_review,
            block_on_review_failure=not review_gate_policy.advisory_only,
            review_gate_policy_id=review_gate_policy.id,
            delivery_policy_id=delivery_policy.id,
            source="operator",
            requester=intake.requester,
            context=intake.context,
            project_id=intake.project_id,
        )

    def create_plan(
        self,
        intake: OperatorIntake,
        *,
        qualification: IntakeQualification | None = None,
    ) -> JobPlan:
        """Create a planner-grade execution outline for this intake."""
        qualification = qualification or self.qualify(intake)
        self._normalize_intake_enums(intake)
        resolved = qualification.resolved_work_type
        capability_assignments = self._select_capabilities(intake, qualification)
        budget = self._build_budget_envelope(intake, qualification)
        phases = self._build_plan_phases(
            intake,
            qualification,
            capability_assignments=capability_assignments,
        )
        steps = self._build_plan_steps(
            intake,
            qualification,
            capability_assignments=capability_assignments,
        )
        return JobPlan(
            resolved_work_type=resolved,
            title=self._build_plan_title(intake, resolved),
            summary=self._build_plan_summary(intake, qualification),
            scope_summary=self._build_scope_summary(intake),
            acceptance_summary=self._build_acceptance_summary(intake),
            scope_size=qualification.scope_size,
            scope_signals=list(qualification.scope_signals),
            risk_level=qualification.risk_level,
            risk_factors=list(qualification.risk_factors),
            budget_envelope=budget.tier,
            budget=budget,
            warnings=list(qualification.warnings),
            blockers=list(qualification.blockers),
            planned_artifacts=self._planned_artifacts(intake, resolved),
            recommended_next_action=self._recommended_next_action(
                qualification, resolved, budget
            ),
            phases=phases,
            capability_assignments=capability_assignments,
            steps=steps,
        )

    def _resolve_work_type(self, intake: OperatorIntake) -> OperatorWorkType:
        if intake.work_type != OperatorWorkType.AUTO:
            return intake.work_type
        if intake.diff_spec:
            return OperatorWorkType.REVIEW
        if intake.acceptance_criteria or intake.target_files or intake.implementation_plan:
            return OperatorWorkType.BUILD
        description = intake.description.lower()
        build_hints = ("implement", "build", "fix", "refactor", "add", "change")
        if any(hint in description for hint in build_hints):
            return OperatorWorkType.BUILD
        return OperatorWorkType.REVIEW

    def _normalize_intake_enums(self, intake: OperatorIntake) -> None:
        if not isinstance(intake.work_type, OperatorWorkType):
            intake.work_type = OperatorWorkType(str(intake.work_type))
        if not isinstance(intake.build_type, BuildJobType):
            intake.build_type = BuildJobType(str(intake.build_type))

    def _resolve_reasons(
        self,
        intake: OperatorIntake,
        resolved: OperatorWorkType,
    ) -> list[str]:
        reasons: list[str] = []
        acceptance = self._build_acceptance_summary(intake)
        if intake.diff_spec:
            reasons.append("diff_spec present, so review routing is supported.")
        if intake.target_files:
            reasons.append(
                f"target_files present ({len(intake.target_files)}), so scoped execution is available."
            )
        if intake.implementation_plan:
            reasons.append(
                f"implementation_plan present ({len(intake.implementation_plan)} operations), so the builder can run bounded local mutations."
            )
        if intake.acceptance_criteria:
            reasons.append(
                "acceptance_criteria present "
                f"({acceptance['total']} total; {acceptance['required']} required; "
                f"{acceptance['optional']} optional), so build routing is meaningful."
            )
        if intake.git_url and not intake.repo_path:
            reasons.append("git_url can be acquired into a managed local mirror before runtime execution.")
        if resolved == OperatorWorkType.BUILD and intake.run_post_build_review:
            reasons.append("build route will request deterministic post-build review.")
        if not reasons:
            reasons.append("defaulted to lightweight repository review routing.")
        return reasons

    def _build_plan_steps(
        self,
        intake: OperatorIntake,
        qualification: IntakeQualification,
        *,
        capability_assignments: list[JobPlanCapability],
    ) -> list[JobPlanStep]:
        blocked = not qualification.supported
        status = PlanStepStatus.BLOCKED if blocked else PlanStepStatus.PLANNED
        capabilities_by_phase = self._capability_ids_by_phase(capability_assignments)
        steps = [
            JobPlanStep(
                phase=PlanPhase.QUALIFY,
                title="Qualify intake and normalize scope",
                status=status,
                detail=(
                    "Resolve route, source kind, and blockers before runtime execution."
                ),
                outputs=["qualification", "normalized_repo_path"],
                capability_ids=capabilities_by_phase.get(PlanPhase.QUALIFY, []),
            )
        ]

        if qualification.resolved_work_type == OperatorWorkType.BUILD:
            operation_count = len(intake.implementation_plan)
            acceptance = self._build_acceptance_summary(intake)
            build_detail = (
                f"Apply {operation_count} structured workspace operation(s) through the bounded local engine."
                if operation_count
                else "Execute the current builder capability in the workspace. Without a structured implementation plan this remains audit-only."
            )
            verify_detail = (
                f"Run verification defaults and evaluate {acceptance['total']} acceptance criterion/criteria "
                f"({acceptance['required']} required, {acceptance['optional']} optional)."
                if acceptance["total"]
                else "Run verification defaults and use the verification outcome as the acceptance proxy."
            )
            steps.extend(
                [
                    JobPlanStep(
                        phase=PlanPhase.BUILD,
                        title="Prepare workspace and sync repository",
                        status=status,
                        detail="Materialize the requested repo into a managed workspace.",
                        outputs=["workspace", "repo_sync_trace"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.BUILD, []),
                    ),
                    JobPlanStep(
                        phase=PlanPhase.BUILD,
                        title="Run deterministic build execution",
                        status=status,
                        detail=build_detail,
                        outputs=["build_trace", "implementation_results"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.BUILD, []),
                    ),
                    JobPlanStep(
                        phase=PlanPhase.VERIFY,
                        title="Verify and evaluate acceptance",
                        status=status,
                        detail=verify_detail,
                        outputs=["verification_report", "acceptance_report"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.VERIFY, []),
                    ),
                    JobPlanStep(
                        phase=PlanPhase.REVIEW,
                        title="Run post-build review gate",
                        status=(
                            status
                            if intake.run_post_build_review
                            else PlanStepStatus.OPTIONAL
                        ),
                        detail=(
                            "Use ReviewService to gate completion on deterministic review findings."
                            if intake.run_post_build_review
                            else "Post-build review is disabled for this intake."
                        ),
                        outputs=["review_report", "finding_list"]
                        if intake.run_post_build_review
                        else [],
                        capability_ids=capabilities_by_phase.get(PlanPhase.REVIEW, []),
                        blocking=intake.run_post_build_review,
                    ),
                    JobPlanStep(
                        phase=PlanPhase.DELIVER,
                        title="Capture delivery-grade artifacts",
                        status=status,
                        detail="Persist diff, trace, and supporting artifacts for recovery/query.",
                        outputs=["diff", "execution_trace"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.DELIVER, []),
                    ),
                ]
            )
        else:
            steps.extend(
                [
                    JobPlanStep(
                        phase=PlanPhase.REVIEW,
                        title="Analyze repository or diff scope",
                        status=status,
                        detail="Run the review workflow over the requested repo or diff range.",
                        outputs=["review_report", "finding_list"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.REVIEW, []),
                    ),
                    JobPlanStep(
                        phase=PlanPhase.VERIFY,
                        title="Verify and package review output",
                        status=status,
                        detail="Finalize report artifacts and client-safe recovery payloads.",
                        outputs=["review_report", "execution_trace"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.VERIFY, []),
                    ),
                    JobPlanStep(
                        phase=PlanPhase.DELIVER,
                        title="Prepare operator handoff bundle",
                        status=status,
                        detail="Prepare a delivery-safe artifact bundle for operator handoff.",
                        outputs=["executive_summary", "review_report"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.DELIVER, []),
                    ),
                ]
            )
        return steps

    def _build_plan_phases(
        self,
        intake: OperatorIntake,
        qualification: IntakeQualification,
        *,
        capability_assignments: list[JobPlanCapability],
    ) -> list[JobPlanPhase]:
        blocked = not qualification.supported
        status = PlanStepStatus.BLOCKED if blocked else PlanStepStatus.PLANNED
        capabilities_by_phase = self._capability_ids_by_phase(capability_assignments)
        phases = [
            JobPlanPhase(
                phase=PlanPhase.QUALIFY,
                title="Qualification",
                status=status,
                summary=(
                    f"Normalize source, scope, and route as a {qualification.resolved_work_type.value} request."
                ),
                outputs=["qualification"],
                capability_ids=capabilities_by_phase.get(PlanPhase.QUALIFY, []),
            )
        ]
        if qualification.resolved_work_type == OperatorWorkType.BUILD:
            operation_count = len(intake.implementation_plan)
            acceptance = self._build_acceptance_summary(intake)
            build_summary = (
                f"Prepare a managed workspace and apply {operation_count} structured builder operation(s)."
                if operation_count
                else "Prepare a managed workspace and run the selected builder capability in audit-only mode."
            )
            phases.extend(
                [
                    JobPlanPhase(
                        phase=PlanPhase.BUILD,
                        title="Build",
                        status=status,
                        summary=build_summary,
                        outputs=["workspace", "build_trace", "implementation_results"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.BUILD, []),
                    ),
                    JobPlanPhase(
                        phase=PlanPhase.VERIFY,
                        title="Verify",
                        status=status,
                        summary=(
                            f"Run verification defaults and evaluate {acceptance['total']} acceptance criterion/criteria "
                            f"({acceptance['required']} required, {acceptance['optional']} optional)."
                        ),
                        outputs=["verification_report", "acceptance_report"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.VERIFY, []),
                    ),
                    JobPlanPhase(
                        phase=PlanPhase.REVIEW,
                        title="Review Gate",
                        status=(
                            status
                            if intake.run_post_build_review
                            else PlanStepStatus.OPTIONAL
                        ),
                        summary=(
                            "Apply deterministic post-build review before completion."
                            if intake.run_post_build_review
                            else "Post-build review is disabled for this request."
                        ),
                        outputs=["review_report", "finding_list"]
                        if intake.run_post_build_review
                        else [],
                        capability_ids=capabilities_by_phase.get(PlanPhase.REVIEW, []),
                        blocking=intake.run_post_build_review,
                    ),
                    JobPlanPhase(
                        phase=PlanPhase.DELIVER,
                        title="Deliver",
                        status=status,
                        summary=(
                            "Persist delivery-safe build artifacts for recovery and operator handoff."
                        ),
                        outputs=["diff", "execution_trace"],
                        capability_ids=capabilities_by_phase.get(PlanPhase.DELIVER, []),
                    ),
                ]
            )
            return phases

        phases.extend(
            [
                JobPlanPhase(
                    phase=PlanPhase.REVIEW,
                    title="Review",
                    status=status,
                    summary=(
                        "Analyze repository or diff scope through the review workflow."
                    ),
                    outputs=["review_report", "finding_list"],
                    capability_ids=capabilities_by_phase.get(PlanPhase.REVIEW, []),
                ),
                JobPlanPhase(
                    phase=PlanPhase.VERIFY,
                    title="Verify",
                    status=status,
                    summary=(
                        "Finalize and verify recovery-safe review artifacts."
                    ),
                    outputs=["review_report", "execution_trace"],
                    capability_ids=capabilities_by_phase.get(PlanPhase.VERIFY, []),
                ),
                JobPlanPhase(
                    phase=PlanPhase.DELIVER,
                    title="Deliver",
                    status=status,
                    summary=(
                        "Prepare operator handoff artifacts and recommended summary."
                    ),
                    outputs=["executive_summary", "review_report"],
                    capability_ids=capabilities_by_phase.get(PlanPhase.DELIVER, []),
                ),
            ]
        )
        return phases

    def _build_plan_title(
        self,
        intake: OperatorIntake,
        resolved: OperatorWorkType,
    ) -> str:
        subject = intake.description or intake.context or intake.diff_spec or intake.repo_path
        if resolved == OperatorWorkType.BUILD:
            return f"Build plan: {subject or intake.build_type.value}"
        return f"Review plan: {subject or 'repository review'}"

    def _build_plan_summary(
        self,
        intake: OperatorIntake,
        qualification: IntakeQualification,
    ) -> str:
        subject = qualification.normalized_repo_path or intake.git_url or "(unknown source)"
        if not qualification.supported:
            return (
                f"Plan is blocked for {subject}. Resolve blockers before execution can start."
            )
        if qualification.resolved_work_type == OperatorWorkType.BUILD:
            operation_count = len(intake.implementation_plan)
            acceptance = self._build_acceptance_summary(intake)
            if operation_count:
                return (
                    f"Execute a {intake.build_type.value} build over {subject}, "
                    f"apply {operation_count} structured workspace operation(s), "
                    f"verify it, evaluate {acceptance['total']} acceptance criterion/criteria, "
                    "and capture recovery-safe artifacts."
                )
            return (
                f"Execute a {intake.build_type.value} build over {subject}, "
                f"verify it, evaluate {acceptance['total']} acceptance criterion/criteria, "
                "and capture recovery-safe artifacts."
            )
        if intake.diff_spec:
            return (
                f"Review diff scope `{intake.diff_spec}` in {subject} and package the findings."
            )
        return f"Review repository state in {subject} and package the findings."

    def _build_scope_summary(self, intake: OperatorIntake) -> str:
        parts = []
        if intake.repo_path:
            parts.append(f"repo={Path(intake.repo_path).resolve()}")
        elif intake.git_url:
            parts.append(f"git_url={intake.git_url}")
        if intake.diff_spec:
            parts.append(f"diff={intake.diff_spec}")
        if intake.target_files:
            parts.append(f"targets={len(intake.target_files)}")
        if intake.implementation_plan:
            parts.append(f"implementation_ops={len(intake.implementation_plan)}")
        if intake.acceptance_criteria:
            acceptance = self._build_acceptance_summary(intake)
            parts.append(
                "acceptance="
                f"{acceptance['total']}(required={acceptance['required']},"
                f"optional={acceptance['optional']},structured={acceptance['structured']})"
            )
        if intake.focus_areas:
            parts.append(f"focus={len(intake.focus_areas)}")
        if not parts:
            return "no explicit scope signals"
        return "; ".join(parts)

    def _build_acceptance_summary(self, intake: OperatorIntake) -> dict[str, Any]:
        criteria = intake.normalized_acceptance_criteria()
        by_evaluator: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for criterion in criteria:
            by_evaluator[criterion.evaluator.value] = (
                by_evaluator.get(criterion.evaluator.value, 0) + 1
            )
            by_kind[criterion.kind.value] = by_kind.get(criterion.kind.value, 0) + 1
        return {
            "total": len(criteria),
            "required": sum(1 for criterion in criteria if criterion.required),
            "optional": sum(1 for criterion in criteria if not criterion.required),
            "structured": sum(1 for criterion in criteria if criterion.metadata),
            "by_evaluator": by_evaluator,
            "by_kind": by_kind,
            "criteria": [criterion.to_dict() for criterion in criteria],
        }

    def _scope_profile(
        self,
        intake: OperatorIntake,
        resolved: OperatorWorkType,
    ) -> dict[str, Any]:
        score = 0
        signals: list[str] = []
        acceptance = self._build_acceptance_summary(intake)
        if intake.repo_path:
            signals.append("local repository path provided")
        elif intake.git_url:
            signals.append("git_url source provided for managed acquisition")
            score += 1
        if intake.diff_spec:
            signals.append("explicit diff scope provided")
            score += 2
        if intake.target_files:
            signals.append(f"{len(intake.target_files)} target file(s) requested")
            score += min(len(intake.target_files), 4)
        if intake.implementation_plan:
            signals.append(
                f"{len(intake.implementation_plan)} structured implementation operation(s) declared"
            )
            score += min(len(intake.implementation_plan), 4)
        if intake.acceptance_criteria:
            signals.append(
                f"{acceptance['total']} acceptance criterion/criteria declared"
            )
            score += min(acceptance["total"], 4)
            if acceptance["structured"]:
                signals.append(
                    f"{acceptance['structured']} acceptance criterion/criteria carry structured metadata"
                )
                score += min(acceptance["structured"], 2)
        if intake.focus_areas:
            signals.append(f"{len(intake.focus_areas)} focus area(s) supplied")
            score += min(len(intake.focus_areas), 3)
        if resolved == OperatorWorkType.BUILD:
            signals.append(f"mutable build route selected ({intake.build_type.value})")
            score += 2
        if resolved == OperatorWorkType.BUILD and intake.run_post_build_review:
            signals.append("post-build review gate requested")
            score += 1
        if score <= 2:
            size = "small"
        elif score <= 6:
            size = "medium"
        else:
            size = "large"
        if not signals:
            signals.append("default lightweight repository review signals only")
        return {"score": score, "size": size, "signals": signals}

    def _assess_risk(
        self,
        intake: OperatorIntake,
        resolved: OperatorWorkType,
        scope: dict[str, Any],
    ) -> tuple[str, list[str]]:
        factors: list[str] = []
        acceptance = self._build_acceptance_summary(intake)
        if resolved == OperatorWorkType.BUILD:
            factors.append("mutable workspace execution is required")
        if resolved == OperatorWorkType.BUILD and intake.build_type in {
            BuildJobType.INTEGRATION,
            BuildJobType.DEVOPS,
        }:
            factors.append(
                f"{intake.build_type.value} work touches higher-impact cross-system surfaces"
            )
        if intake.run_post_build_review and resolved == OperatorWorkType.BUILD:
            factors.append("completion depends on a post-build review gate")
        if intake.diff_spec and resolved == OperatorWorkType.REVIEW:
            factors.append("review is constrained to an explicit diff range")
        if not intake.diff_spec and resolved == OperatorWorkType.REVIEW:
            factors.append("review is repo-wide rather than diff-scoped")
        if scope["size"] == "large":
            factors.append("scope exceeds the lightweight execution envelope")
        if len(intake.target_files) >= 5:
            factors.append("target set spans multiple files")
        if len(intake.implementation_plan) >= 4:
            factors.append("implementation plan spans multiple bounded mutations")
        if acceptance["total"] >= 4:
            factors.append("acceptance surface is broad")
        if acceptance["structured"] >= 2:
            factors.append("acceptance uses multiple structured deterministic checks")

        if resolved == OperatorWorkType.REVIEW and intake.diff_spec and scope["size"] == "small":
            return "low", factors
        if resolved == OperatorWorkType.BUILD and (
            scope["size"] == "large"
            or intake.build_type in {BuildJobType.INTEGRATION, BuildJobType.DEVOPS}
        ):
            return "high", factors
        return "medium", factors

    def _build_budget_envelope(
        self,
        intake: OperatorIntake,
        qualification: IntakeQualification,
    ) -> JobPlanBudgetEnvelope:
        estimated_cost = self._estimate_planned_cost_usd(intake, qualification)
        budget_status = self._get_budget_status(estimated_cost)
        daily_spent = float(budget_status.get("daily_spent", 0.0))
        monthly_spent = float(budget_status.get("monthly_spent", 0.0))
        policy_result = self._budget_policy.check(
            estimated_cost,
            daily_spent=daily_spent,
            monthly_spent=monthly_spent,
        )
        forecast = self._budget_policy.get_forecast(
            daily_spent=daily_spent,
            monthly_spent=monthly_spent,
        )
        acceptance = self._build_acceptance_summary(intake)
        rationale = [
            f"resolved_work_type={qualification.resolved_work_type.value}",
            f"scope_size={qualification.scope_size}",
            f"target_files={len(intake.target_files)}",
            f"implementation_operations={len(intake.implementation_plan)}",
            f"acceptance_criteria={acceptance['total']}",
        ]
        if acceptance["structured"]:
            rationale.append(f"structured_acceptance={acceptance['structured']}")
        if intake.run_post_build_review and qualification.resolved_work_type == OperatorWorkType.BUILD:
            rationale.append("post_build_review=true")
        if intake.git_url and not intake.repo_path:
            rationale.append("git_url_acquisition=true")
        within_budget = bool(
            budget_status.get("within_budget", True)
            and policy_result.allowed
        )
        return JobPlanBudgetEnvelope(
            tier=qualification.scope_size,
            estimated_cost_usd=estimated_cost,
            within_budget=within_budget,
            requires_approval=policy_result.requires_approval,
            hard_cap_hit=policy_result.hard_cap_hit,
            soft_cap_hit=policy_result.soft_cap_hit,
            stop_loss_hit=policy_result.stop_loss_hit,
            warnings=list(policy_result.warnings),
            rationale=rationale,
            daily_remaining_usd=round(float(budget_status.get("daily_remaining", 0.0)), 2),
            monthly_remaining_usd=round(float(budget_status.get("monthly_remaining", 0.0)), 2),
            daily_soft_remaining_usd=round(float(forecast["daily"]["soft_remaining"]), 2),
            monthly_soft_remaining_usd=round(float(forecast["monthly"]["soft_remaining"]), 2),
            daily_stop_loss_remaining_usd=round(
                float(forecast["daily"]["stop_loss_remaining"]),
                2,
            ),
            monthly_stop_loss_remaining_usd=round(
                float(forecast["monthly"]["stop_loss_remaining"]),
                2,
            ),
            single_tx_approval_cap_usd=round(
                float(forecast.get("single_tx_approval_cap", 0.0)),
                2,
            ),
        )

    def _estimate_planned_cost_usd(
        self,
        intake: OperatorIntake,
        qualification: IntakeQualification,
    ) -> float:
        acceptance = self._build_acceptance_summary(intake)
        amount = 1.5 if qualification.resolved_work_type == OperatorWorkType.REVIEW else 4.0
        amount += min(len(intake.target_files), 6) * 0.8
        amount += min(len(intake.implementation_plan), 8) * 0.7
        amount += min(acceptance["total"], 6) * 1.1
        amount += min(acceptance["structured"], 4) * 0.6
        amount += min(len(intake.focus_areas), 4) * 0.4
        if intake.diff_spec:
            amount += 1.0
        if qualification.scope_size == "medium":
            amount += 2.5
        elif qualification.scope_size == "large":
            amount += 6.0
        if qualification.resolved_work_type == OperatorWorkType.BUILD:
            amount += 2.0
            if intake.build_type == BuildJobType.INTEGRATION:
                amount += 4.0
            elif intake.build_type == BuildJobType.DEVOPS:
                amount += 3.0
            elif intake.build_type == BuildJobType.TESTING:
                amount += 1.0
        if intake.run_post_build_review and qualification.resolved_work_type == OperatorWorkType.BUILD:
            amount += 2.0
        return cast("float", round(amount, 2))

    def _get_budget_status(self, estimated_cost: float) -> dict[str, Any]:
        if not callable(self._budget_status_provider):
            return {
                "daily_spent": 0.0,
                "daily_remaining": self._budget_policy.limits.daily_hard_cap,
                "monthly_spent": 0.0,
                "monthly_remaining": self._budget_policy.limits.monthly_hard_cap,
                "within_budget": estimated_cost <= self._budget_policy.limits.daily_hard_cap,
            }
        return cast("dict[str, Any]", self._budget_status_provider(estimated_cost))

    def _select_capabilities(
        self,
        intake: OperatorIntake,
        qualification: IntakeQualification,
    ) -> list[JobPlanCapability]:
        assignments = [
            JobPlanCapability(
                phase=PlanPhase.QUALIFY,
                capability_id="intake_router_v1",
                label="Intake Router",
                source="planner_profile",
                reason="Qualification always passes through the unified intake router.",
            )
        ]
        if intake.git_url and not intake.repo_path:
            assignments.append(
                JobPlanCapability(
                    phase=PlanPhase.QUALIFY,
                    capability_id="repo_import_mirror",
                    label="Repo Import Mirror",
                    source="environment_profile",
                    reason="Acquire the supported git_url into a managed local mirror before runtime routing.",
                    metadata={"git_url": intake.git_url, "environment_profile_id": "repo_import_mirror"},
                )
            )
        if qualification.resolved_work_type == OperatorWorkType.BUILD:
            build_capability = self._build_phase_capability(intake)
            assignments.append(build_capability)
            assignments.append(
                JobPlanCapability(
                    phase=PlanPhase.VERIFY,
                    capability_id="verify_default_suite",
                    label="Verification Defaults",
                    source="planner_profile",
                    reason=(
                        f"Use {build_capability.metadata['verification_defaults']} as the default verification profile."
                    ),
                    metadata={
                        "verification_defaults": build_capability.metadata[
                            "verification_defaults"
                        ],
                        "acceptance_summary": self._build_acceptance_summary(intake),
                    },
                )
            )
            if intake.run_post_build_review:
                review_policy = self._build_review_gate_policy(intake)
                assignments.append(
                    JobPlanCapability(
                        phase=PlanPhase.REVIEW,
                        capability_id="review_repo_audit_v1",
                        label="Post-build Review",
                        source="planner_profile",
                        reason=(
                            "Build completion is gated by deterministic repo_audit review over the workspace."
                        ),
                    )
                )
                assignments.append(
                    JobPlanCapability(
                        phase=PlanPhase.REVIEW,
                        capability_id=review_policy.id,
                        label="Review Gate Policy",
                        source="policy_profile",
                        reason=(
                            "Build completion is gated by a deterministic review threshold policy."
                        ),
                        metadata={
                            "policy_id": review_policy.id,
                            "description": review_policy.description,
                            "environment_profile_id": "review_host_read_only",
                        },
                    )
                )
            delivery_policy = self._build_delivery_policy(intake)
            assignments.append(
                JobPlanCapability(
                    phase=PlanPhase.DELIVER,
                    capability_id=delivery_policy.id,
                    label="Delivery Policy",
                    source="policy_profile",
                    reason="Build delivery should remain approval-gated and queryable for operator handoff.",
                    metadata={
                        "approval_required": delivery_policy.approval_required,
                        "allow_external_send": delivery_policy.allow_external_send,
                        "gateway_required": delivery_policy.gateway_required,
                        "environment_profile_id": "delivery_export_only",
                    },
                )
            )
            return assignments

        review_type = "pr_review_v1" if intake.diff_spec else "repo_audit_v1"
        review_policy = select_review_execution_policy(
            review_type=intake.diff_spec and "pr_review" or "repo_audit",
            diff_spec=intake.diff_spec,
            source="telegram",
        )
        delivery_policy = get_delivery_policy("approval_required")
        assignments.extend(
            [
                JobPlanCapability(
                    phase=PlanPhase.REVIEW,
                    capability_id=review_type,
                    label="Review Workflow",
                    source="execution_policy",
                    reason=(
                        "Diff-scoped review selected."
                        if intake.diff_spec
                        else "Repository-wide review selected."
                    ),
                    metadata={
                        "environment_profile_id": "review_host_read_only",
                        "execution_policy_id": review_policy.id,
                        "allow_host_read": review_policy.allow_host_read,
                        "allow_git_subprocess": review_policy.allow_git_subprocess,
                    },
                ),
                JobPlanCapability(
                    phase=PlanPhase.VERIFY,
                    capability_id="review_verifier_v1",
                    label="Review Verifier",
                    source="planner_profile",
                    reason="Review output should be verified before operator handoff.",
                    metadata={"environment_profile_id": "delivery_export_only"},
                ),
                JobPlanCapability(
                    phase=PlanPhase.DELIVER,
                    capability_id="review_handoff_bundle_v1",
                    label="Review Handoff Bundle",
                    source="delivery_policy",
                    reason="Prepare recovery-safe review artifacts for operator consumption.",
                    metadata={
                        "environment_profile_id": "delivery_export_only",
                        "delivery_policy_id": delivery_policy.id,
                        "approval_required": delivery_policy.approval_required,
                    },
                ),
            ]
        )
        return assignments

    def _build_phase_capability(self, intake: OperatorIntake) -> JobPlanCapability:
        capability = get_capability(intake.build_type)
        acceptance = self._build_acceptance_summary(intake)
        return JobPlanCapability(
            phase=PlanPhase.BUILD,
            capability_id=capability.id,
            label=capability.label,
            source="build_catalog",
            reason=(
                f"Build type `{intake.build_type.value}` resolves to the declared `{capability.id}` capability."
            ),
            metadata={
                "build_type": intake.build_type.value,
                "supports_resume": capability.supports_resume,
                "review_after_build_default": capability.review_after_build_default,
                "structured_operation_count": len(intake.implementation_plan),
                "operation_mix": self._implementation_operation_mix(intake),
                "max_operation_count": capability.max_operation_count,
                "supported_operation_types": [
                    item.value for item in capability.supported_operation_types
                ],
                "verification_defaults": [
                    item.value for item in capability.verification_defaults
                ],
                "acceptance_summary": acceptance,
                "implementation_mode": (
                    "bounded_local_engine"
                    if intake.implementation_plan
                    else "audit_marker_only"
                ),
                "environment_profile_id": "build_workspace_local",
            },
        )

    def _implementation_operation_mix(
        self,
        intake: OperatorIntake,
    ) -> dict[str, int]:
        mix: dict[str, int] = {}
        for operation in intake.implementation_plan:
            key = operation.operation_type.value
            mix[key] = mix.get(key, 0) + 1
        return dict(sorted(mix.items()))

    def _build_review_gate_policy(self, intake: OperatorIntake) -> Any:
        if not intake.run_post_build_review:
            return get_review_gate_policy("advisory")
        if intake.build_type in {BuildJobType.INTEGRATION, BuildJobType.DEVOPS}:
            return get_review_gate_policy("high_or_critical")
        return get_review_gate_policy("critical_findings")

    def _build_delivery_policy(self, intake: OperatorIntake) -> Any:
        return get_delivery_policy("approval_required")

    def _capability_ids_by_phase(
        self,
        assignments: list[JobPlanCapability],
    ) -> dict[PlanPhase, list[str]]:
        grouped: dict[PlanPhase, list[str]] = {}
        for assignment in assignments:
            grouped.setdefault(assignment.phase, []).append(assignment.capability_id)
        return grouped

    def _planned_artifacts(
        self,
        intake: OperatorIntake,
        resolved: OperatorWorkType,
    ) -> list[str]:
        if resolved == OperatorWorkType.BUILD:
            artifacts = [
                "verification_report",
                "acceptance_report",
                "diff",
                "execution_trace",
            ]
            if intake.run_post_build_review:
                artifacts.extend(["review_report", "finding_list"])
            return artifacts
        return ["review_report", "finding_list", "execution_trace"]

    def _recommended_next_action(
        self,
        qualification: IntakeQualification,
        resolved: OperatorWorkType,
        budget: JobPlanBudgetEnvelope,
    ) -> str:
        if not qualification.supported:
            return "Resolve blockers and rerun intake preview."
        if budget.hard_cap_hit:
            return "Budget hard cap blocks execution. Reduce scope or reset the budget window first."
        if budget.stop_loss_hit:
            return "Budget stop-loss blocks execution. Preserve runway or wait for approval/reset before execution."
        if not budget.within_budget:
            return "Reduce scope or budget exposure before submitting this intake."
        if budget.requires_approval:
            return "Request budget approval before submitting this intake for execution."
        if qualification.source_kind == IntakeSourceKind.GIT_URL:
            return "Submit the intake to acquire the repository into a managed mirror and then route the requested work."
        if resolved == OperatorWorkType.BUILD:
            return (
                "Submit the intake to create a build job with phase-aware planning, verification, and artifact capture."
            )
        return "Submit the intake to create a review job and package the report artifacts."
