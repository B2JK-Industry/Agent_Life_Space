"""
Agent Life Space — Unified Operator Intake

Shared intake model and routing logic for review/build product entrypoints.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from agent.build.models import AcceptanceCriterion, BuildIntake, BuildJobType
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
    acceptance_criteria: list[str] = field(default_factory=list)
    run_post_build_review: bool = True
    max_files: int = 100

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
            "acceptance_criteria": list(self.acceptance_criteria),
            "run_post_build_review": self.run_post_build_review,
            "max_files": self.max_files,
        }


@dataclass
class JobPlanStep:
    """Single planned step for an operator intake."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    status: PlanStepStatus = PlanStepStatus.PLANNED
    detail: str = ""
    outputs: list[str] = field(default_factory=list)
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "detail": self.detail,
            "outputs": list(self.outputs),
            "blocking": self.blocking,
        }


@dataclass
class JobPlan:
    """Planner output for a qualified operator intake."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    resolved_work_type: OperatorWorkType = OperatorWorkType.REVIEW
    title: str = ""
    summary: str = ""
    scope_summary: str = ""
    risk_level: str = "low"
    budget_envelope: str = "small"
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    planned_artifacts: list[str] = field(default_factory=list)
    recommended_next_action: str = ""
    steps: list[JobPlanStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "resolved_work_type": self.resolved_work_type.value,
            "title": self.title,
            "summary": self.summary,
            "scope_summary": self.scope_summary,
            "risk_level": self.risk_level,
            "budget_envelope": self.budget_envelope,
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "planned_artifacts": list(self.planned_artifacts),
            "recommended_next_action": self.recommended_next_action,
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
    risk_level: str = "low"
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
            "risk_level": self.risk_level,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
        }


class OperatorIntakeService:
    """Qualify and route unified operator intake into build/review jobs."""

    def qualify(self, intake: OperatorIntake) -> IntakeQualification:
        if not isinstance(intake.work_type, OperatorWorkType):
            intake.work_type = OperatorWorkType(str(intake.work_type))
        if not isinstance(intake.build_type, BuildJobType):
            intake.build_type = BuildJobType(str(intake.build_type))
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
            qualification.supported = False
            qualification.blockers.append(
                "git_url intake is modeled, but clone-and-route execution is not implemented yet."
            )
            qualification.risk_level = "medium"
            return qualification

        if qualification.blockers:
            qualification.supported = False
            return qualification

        resolved = self._resolve_work_type(intake)
        qualification.resolved_work_type = resolved
        qualification.reasons.extend(self._resolve_reasons(intake, resolved))

        if resolved == OperatorWorkType.BUILD:
            qualification.risk_level = "medium"
            if not intake.acceptance_criteria:
                qualification.warnings.append(
                    "Build route has no acceptance criteria; the job may fail closed."
                )
            if not intake.description:
                qualification.supported = False
                qualification.blockers.append(
                    "Build routing requires a non-empty description."
                )
        else:
            qualification.risk_level = "low" if intake.diff_spec else "medium"

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
            context=intake.context or intake.description,
        )

    def to_build_intake(self, intake: OperatorIntake) -> BuildIntake:
        return BuildIntake(
            repo_path=intake.repo_path,
            build_type=intake.build_type,
            description=intake.description,
            target_files=list(intake.target_files),
            acceptance_criteria=[
                AcceptanceCriterion(description=item)
                for item in intake.acceptance_criteria
            ],
            run_post_build_review=intake.run_post_build_review,
            requester=intake.requester,
            context=intake.context,
        )

    def create_plan(
        self,
        intake: OperatorIntake,
        *,
        qualification: IntakeQualification | None = None,
    ) -> JobPlan:
        """Create a planner-grade execution outline for this intake."""
        qualification = qualification or self.qualify(intake)
        if not isinstance(intake.work_type, OperatorWorkType):
            intake.work_type = OperatorWorkType(str(intake.work_type))
        resolved = qualification.resolved_work_type
        steps = self._build_plan_steps(intake, qualification)
        return JobPlan(
            resolved_work_type=resolved,
            title=self._build_plan_title(intake, resolved),
            summary=self._build_plan_summary(intake, qualification),
            scope_summary=self._build_scope_summary(intake),
            risk_level=qualification.risk_level,
            budget_envelope=self._estimate_budget_envelope(intake, qualification),
            warnings=list(qualification.warnings),
            blockers=list(qualification.blockers),
            planned_artifacts=self._planned_artifacts(intake, resolved),
            recommended_next_action=self._recommended_next_action(
                qualification, resolved
            ),
            steps=steps,
        )

    def _resolve_work_type(self, intake: OperatorIntake) -> OperatorWorkType:
        if intake.work_type != OperatorWorkType.AUTO:
            return intake.work_type
        if intake.diff_spec:
            return OperatorWorkType.REVIEW
        if intake.acceptance_criteria or intake.target_files:
            return OperatorWorkType.BUILD
        description = intake.description.lower()
        build_hints = ("implement", "build", "fix", "refactor", "add", "change")
        if any(hint in description for hint in build_hints):
            return OperatorWorkType.BUILD
        return OperatorWorkType.REVIEW

    def _resolve_reasons(
        self,
        intake: OperatorIntake,
        resolved: OperatorWorkType,
    ) -> list[str]:
        reasons: list[str] = []
        if intake.diff_spec:
            reasons.append("diff_spec present, so review routing is supported.")
        if intake.target_files:
            reasons.append(
                f"target_files present ({len(intake.target_files)}), so scoped execution is available."
            )
        if intake.acceptance_criteria:
            reasons.append(
                f"acceptance_criteria present ({len(intake.acceptance_criteria)}), so build routing is meaningful."
            )
        if resolved == OperatorWorkType.BUILD and intake.run_post_build_review:
            reasons.append("build route will request deterministic post-build review.")
        if not reasons:
            reasons.append("defaulted to lightweight repository review routing.")
        return reasons

    def _build_plan_steps(
        self,
        intake: OperatorIntake,
        qualification: IntakeQualification,
    ) -> list[JobPlanStep]:
        blocked = not qualification.supported
        status = PlanStepStatus.BLOCKED if blocked else PlanStepStatus.PLANNED
        steps = [
            JobPlanStep(
                title="Qualify intake and normalize scope",
                status=status,
                detail=(
                    "Resolve route, source kind, and blockers before runtime execution."
                ),
                outputs=["qualification", "normalized_repo_path"],
            )
        ]

        if qualification.resolved_work_type == OperatorWorkType.BUILD:
            steps.extend(
                [
                    JobPlanStep(
                        title="Prepare workspace and sync repository",
                        status=status,
                        detail="Materialize the requested repo into a managed workspace.",
                        outputs=["workspace", "repo_sync_trace"],
                    ),
                    JobPlanStep(
                        title="Run deterministic build execution",
                        status=status,
                        detail="Execute the current builder capability in the workspace.",
                        outputs=["build_trace"],
                    ),
                    JobPlanStep(
                        title="Verify and evaluate acceptance",
                        status=status,
                        detail="Run verification defaults and evaluate declared acceptance criteria.",
                        outputs=["verification_report", "acceptance_report"],
                    ),
                    JobPlanStep(
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
                        blocking=intake.run_post_build_review,
                    ),
                    JobPlanStep(
                        title="Capture delivery-grade artifacts",
                        status=status,
                        detail="Persist diff, trace, and supporting artifacts for recovery/query.",
                        outputs=["diff", "execution_trace"],
                    ),
                ]
            )
        else:
            steps.extend(
                [
                    JobPlanStep(
                        title="Analyze repository or diff scope",
                        status=status,
                        detail="Run the review workflow over the requested repo or diff range.",
                        outputs=["review_report", "finding_list"],
                    ),
                    JobPlanStep(
                        title="Verify and package review output",
                        status=status,
                        detail="Finalize report artifacts and client-safe recovery payloads.",
                        outputs=["review_report", "execution_trace"],
                    ),
                ]
            )
        return steps

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
            return (
                f"Execute a {intake.build_type.value} build over {subject}, "
                "verify it, evaluate acceptance, and capture recovery-safe artifacts."
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
        if intake.diff_spec:
            parts.append(f"diff={intake.diff_spec}")
        if intake.target_files:
            parts.append(f"targets={len(intake.target_files)}")
        if intake.acceptance_criteria:
            parts.append(f"acceptance={len(intake.acceptance_criteria)}")
        if intake.focus_areas:
            parts.append(f"focus={len(intake.focus_areas)}")
        if not parts:
            return "no explicit scope signals"
        return "; ".join(parts)

    def _estimate_budget_envelope(
        self,
        intake: OperatorIntake,
        qualification: IntakeQualification,
    ) -> str:
        score = 0
        score += len(intake.target_files)
        score += len(intake.acceptance_criteria)
        score += len(intake.focus_areas)
        score += 2 if intake.diff_spec else 0
        score += 2 if qualification.resolved_work_type == OperatorWorkType.BUILD else 0
        if score <= 2:
            return "small"
        if score <= 6:
            return "medium"
        return "large"

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
    ) -> str:
        if not qualification.supported:
            return "Resolve blockers and rerun intake preview."
        if resolved == OperatorWorkType.BUILD:
            return "Submit the intake to create a build job with verification and artifact capture."
        return "Submit the intake to create a review job and package the report artifacts."
