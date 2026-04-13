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
from pathlib import Path
from typing import Any, cast

from agent.control.models import (
    ArtifactKind as ArtifactKind,
)
from agent.control.models import (
    ExecutionMode as ExecutionMode,
)
from agent.control.models import (
    ExecutionStep as ExecutionStep,
)
from agent.control.models import (
    JobKind as JobKind,
)
from agent.control.models import (
    JobStatus as JobStatus,
)
from agent.control.models import (
    JobTiming as JobTiming,
)
from agent.control.models import (
    UsageSummary as UsageSummary,
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


class BuildCheckpointPhase(str, Enum):
    """Major resumable checkpoints in the build flow."""
    VALIDATED = "validated"
    WORKSPACE_READY = "workspace_ready"
    REPO_SYNCED = "repo_synced"
    BUILT = "built"
    VERIFIED = "verified"
    ACCEPTANCE_EVALUATED = "acceptance_evaluated"
    REVIEWED = "reviewed"
    ARTIFACTS_CAPTURED = "artifacts_captured"
    COMPLETED = "completed"


class BuildImplementationMode(str, Enum):
    """How the mutable build step executed."""

    AUDIT_MARKER_ONLY = "audit_marker_only"
    BOUNDED_LOCAL_ENGINE = "bounded_local_engine"


class BuildOperationType(str, Enum):
    """Deterministic local mutation kinds supported by the builder."""

    WRITE_FILE = "write_file"
    APPEND_TEXT = "append_text"
    REPLACE_TEXT = "replace_text"
    INSERT_BEFORE_TEXT = "insert_before_text"
    INSERT_AFTER_TEXT = "insert_after_text"
    DELETE_TEXT = "delete_text"
    DELETE_FILE = "delete_file"
    COPY_FILE = "copy_file"
    MOVE_FILE = "move_file"
    JSON_SET = "json_set"


class BuildOperationStatus(str, Enum):
    """Outcome of one deterministic local mutation."""

    APPLIED = "applied"
    NOOP = "noop"
    FAILED = "failed"


@dataclass
class BuildOperation:
    """Single bounded mutation instruction for local deterministic execution."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    operation_type: BuildOperationType = BuildOperationType.WRITE_FILE
    path: str = ""
    source_path: str = ""
    description: str = ""
    content: str = ""
    match_text: str = ""
    replacement_text: str = ""
    json_path: list[str] = field(default_factory=list)
    value: Any = None

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.path:
            errors.append("implementation_plan.path is required")
        else:
            candidate = Path(self.path)
            if candidate.is_absolute():
                errors.append("implementation_plan.path must be relative")
            if ".." in candidate.parts:
                errors.append("implementation_plan.path must not contain '..'")
        if self.operation_type in {
            BuildOperationType.COPY_FILE,
            BuildOperationType.MOVE_FILE,
        }:
            if not self.source_path:
                errors.append(
                    f"{self.operation_type.value} operations require source_path"
                )
            else:
                source_candidate = Path(self.source_path)
                if source_candidate.is_absolute():
                    errors.append("implementation_plan.source_path must be relative")
                if ".." in source_candidate.parts:
                    errors.append(
                        "implementation_plan.source_path must not contain '..'"
                    )

        if self.operation_type in {
            BuildOperationType.REPLACE_TEXT,
            BuildOperationType.INSERT_BEFORE_TEXT,
            BuildOperationType.INSERT_AFTER_TEXT,
            BuildOperationType.DELETE_TEXT,
        } and not self.match_text:
            errors.append(
                f"{self.operation_type.value} operations require match_text"
            )
        if self.operation_type == BuildOperationType.JSON_SET and not self.json_path:
            errors.append("json_set operations require json_path")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "operation_type": self.operation_type.value,
            "path": self.path,
            "source_path": self.source_path,
            "description": self.description,
            "content": self.content,
            "match_text": self.match_text,
            "replacement_text": self.replacement_text,
            "json_path": list(self.json_path),
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOperation:
        raw_json_path = d.get("json_path", [])
        if isinstance(raw_json_path, str):
            json_path = [part for part in raw_json_path.split(".") if part]
        else:
            json_path = [str(part) for part in raw_json_path]
        operation_value = (
            d.get("operation_type")
            or d.get("type")
            or d.get("op")
            or BuildOperationType.WRITE_FILE.value
        )
        return cls(
            id=d.get("id") or uuid.uuid4().hex[:8],
            operation_type=BuildOperationType(operation_value),
            path=d.get("path", ""),
            source_path=d.get("source_path", d.get("source", "")),
            description=d.get("description", ""),
            content=d.get("content", ""),
            match_text=d.get("match_text", d.get("match", "")),
            replacement_text=d.get(
                "replacement_text",
                d.get("replacement", ""),
            ),
            json_path=json_path,
            value=d.get("value"),
        )


@dataclass
class BuildOperationResult:
    """Persisted outcome for one deterministic build operation."""

    operation_id: str = ""
    operation_type: BuildOperationType = BuildOperationType.WRITE_FILE
    path: str = ""
    status: BuildOperationStatus = BuildOperationStatus.APPLIED
    changed: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type.value,
            "path": self.path,
            "status": self.status.value,
            "changed": self.changed,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOperationResult:
        return cls(
            operation_id=d.get("operation_id", ""),
            operation_type=BuildOperationType(
                d.get("operation_type", BuildOperationType.WRITE_FILE.value)
            ),
            path=d.get("path", ""),
            status=BuildOperationStatus(
                d.get("status", BuildOperationStatus.APPLIED.value)
            ),
            changed=d.get("changed", False),
            detail=d.get("detail", ""),
        )


# ─────────────────────────────────────────────
# Acceptance Criteria
# ─────────────────────────────────────────────

class CriterionKind(str, Enum):
    """What aspect this criterion checks."""
    FUNCTIONAL = "functional"
    QUALITY = "quality"
    SECURITY = "security"
    PERFORMANCE = "performance"


class CriterionEvaluator(str, Enum):
    """Explicit evaluator hint for an acceptance criterion."""

    AUTO = "auto"
    VERIFY_COMMAND = "verify_command"
    VERIFICATION = "verification"
    REVIEW = "review"
    CHANGE_SET = "change_set"
    WORKSPACE = "workspace"
    IMPLEMENTATION = "implementation"


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
    required: bool = True
    evaluator: CriterionEvaluator = CriterionEvaluator.AUTO
    status: CriterionStatus = CriterionStatus.PENDING
    evidence: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

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
            "required": self.required,
            "evaluator": self.evaluator.value,
            "status": self.status.value,
            "evidence": self.evidence,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AcceptanceCriterion:
        return cls(
            id=d.get("id", ""),
            description=d.get("description", ""),
            kind=CriterionKind(d.get("kind", "functional")),
            required=d.get("required", True),
            evaluator=CriterionEvaluator(d.get("evaluator", "auto")),
            status=CriterionStatus(d.get("status", "pending")),
            evidence=d.get("evidence", ""),
            metadata=dict(d.get("metadata", {})),
        )

    @classmethod
    def from_input(cls, value: AcceptanceCriterion | str | dict[str, Any]) -> AcceptanceCriterion:
        """Normalize operator/build acceptance input into a criterion object."""

        if isinstance(value, cls):
            return cls.from_dict(value.to_dict())
        if isinstance(value, str):
            return cls.from_text(value)
        if isinstance(value, dict):
            return cls.from_dict(value)
        raise ValueError("acceptance criteria must be strings or JSON objects")

    @classmethod
    def from_text(cls, text: str) -> AcceptanceCriterion:
        """Parse a lightweight operator-facing criterion string."""

        raw = text.strip()
        if not raw:
            return cls(description="")

        remaining = raw
        kind = CriterionKind.FUNCTIONAL
        required = True
        evaluator = CriterionEvaluator.AUTO

        token_map: dict[str, tuple[str, Any]] = {
            "functional": ("kind", CriterionKind.FUNCTIONAL),
            "quality": ("kind", CriterionKind.QUALITY),
            "security": ("kind", CriterionKind.SECURITY),
            "performance": ("kind", CriterionKind.PERFORMANCE),
            "required": ("required", True),
            "optional": ("required", False),
            "verify": ("evaluator", CriterionEvaluator.VERIFY_COMMAND),
            "verification": ("evaluator", CriterionEvaluator.VERIFICATION),
            "review": ("evaluator", CriterionEvaluator.REVIEW),
            "audit": ("evaluator", CriterionEvaluator.REVIEW),
            "change": ("evaluator", CriterionEvaluator.CHANGE_SET),
            "changes": ("evaluator", CriterionEvaluator.CHANGE_SET),
            "patch": ("evaluator", CriterionEvaluator.CHANGE_SET),
            "diff": ("evaluator", CriterionEvaluator.CHANGE_SET),
            "workspace": ("evaluator", CriterionEvaluator.WORKSPACE),
            "build": ("evaluator", CriterionEvaluator.WORKSPACE),
            "implementation": ("evaluator", CriterionEvaluator.IMPLEMENTATION),
            "engine": ("evaluator", CriterionEvaluator.IMPLEMENTATION),
            "mutation": ("evaluator", CriterionEvaluator.IMPLEMENTATION),
        }

        while True:
            prefix, sep, rest = remaining.partition(":")
            if not sep:
                break
            token = prefix.strip().casefold().replace("-", "_").replace(" ", "_")
            parsed = token_map.get(token)
            if parsed is None:
                break
            field_name, value = parsed
            if field_name == "kind":
                kind = cast("CriterionKind", value)
            elif field_name == "required":
                required = cast("bool", value)
            else:
                evaluator = cast("CriterionEvaluator", value)
            remaining = rest.strip()

        description = remaining or raw
        if evaluator == CriterionEvaluator.VERIFY_COMMAND and not description.casefold().startswith(
            "verify:"
        ):
            description = f"verify: {description}"
        if evaluator == CriterionEvaluator.AUTO and raw.casefold().startswith("verify:"):
            evaluator = CriterionEvaluator.VERIFY_COMMAND
            description = raw

        return cls(
            description=description,
            kind=kind,
            required=required,
            evaluator=evaluator,
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

    @property
    def required_total(self) -> int:
        return sum(1 for c in self.criteria if c.required)

    @property
    def required_met_count(self) -> int:
        return sum(
            1
            for c in self.criteria
            if c.required and c.status == CriterionStatus.MET
        )

    @property
    def required_unmet_count(self) -> int:
        return sum(
            1
            for c in self.criteria
            if c.required and c.status == CriterionStatus.UNMET
        )

    @property
    def optional_total(self) -> int:
        return sum(1 for c in self.criteria if not c.required)

    @property
    def optional_met_count(self) -> int:
        return sum(
            1
            for c in self.criteria
            if not c.required and c.status == CriterionStatus.MET
        )

    @property
    def optional_unmet_count(self) -> int:
        return sum(
            1
            for c in self.criteria
            if not c.required and c.status == CriterionStatus.UNMET
        )

    def evaluate(self) -> None:
        """Set accepted=True only if all required non-skipped criteria are met."""
        active = [c for c in self.criteria if c.status != CriterionStatus.SKIPPED]
        required_active = [c for c in active if c.required]
        self.accepted = len(active) > 0 and all(
            c.status == CriterionStatus.MET for c in required_active
        )
        self.evaluated_at = datetime.now(UTC).isoformat()
        met = self.met_count
        unmet = self.unmet_count
        skipped = sum(1 for c in self.criteria if c.status == CriterionStatus.SKIPPED)
        self.summary = (
            f"required {self.required_met_count}/{self.required_total} met, "
            f"optional {self.optional_met_count}/{self.optional_total} met, "
            f"{met}/{self.total} total met, {unmet} unmet, {skipped} skipped. "
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
            "required_total": self.required_total,
            "required_met_count": self.required_met_count,
            "required_unmet_count": self.required_unmet_count,
            "optional_total": self.optional_total,
            "optional_met_count": self.optional_met_count,
            "optional_unmet_count": self.optional_unmet_count,
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


@dataclass
class BuildCheckpoint:
    """Recorded milestone for resumable build execution."""
    phase: BuildCheckpointPhase = BuildCheckpointPhase.VALIDATED
    detail: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "detail": self.detail,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildCheckpoint:
        return cls(
            phase=BuildCheckpointPhase(d.get("phase", "validated")),
            detail=d.get("detail", ""),
            created_at=d.get("created_at", ""),
        )


# ─────────────────────────────────────────────
# Build Intake
# ─────────────────────────────────────────────

@dataclass
class BuildIntake:
    """Input specification for a build job."""
    repo_path: str = ""
    build_type: BuildJobType = BuildJobType.IMPLEMENTATION
    capability_id: str = ""
    execution_policy_id: str = "workspace_local_mutation"
    description: str = ""
    target_files: list[str] = field(default_factory=list)
    implementation_plan: list[BuildOperation] = field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)
    run_post_build_review: bool = False
    block_on_review_failure: bool = True
    review_gate_policy_id: str = "critical_findings"
    delivery_policy_id: str = "approval_required"
    source: str = "manual"
    requester: str = ""
    context: str = ""
    project_id: str = ""

    def validate(self) -> list[str]:
        """Validate intake. Returns list of errors (empty = valid)."""
        errors: list[str] = []
        if not self.repo_path:
            errors.append("repo_path is required")
        if ".." in self.repo_path:
            errors.append("repo_path must not contain '..'")
        if not self.description:
            errors.append("description is required for build jobs")
        for operation in self.implementation_plan:
            errors.extend(operation.validate())
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": self.repo_path,
            "build_type": self.build_type.value,
            "capability_id": self.capability_id,
            "execution_policy_id": self.execution_policy_id,
            "description": self.description,
            "target_files": self.target_files,
            "implementation_plan": [operation.to_dict() for operation in self.implementation_plan],
            "acceptance_criteria": [c.to_dict() for c in self.acceptance_criteria],
            "run_post_build_review": self.run_post_build_review,
            "block_on_review_failure": self.block_on_review_failure,
            "review_gate_policy_id": self.review_gate_policy_id,
            "delivery_policy_id": self.delivery_policy_id,
            "source": self.source,
            "requester": self.requester,
            "context": self.context,
            "project_id": self.project_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildIntake:
        return cls(
            repo_path=d.get("repo_path", ""),
            build_type=BuildJobType(d.get("build_type", "implementation")),
            capability_id=d.get("capability_id", ""),
            execution_policy_id=d.get("execution_policy_id", "workspace_local_mutation"),
            description=d.get("description", ""),
            target_files=d.get("target_files", []),
            implementation_plan=[
                BuildOperation.from_dict(operation)
                for operation in d.get("implementation_plan", [])
            ],
            acceptance_criteria=[
                AcceptanceCriterion.from_dict(c)
                for c in d.get("acceptance_criteria", [])
            ],
            run_post_build_review=d.get("run_post_build_review", False),
            block_on_review_failure=d.get("block_on_review_failure", True),
            review_gate_policy_id=d.get("review_gate_policy_id", "critical_findings"),
            delivery_policy_id=d.get("delivery_policy_id", "approval_required"),
            source=d.get("source", "manual"),
            requester=d.get("requester", ""),
            context=d.get("context", ""),
            project_id=d.get("project_id", ""),
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
    capability_id: str = ""
    source: str = "manual"
    requester: str = ""
    owner: str = "agent"
    resumed_from_job_id: str = ""
    resume_count: int = 0

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
    checkpoints: list[BuildCheckpoint] = field(default_factory=list)
    implementation_mode: BuildImplementationMode = BuildImplementationMode.AUDIT_MARKER_ONLY
    implementation_results: list[BuildOperationResult] = field(default_factory=list)

    # Output
    artifacts: list[BuildArtifact] = field(default_factory=list)
    execution_trace: list[ExecutionStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Cost
    usage: UsageSummary = field(default_factory=UsageSummary)

    # Error
    error: str = ""
    denial: dict[str, Any] = field(default_factory=dict)

    def trace(self, step: str) -> ExecutionStep:
        """Start a new execution trace step."""
        t = ExecutionStep(step=step)
        self.execution_trace.append(t)
        return t

    def record_checkpoint(
        self,
        phase: BuildCheckpointPhase,
        detail: str = "",
    ) -> None:
        self.checkpoints.append(BuildCheckpoint(phase=phase, detail=detail))

    @property
    def last_checkpoint(self) -> BuildCheckpoint | None:
        if not self.checkpoints:
            return None
        return self.checkpoints[-1]

    def has_checkpoint(self, phase: BuildCheckpointPhase) -> bool:
        return any(checkpoint.phase == phase for checkpoint in self.checkpoints)

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
            "capability_id": self.capability_id,
            "source": self.source,
            "requester": self.requester,
            "owner": self.owner,
            "resumed_from_job_id": self.resumed_from_job_id,
            "resume_count": self.resume_count,
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
            "checkpoints": [checkpoint.to_dict() for checkpoint in self.checkpoints],
            "implementation_mode": self.implementation_mode.value,
            "implementation_results": [
                result.to_dict() for result in self.implementation_results
            ],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "execution_trace": [t.to_dict() for t in self.execution_trace],
            "metadata": dict(self.metadata),
            "usage": self.usage.to_dict(),
            "error": self.error,
            "denial": dict(self.denial),
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
        checkpoints = [
            BuildCheckpoint.from_dict(checkpoint)
            for checkpoint in d.get("checkpoints", [])
        ]
        implementation_results = [
            BuildOperationResult.from_dict(item)
            for item in d.get("implementation_results", [])
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
            capability_id=d.get("capability_id", ""),
            source=d.get("source", "manual"),
            requester=d.get("requester", ""),
            owner=d.get("owner", "agent"),
            resumed_from_job_id=d.get("resumed_from_job_id", ""),
            resume_count=d.get("resume_count", 0),
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
            checkpoints=checkpoints,
            implementation_mode=BuildImplementationMode(
                d.get("implementation_mode", BuildImplementationMode.AUDIT_MARKER_ONLY.value)
            ),
            implementation_results=implementation_results,
            artifacts=artifacts,
            execution_trace=traces,
            metadata=dict(d.get("metadata", {})),
            usage=usage,
            error=d.get("error", ""),
            denial=dict(d.get("denial", {})),
        )
