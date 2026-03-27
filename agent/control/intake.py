"""
Agent Life Space — Unified Operator Intake

Shared intake model and routing logic for review/build product entrypoints.
"""

from __future__ import annotations

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
