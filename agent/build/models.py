"""
Agent Life Space — Builder Domain Models

First-class builder bounded context. Build jobs are workspace-first,
acceptance-driven, and verification-gated.

Built on shared control-plane primitives (agent.control.models).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from agent.control.models import (
    ArtifactKind,
    ExecutionMode,
    ExecutionStep,
    JobKind,
    JobStatus,
    JobTiming,
    UsageSummary,
)

# ─────────────────────────────────────────────
# Build Job Types
# ─────────────────────────────────────────────

class BuildJobType(str, Enum):
    """What kind of build work this is."""
    IMPLEMENTATION = "implementation"
    INTEGRATION = "integration"
    DEVOPS = "devops"
    TESTING = "testing"


# ─────────────────────────────────────────────
# Build-specific status extensions
# ─────────────────────────────────────────────

class BuildPhase(str, Enum):
    """Fine-grained build phases within the RUNNING status."""
    WORKSPACE_SETUP = "workspace_setup"
    BUILDING = "building"
    TESTING = "testing"
    LINTING = "linting"
    TYPE_CHECKING = "type_checking"


# ─────────────────────────────────────────────
# Acceptance Criteria
# ─────────────────────────────────────────────

class CriterionKind(str, Enum):
    """What aspect this criterion checks."""
    FUNCTIONAL = "functional"
    QUALITY = "quality"
    SECURITY = "security"
    PERFORMANCE = "performance"


class CriterionStatus(str, Enum):
    """Result of evaluating a single criterion."""
    PENDING = "pending"
    MET = "met"
    UNMET = "unmet"
    SKIPPED = "skipped"


@dataclass
class AcceptanceCriterion:
    """Single evaluable acceptance requirement."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""
    kind: CriterionKind = CriterionKind.FUNCTIONAL
    status: CriterionStatus = CriterionStatus.PENDING
    evidence: str = ""

    def meet(self, evidence: str = "") -> None:
        self.status = CriterionStatus.MET
        self.evidence = evidence

    def fail(self, evidence: str = "") -> None:
        self.status = CriterionStatus.UNMET
        self.evidence = evidence

    def skip(self, reason: str = "") -> None:
        self.status = CriterionStatus.SKIPPED
        self.evidence = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "kind": self.kind.value,
            "status": self.status.value,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AcceptanceCriterion:
        return cls(
            id=d.get("id", ""),
            description=d.get("description", ""),
            kind=CriterionKind(d.get("kind", "functional")),
            status=CriterionStatus(d.get("status", "pending")),
            evidence=d.get("evidence", ""),
        )


@dataclass
class AcceptanceVerdict:
    """Aggregate acceptance result for a build job."""
    accepted: bool = False
    criteria: list[AcceptanceCriterion] = field(default_factory=list)
    summary: str = ""
    evaluated_at: str = ""

    @property
    def met_count(self) -> int:
        return sum(1 for c in self.criteria if c.status == CriterionStatus.MET)

    @property
    def unmet_count(self) -> int:
        return sum(1 for c in self.criteria if c.status == CriterionStatus.UNMET)

    @property
    def total(self) -> int:
        return len(self.criteria)

    def evaluate(self) -> None:
        """Set accepted=True only if all non-skipped criteria are met."""
        active = [c for c in self.criteria if c.status != CriterionStatus.SKIPPED]
        self.accepted = len(active) > 0 and all(
            c.status == CriterionStatus.MET for c in active
        )
        self.evaluated_at = datetime.now(UTC).isoformat()
        met = self.met_count
        unmet = self.unmet_count
        skipped = sum(1 for c in self.criteria if c.status == CriterionStatus.SKIPPED)
        self.summary = (
            f"{met}/{self.total} met, {unmet} unmet, {skipped} skipped. "
            f"Verdict: {'accepted' if self.accepted else 'rejected'}."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "criteria": [c.to_dict() for c in self.criteria],
            "summary": self.summary,
            "evaluated_at": self.evaluated_at,
            "met_count": self.met_count,
            "unmet_count": self.unmet_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AcceptanceVerdict:
        return cls(
            accepted=d.get("accepted", False),
            criteria=[
                AcceptanceCriterion.from_dict(c)
                for c in d.get("criteria", [])
            ],
            summary=d.get("summary", ""),
            evaluated_at=d.get("evaluated_at", ""),
        )


# ─────────────────────────────────────────────
# Verification Result
# ─────────────────────────────────────────────

class VerificationKind(str, Enum):
    """What type of verification step ran."""
    TEST = "test"
    LINT = "lint"
    TYPECHECK = "typecheck"
    CUSTOM = "custom"


@dataclass
class VerificationResult:
    """Result of a single verification step."""
    kind: VerificationKind = VerificationKind.TEST
    passed: bool = False
    command: str = ""
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "passed": self.passed,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout_length": len(self.stdout),
            "stderr_length": len(self.stderr),
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VerificationResult:
        return cls(
            kind=VerificationKind(d.get("kind", "test")),
            passed=d.get("passed", False),
            command=d.get("command", ""),
            exit_code=d.get("exit_code", -1),
            stdout="",   # Content loaded via storage
            stderr="",
            duration_ms=d.get("duration_ms", 0.0),
        )


# ─────────────────────────────────────────────
# Build Intake
# ─────────────────────────────────────────────

@dataclass
class BuildIntake:
    """Input specification for a build job."""
    repo_path: str = ""
    build_type: BuildJobType = BuildJobType.IMPLEMENTATION
    description: str = ""
    target_files: list[str] = field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)
    run_post_build_review: bool = False
    block_on_review_failure: bool = True
    requester: str = ""
    context: str = ""

    def validate(self) -> list[str]:
        """Validate intake. Returns list of errors (empty = valid)."""
        errors: list[str] = []
        if not self.repo_path:
            errors.append("repo_path is required")
        if ".." in self.repo_path:
            errors.append("repo_path must not contain '..'")
        if not self.description:
            errors.append("description is required for build jobs")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": self.repo_path,
            "build_type": self.build_type.value,
            "description": self.description,
            "target_files": self.target_files,
            "acceptance_criteria": [c.to_dict() for c in self.acceptance_criteria],
            "run_post_build_review": self.run_post_build_review,
            "block_on_review_failure": self.block_on_review_failure,
            "requester": self.requester,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildIntake:
        return cls(
            repo_path=d.get("repo_path", ""),
            build_type=BuildJobType(d.get("build_type", "implementation")),
            description=d.get("description", ""),
            target_files=d.get("target_files", []),
            acceptance_criteria=[
                AcceptanceCriterion.from_dict(c)
                for c in d.get("acceptance_criteria", [])
            ],
            run_post_build_review=d.get("run_post_build_review", False),
            block_on_review_failure=d.get("block_on_review_failure", True),
            requester=d.get("requester", ""),
            context=d.get("context", ""),
        )


# ─────────────────────────────────────────────
# Build Artifact
# ─────────────────────────────────────────────

@dataclass
class BuildArtifact:
    """Identifiable, timestamped output from a build job."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    artifact_kind: ArtifactKind = ArtifactKind.PATCH
    job_id: str = ""
    content: str = ""
    content_json: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    format: str = "text"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "artifact_kind": self.artifact_kind.value,
            "job_id": self.job_id,
            "content_length": len(self.content),
            "has_json": bool(self.content_json),
            "created_at": self.created_at,
            "format": self.format,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildArtifact:
        return cls(
            id=d.get("id", ""),
            artifact_kind=ArtifactKind(d.get("artifact_kind", "patch")),
            job_id=d.get("job_id", ""),
            content="",  # Content loaded via storage
            created_at=d.get("created_at", ""),
            format=d.get("format", "text"),
        )


# ─────────────────────────────────────────────
# Build Job
# ─────────────────────────────────────────────

@dataclass
class BuildJob:
    """Unit of work for the builder product.

    Carries full lifecycle: intake -> validate -> workspace -> build ->
    verify -> acceptance -> artifacts -> complete.

    Built on shared control-plane primitives.
    """
    # Identity
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    job_kind: JobKind = JobKind.BUILD
    build_type: BuildJobType = BuildJobType.IMPLEMENTATION
    source: str = "manual"
    requester: str = ""
    owner: str = "agent"

    # Input
    intake: BuildIntake = field(default_factory=BuildIntake)

    # Execution
    workspace_id: str = ""
    execution_mode: ExecutionMode = ExecutionMode.WORKSPACE_BOUND
    phase: BuildPhase = BuildPhase.WORKSPACE_SETUP

    # Lifecycle
    status: JobStatus = JobStatus.CREATED
    timing: JobTiming = field(default_factory=JobTiming)

    # Verification
    verification_results: list[VerificationResult] = field(default_factory=list)
    acceptance: AcceptanceVerdict = field(default_factory=AcceptanceVerdict)
    post_build_review_job_id: str = ""
    post_build_review_verdict: str = ""
    post_build_review_findings: dict[str, int] = field(default_factory=dict)

    # Output
    artifacts: list[BuildArtifact] = field(default_factory=list)
    execution_trace: list[ExecutionStep] = field(default_factory=list)

    # Cost
    usage: UsageSummary = field(default_factory=UsageSummary)

    # Error
    error: str = ""

    def trace(self, step: str) -> ExecutionStep:
        """Start a new execution trace step."""
        t = ExecutionStep(step=step)
        self.execution_trace.append(t)
        return t

    @property
    def verification_passed(self) -> bool:
        """True if all verification results passed."""
        return (
            len(self.verification_results) > 0
            and all(v.passed for v in self.verification_results)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_kind": self.job_kind.value,
            "build_type": self.build_type.value,
            "source": self.source,
            "requester": self.requester,
            "owner": self.owner,
            "intake": self.intake.to_dict(),
            "workspace_id": self.workspace_id,
            "execution_mode": self.execution_mode.value,
            "phase": self.phase.value,
            "status": self.status.value,
            "timing": self.timing.to_dict(),
            "verification_results": [v.to_dict() for v in self.verification_results],
            "acceptance": self.acceptance.to_dict(),
            "post_build_review_job_id": self.post_build_review_job_id,
            "post_build_review_verdict": self.post_build_review_verdict,
            "post_build_review_findings": self.post_build_review_findings,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "execution_trace": [t.to_dict() for t in self.execution_trace],
            "usage": self.usage.to_dict(),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildJob:
        """Reconstruct a BuildJob from persisted dict. Recovery-safe."""
        intake = BuildIntake.from_dict(d.get("intake", {}))
        acceptance = AcceptanceVerdict.from_dict(d.get("acceptance", {}))
        traces = [
            ExecutionStep.from_dict(t) for t in d.get("execution_trace", [])
        ]
        artifacts = [
            BuildArtifact.from_dict(a) for a in d.get("artifacts", [])
        ]
        verifications = [
            VerificationResult.from_dict(v)
            for v in d.get("verification_results", [])
        ]
        timing = JobTiming.from_dict(d.get("timing", {}))
        usage = UsageSummary.from_dict(d.get("usage", {}))

        return cls(
            id=d.get("id", ""),
            job_kind=JobKind(d.get("job_kind", "build")),
            build_type=BuildJobType(d.get("build_type", "implementation")),
            source=d.get("source", "manual"),
            requester=d.get("requester", ""),
            owner=d.get("owner", "agent"),
            intake=intake,
            workspace_id=d.get("workspace_id", ""),
            execution_mode=ExecutionMode(
                d.get("execution_mode", "workspace_bound")
            ),
            phase=BuildPhase(d.get("phase", "workspace_setup")),
            status=JobStatus(d.get("status", "created")),
            timing=timing,
            verification_results=verifications,
            acceptance=acceptance,
            post_build_review_job_id=d.get("post_build_review_job_id", ""),
            post_build_review_verdict=d.get("post_build_review_verdict", ""),
            post_build_review_findings=d.get("post_build_review_findings", {}),
            artifacts=artifacts,
            execution_trace=traces,
            usage=usage,
            error=d.get("error", ""),
        )
