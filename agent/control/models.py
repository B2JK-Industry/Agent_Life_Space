"""
Agent Life Space — Control-Plane Foundation

Shared primitives for all job types (review, build, operate).
Domain-specific bounded contexts (agent/review/, agent/build/) extend
these primitives — they do not replace them.

These types exist to prevent parallel work models from diverging.
ReviewJob and BuildJob both map to these shared concepts.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# ─────────────────────────────────────────────
# Job Kind — what type of work this is
# ─────────────────────────────────────────────

class JobKind(str, Enum):
    """Top-level work classification across all bounded contexts."""
    REVIEW = "review"
    BUILD = "build"
    OPERATE = "operate"
    DELIVERY = "delivery"


# ─────────────────────────────────────────────
# Job Status — shared lifecycle states
# ─────────────────────────────────────────────

class JobStatus(str, Enum):
    """Shared lifecycle states for all job types.

    Domain-specific statuses (e.g. ANALYZING for review, BUILDING for build)
    are owned by the bounded context. This enum covers the shared envelope.
    """
    CREATED = "created"
    VALIDATING = "validating"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


# ─────────────────────────────────────────────
# Execution Mode
# ─────────────────────────────────────────────

class ExecutionMode(str, Enum):
    """How a job accesses the filesystem."""
    READ_ONLY_HOST = "read_only_host"
    WORKSPACE_BOUND = "workspace_bound"


# ─────────────────────────────────────────────
# Job Timing — lifecycle timestamps
# ─────────────────────────────────────────────

@dataclass
class JobTiming:
    """Shared timing envelope for any job."""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str = ""
    completed_at: str = ""

    def mark_started(self) -> None:
        self.started_at = datetime.now(UTC).isoformat()

    def mark_completed(self) -> None:
        self.completed_at = datetime.now(UTC).isoformat()

    @property
    def duration_ms(self) -> float | None:
        if not self.started_at or not self.completed_at:
            return None
        start = datetime.fromisoformat(self.started_at)
        end = datetime.fromisoformat(self.completed_at)
        return (end - start).total_seconds() * 1000

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JobTiming:
        return cls(
            created_at=d.get("created_at", ""),
            started_at=d.get("started_at", ""),
            completed_at=d.get("completed_at", ""),
        )


# ─────────────────────────────────────────────
# Execution Trace — shared audit step
# ─────────────────────────────────────────────

@dataclass
class ExecutionStep:
    """Single auditable step in any job's execution."""
    step: str = ""
    status: str = "pending"
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    detail: str = ""
    error: str = ""

    @property
    def duration_ms(self) -> float:
        if self.completed_at and self.started_at:
            return round((self.completed_at - self.started_at) * 1000, 1)
        return 0

    def complete(self, detail: str = "") -> None:
        self.status = "completed"
        self.completed_at = time.time()
        if detail:
            self.detail = detail

    def fail(self, error: str) -> None:
        self.status = "failed"
        self.completed_at = time.time()
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionStep:
        return cls(
            step=d.get("step", ""),
            status=d.get("status", "completed"),
            started_at=d.get("started_at", 0.0),
            completed_at=d.get("completed_at", 0.0),
            detail=d.get("detail", ""),
            error=d.get("error", ""),
        )


# ─────────────────────────────────────────────
# Artifact Ref — shared artifact identity
# ─────────────────────────────────────────────

class ArtifactKind(str, Enum):
    """Shared artifact classification."""
    # Review artifacts
    REVIEW_REPORT = "review_report"
    FINDING_LIST = "finding_list"
    DIFF_ANALYSIS = "diff_analysis"
    SECURITY_REPORT = "security_report"
    EXECUTIVE_SUMMARY = "executive_summary"
    # Build artifacts
    PATCH = "patch"
    DIFF = "diff"
    VERIFICATION_REPORT = "verification_report"
    ACCEPTANCE_REPORT = "acceptance_report"
    # Shared
    EXECUTION_TRACE = "execution_trace"


@dataclass
class ArtifactRef:
    """Lightweight reference to an artifact stored elsewhere.

    Full content lives in domain-specific storage (ReviewStorage,
    BuildStorage). This ref carries identity and metadata only.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: ArtifactKind = ArtifactKind.EXECUTION_TRACE
    job_id: str = ""
    format: str = "json"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "job_id": self.job_id,
            "format": self.format,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArtifactRef:
        return cls(
            id=d.get("id", ""),
            kind=ArtifactKind(d.get("kind", "execution_trace")),
            job_id=d.get("job_id", ""),
            format=d.get("format", "json"),
            created_at=d.get("created_at", ""),
        )


@dataclass
class ArtifactQuerySummary:
    """Normalized summary view across build/review artifacts."""

    artifact_id: str
    artifact_kind: ArtifactKind
    job_id: str
    job_kind: JobKind
    source_type: str = ""
    format: str = ""
    created_at: str = ""
    content_length: int = 0
    has_json: bool = False
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind.value,
            "job_id": self.job_id,
            "job_kind": self.job_kind.value,
            "source_type": self.source_type,
            "format": self.format,
            "created_at": self.created_at,
            "content_length": self.content_length,
            "has_json": self.has_json,
            "title": self.title,
        }


@dataclass
class ArtifactQueryDetail(ArtifactQuerySummary):
    """Detailed cross-system artifact view with recoverable payload."""

    content: str = ""
    content_json: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["content"] = self.content
        base["content_json"] = self.content_json
        base["metadata"] = self.metadata
        return base


# ─────────────────────────────────────────────
# Delivery Package — shared handoff envelope
# ─────────────────────────────────────────────

@dataclass
class DeliveryPackage:
    """Shared preview/delivery envelope for operator-facing handoff packages."""

    bundle_id: str
    job_id: str
    job_kind: JobKind
    package_type: str = ""
    title: str = ""
    status: str = ""
    requester: str = ""
    workspace_id: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    artifact_count: int = 0
    delivery_ready: bool = False
    created_at: str = ""
    completed_at: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "job_id": self.job_id,
            "job_kind": self.job_kind.value,
            "package_type": self.package_type,
            "title": self.title,
            "status": self.status,
            "requester": self.requester,
            "workspace_id": self.workspace_id,
            "artifact_ids": list(self.artifact_ids),
            "artifact_count": self.artifact_count,
            "delivery_ready": self.delivery_ready,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "summary": self.summary,
            "payload": self.payload,
        }


# ─────────────────────────────────────────────
# Planner State — durable operator handoff
# ─────────────────────────────────────────────

class PlanRecordStatus(str, Enum):
    """Lifecycle state for persisted planner output."""

    PREVIEW = "preview"
    SUBMITTED = "submitted"
    BLOCKED = "blocked"
    EXECUTING = "executing"
    COMPLETED = "completed"


@dataclass
class JobPlanRecord:
    """Persisted planner output for operator handoff and recovery."""

    plan_id: str
    status: PlanRecordStatus = PlanRecordStatus.PREVIEW
    title: str = ""
    resolved_work_type: str = ""
    requester: str = ""
    repo_path: str = ""
    git_url: str = ""
    linked_job_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    intake: dict[str, Any] = field(default_factory=dict)
    qualification: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "status": self.status.value,
            "title": self.title,
            "resolved_work_type": self.resolved_work_type,
            "requester": self.requester,
            "repo_path": self.repo_path,
            "git_url": self.git_url,
            "linked_job_id": self.linked_job_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "intake": self.intake,
            "qualification": self.qualification,
            "plan": self.plan,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobPlanRecord:
        return cls(
            plan_id=data.get("plan_id", ""),
            status=PlanRecordStatus(data.get("status", "preview")),
            title=data.get("title", ""),
            resolved_work_type=data.get("resolved_work_type", ""),
            requester=data.get("requester", ""),
            repo_path=data.get("repo_path", ""),
            git_url=data.get("git_url", ""),
            linked_job_id=data.get("linked_job_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            intake=data.get("intake", {}),
            qualification=data.get("qualification", {}),
            plan=data.get("plan", {}),
        )


class TraceRecordKind(str, Enum):
    """Kind of shared planning/control-plane trace."""

    QUALIFICATION = "qualification"
    BUDGET = "budget"
    CAPABILITY = "capability"
    DELIVERY = "delivery"
    REVIEW_POLICY = "review_policy"
    VERIFICATION_DISCOVERY = "verification_discovery"
    EXECUTION = "execution"


@dataclass
class ExecutionTraceRecord:
    """Durable, queryable control-plane trace record."""

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    trace_kind: TraceRecordKind = TraceRecordKind.EXECUTION
    title: str = ""
    detail: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    plan_id: str = ""
    job_id: str = ""
    workspace_id: str = ""
    bundle_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "trace_kind": self.trace_kind.value,
            "title": self.title,
            "detail": self.detail,
            "created_at": self.created_at,
            "plan_id": self.plan_id,
            "job_id": self.job_id,
            "workspace_id": self.workspace_id,
            "bundle_id": self.bundle_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionTraceRecord:
        return cls(
            trace_id=data.get("trace_id", ""),
            trace_kind=TraceRecordKind(data.get("trace_kind", "execution")),
            title=data.get("title", ""),
            detail=data.get("detail", ""),
            created_at=data.get("created_at", ""),
            plan_id=data.get("plan_id", ""),
            job_id=data.get("job_id", ""),
            workspace_id=data.get("workspace_id", ""),
            bundle_id=data.get("bundle_id", ""),
            metadata=data.get("metadata", {}),
        )


class DeliveryLifecycleStatus(str, Enum):
    """Lifecycle state for a persisted delivery package."""

    PREPARED = "prepared"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    HANDED_OFF = "handed_off"


@dataclass
class DeliveryEvent:
    """Single auditable event in the delivery lifecycle."""

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    event_type: str = ""
    status: str = ""
    detail: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "status": self.status,
            "detail": self.detail,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryEvent:
        return cls(
            event_id=data.get("event_id", ""),
            event_type=data.get("event_type", ""),
            status=data.get("status", ""),
            detail=data.get("detail", ""),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class DeliveryRecord:
    """Durable lifecycle state for a shared delivery bundle."""

    bundle_id: str
    job_id: str
    job_kind: JobKind
    title: str = ""
    status: DeliveryLifecycleStatus = DeliveryLifecycleStatus.PREPARED
    requester: str = ""
    workspace_id: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    approval_request_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    summary: dict[str, Any] = field(default_factory=dict)
    events: list[DeliveryEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "job_id": self.job_id,
            "job_kind": self.job_kind.value,
            "title": self.title,
            "status": self.status.value,
            "requester": self.requester,
            "workspace_id": self.workspace_id,
            "artifact_ids": list(self.artifact_ids),
            "approval_request_id": self.approval_request_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summary": self.summary,
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryRecord:
        return cls(
            bundle_id=data.get("bundle_id", ""),
            job_id=data.get("job_id", ""),
            job_kind=JobKind(data.get("job_kind", "delivery")),
            title=data.get("title", ""),
            status=DeliveryLifecycleStatus(data.get("status", "prepared")),
            requester=data.get("requester", ""),
            workspace_id=data.get("workspace_id", ""),
            artifact_ids=data.get("artifact_ids", []),
            approval_request_id=data.get("approval_request_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            summary=data.get("summary", {}),
            events=[
                DeliveryEvent.from_dict(item)
                for item in data.get("events", [])
            ],
        )


# ─────────────────────────────────────────────
# Workspace Queries — shared joins
# ─────────────────────────────────────────────

@dataclass
class WorkspaceQuerySummary:
    """Normalized workspace view linked into the control plane."""

    workspace_id: str
    name: str
    status: str
    created_at: str = ""
    completed_at: str = ""
    task_id: str = ""
    owner_id: str = ""
    job_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    approval_ids: list[str] = field(default_factory=list)
    bundle_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "task_id": self.task_id,
            "owner_id": self.owner_id,
            "job_ids": list(self.job_ids),
            "artifact_ids": list(self.artifact_ids),
            "approval_ids": list(self.approval_ids),
            "bundle_ids": list(self.bundle_ids),
        }


@dataclass
class WorkspaceQueryDetail(WorkspaceQuerySummary):
    """Detailed workspace view with audit trail and linked records."""

    path: str = ""
    commands_run: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    output: str = ""
    error: str = ""
    audit_trail: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["path"] = self.path
        base["commands_run"] = list(self.commands_run)
        base["files_created"] = list(self.files_created)
        base["output"] = self.output
        base["error"] = self.error
        base["audit_trail"] = list(self.audit_trail)
        return base


# ─────────────────────────────────────────────
# Usage Summary — cost/token tracking foundation
# ─────────────────────────────────────────────

@dataclass
class UsageSummary:
    """Per-job resource usage tracking."""
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    model_used: str = ""
    llm_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "model_used": self.model_used,
            "llm_calls": self.llm_calls,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UsageSummary:
        return cls(
            total_tokens=d.get("total_tokens", 0),
            total_cost_usd=d.get("total_cost_usd", 0.0),
            model_used=d.get("model_used", ""),
            llm_calls=d.get("llm_calls", 0),
        )


# ─────────────────────────────────────────────
# Cross-System Job Query Models
# ─────────────────────────────────────────────

@dataclass
class JobQuerySummary:
    """Normalized summary view across build/review jobs."""
    job_id: str
    job_kind: JobKind
    status: str
    title: str
    subkind: str = ""
    requester: str = ""
    execution_mode: str = ""
    created_at: str = ""
    completed_at: str = ""
    artifact_count: int = 0
    scope: str = ""
    outcome: str = ""
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_kind": self.job_kind.value,
            "status": self.status,
            "title": self.title,
            "subkind": self.subkind,
            "requester": self.requester,
            "execution_mode": self.execution_mode,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "artifact_count": self.artifact_count,
            "scope": self.scope,
            "outcome": self.outcome,
            "blocked_reason": self.blocked_reason,
        }


@dataclass
class JobQueryDetail(JobQuerySummary):
    """Detailed cross-system view with normalized metadata."""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["metadata"] = self.metadata
        return base
