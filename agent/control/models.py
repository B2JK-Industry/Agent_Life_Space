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
    DELIVERY_BUNDLE = "delivery_bundle"
    EXTERNAL_API_REQUEST = "external_api_request"
    EXTERNAL_API_RESPONSE = "external_api_response"
    EXTERNAL_API_CATALOG = "external_api_catalog"
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
    retention_policy_id: str = ""
    retention_status: str = ""
    expires_at: str = ""
    recoverable: bool = False

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
            "retention_policy_id": self.retention_policy_id,
            "retention_status": self.retention_status,
            "expires_at": self.expires_at,
            "recoverable": self.recoverable,
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
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


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
    GATEWAY = "gateway"
    QUALITY = "quality"
    RELEASE = "release"
    COST_ACCURACY = "cost_accuracy"
    TELEMETRY = "telemetry"


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
# Persisted Product Jobs — shared durable runtime records
# ─────────────────────────────────────────────

@dataclass
class ProductJobRecord:
    """Shared persisted view of a build/review product job."""

    job_id: str
    job_kind: JobKind
    title: str
    status: str
    subkind: str = ""
    requester: str = ""
    source: str = ""
    execution_mode: str = ""
    workspace_id: str = ""
    scope: str = ""
    outcome: str = ""
    blocked_reason: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str = ""
    duration_ms: float | None = None
    retry_count: int = 0
    failure_count: int = 0
    usage: UsageSummary = field(default_factory=lambda: UsageSummary())
    revenue_usd: float = 0.0
    margin_usd: float = 0.0
    revenue_source: str = ""
    pipeline_id: str = ""
    workflow_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_kind": self.job_kind.value,
            "title": self.title,
            "status": self.status,
            "subkind": self.subkind,
            "requester": self.requester,
            "source": self.source,
            "execution_mode": self.execution_mode,
            "workspace_id": self.workspace_id,
            "scope": self.scope,
            "outcome": self.outcome,
            "blocked_reason": self.blocked_reason,
            "artifact_ids": list(self.artifact_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
            "failure_count": self.failure_count,
            "usage": self.usage.to_dict(),
            "revenue_usd": round(self.revenue_usd, 6),
            "margin_usd": round(self.margin_usd, 6),
            "revenue_source": self.revenue_source,
            "pipeline_id": self.pipeline_id,
            "workflow_id": self.workflow_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductJobRecord:
        return cls(
            job_id=data.get("job_id", ""),
            job_kind=JobKind(data.get("job_kind", "build")),
            title=data.get("title", ""),
            status=data.get("status", ""),
            subkind=data.get("subkind", ""),
            requester=data.get("requester", ""),
            source=data.get("source", ""),
            execution_mode=data.get("execution_mode", ""),
            workspace_id=data.get("workspace_id", ""),
            scope=data.get("scope", ""),
            outcome=data.get("outcome", ""),
            blocked_reason=data.get("blocked_reason", ""),
            artifact_ids=list(data.get("artifact_ids", [])),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            completed_at=data.get("completed_at", ""),
            duration_ms=data.get("duration_ms"),
            retry_count=int(data.get("retry_count", 0)),
            failure_count=int(data.get("failure_count", 0)),
            usage=UsageSummary.from_dict(data.get("usage", {})),
            revenue_usd=float(data.get("revenue_usd", 0.0)),
            margin_usd=float(data.get("margin_usd", 0.0)),
            revenue_source=data.get("revenue_source", ""),
            pipeline_id=data.get("pipeline_id", ""),
            workflow_id=data.get("workflow_id", ""),
            metadata=dict(data.get("metadata", {})),
        )


class ArtifactRetentionStatus(str, Enum):
    """Lifecycle state for retained artifacts and delivery outputs."""

    ACTIVE = "active"
    EXPIRED = "expired"
    PRUNED = "pruned"


@dataclass
class ArtifactRetentionRecord:
    """Durable retention/recovery metadata for artifacts and delivery outputs."""

    record_id: str
    artifact_id: str = ""
    bundle_id: str = ""
    job_id: str = ""
    job_kind: JobKind = JobKind.BUILD
    artifact_kind: ArtifactKind = ArtifactKind.EXECUTION_TRACE
    source_type: str = ""
    title: str = ""
    format: str = "text"
    retention_policy_id: str = ""
    status: ArtifactRetentionStatus = ArtifactRetentionStatus.ACTIVE
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: str = ""
    recoverable: bool = True
    content: str = ""
    content_json: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "artifact_id": self.artifact_id,
            "bundle_id": self.bundle_id,
            "job_id": self.job_id,
            "job_kind": self.job_kind.value,
            "artifact_kind": self.artifact_kind.value,
            "source_type": self.source_type,
            "title": self.title,
            "format": self.format,
            "retention_policy_id": self.retention_policy_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "recoverable": self.recoverable,
            "content": self.content,
            "content_json": dict(self.content_json),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRetentionRecord:
        return cls(
            record_id=data.get("record_id", ""),
            artifact_id=data.get("artifact_id", ""),
            bundle_id=data.get("bundle_id", ""),
            job_id=data.get("job_id", ""),
            job_kind=JobKind(data.get("job_kind", "build")),
            artifact_kind=ArtifactKind(data.get("artifact_kind", "execution_trace")),
            source_type=data.get("source_type", ""),
            title=data.get("title", ""),
            format=data.get("format", "text"),
            retention_policy_id=data.get("retention_policy_id", ""),
            status=ArtifactRetentionStatus(data.get("status", "active")),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            expires_at=data.get("expires_at", ""),
            recoverable=bool(data.get("recoverable", False)),
            content=data.get("content", ""),
            content_json=dict(data.get("content_json", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class CostLedgerEntry:
    """Durable per-job token and cost snapshot."""

    entry_id: str
    job_id: str
    job_kind: JobKind
    title: str = ""
    workspace_id: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    usage: UsageSummary = field(default_factory=lambda: UsageSummary())
    source_type: str = "job_usage_snapshot"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "job_id": self.job_id,
            "job_kind": self.job_kind.value,
            "title": self.title,
            "workspace_id": self.workspace_id,
            "recorded_at": self.recorded_at,
            "usage": self.usage.to_dict(),
            "source_type": self.source_type,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CostLedgerEntry:
        return cls(
            entry_id=data.get("entry_id", ""),
            job_id=data.get("job_id", ""),
            job_kind=JobKind(data.get("job_kind", "build")),
            title=data.get("title", ""),
            workspace_id=data.get("workspace_id", ""),
            recorded_at=data.get("recorded_at", ""),
            usage=UsageSummary.from_dict(data.get("usage", {})),
            source_type=data.get("source_type", "job_usage_snapshot"),
            metadata=dict(data.get("metadata", {})),
        )


# ─────────────────────────────────────────────
# Telemetry Snapshot — periodic runtime metrics
# ─────────────────────────────────────────────

@dataclass
class TelemetrySnapshot:
    """Point-in-time runtime metrics for operator visibility.

    Recorded periodically (e.g. every job completion or via cron)
    to build longitudinal runtime history.
    """

    snapshot_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    # Job throughput
    jobs_completed: int = 0
    jobs_failed: int = 0
    jobs_retried: int = 0
    jobs_active: int = 0
    # Timing
    avg_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    # Cost
    total_cost_usd: float = 0.0
    avg_cost_per_job_usd: float = 0.0
    # Resources
    queue_depth: int = 0
    circuit_breaker_open: bool = False
    # Delivery
    deliveries_total: int = 0
    deliveries_pending: int = 0
    deliveries_failed: int = 0
    deliveries_delivered: int = 0
    # System
    memory_percent: float = 0.0
    cpu_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "recorded_at": self.recorded_at,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "jobs_retried": self.jobs_retried,
            "jobs_active": self.jobs_active,
            "avg_duration_ms": round(self.avg_duration_ms, 1),
            "max_duration_ms": round(self.max_duration_ms, 1),
            "p95_duration_ms": round(self.p95_duration_ms, 1),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "avg_cost_per_job_usd": round(self.avg_cost_per_job_usd, 6),
            "queue_depth": self.queue_depth,
            "circuit_breaker_open": self.circuit_breaker_open,
            "deliveries_total": self.deliveries_total,
            "deliveries_pending": self.deliveries_pending,
            "deliveries_failed": self.deliveries_failed,
            "deliveries_delivered": self.deliveries_delivered,
            "memory_percent": round(self.memory_percent, 1),
            "cpu_percent": round(self.cpu_percent, 1),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetrySnapshot:
        return cls(
            snapshot_id=data.get("snapshot_id", ""),
            recorded_at=data.get("recorded_at", ""),
            jobs_completed=int(data.get("jobs_completed", 0)),
            jobs_failed=int(data.get("jobs_failed", 0)),
            jobs_retried=int(data.get("jobs_retried", 0)),
            jobs_active=int(data.get("jobs_active", 0)),
            avg_duration_ms=float(data.get("avg_duration_ms", 0.0)),
            max_duration_ms=float(data.get("max_duration_ms", 0.0)),
            p95_duration_ms=float(data.get("p95_duration_ms", 0.0)),
            total_cost_usd=float(data.get("total_cost_usd", 0.0)),
            avg_cost_per_job_usd=float(data.get("avg_cost_per_job_usd", 0.0)),
            queue_depth=int(data.get("queue_depth", 0)),
            circuit_breaker_open=bool(data.get("circuit_breaker_open", False)),
            deliveries_total=int(data.get("deliveries_total", 0)),
            deliveries_pending=int(data.get("deliveries_pending", 0)),
            deliveries_failed=int(data.get("deliveries_failed", 0)),
            deliveries_delivered=int(data.get("deliveries_delivered", 0)),
            memory_percent=float(data.get("memory_percent", 0.0)),
            cpu_percent=float(data.get("cpu_percent", 0.0)),
        )


# ─────────────────────────────────────────────
# Recurring Workflows — cron-triggered product jobs
# ─────────────────────────────────────────────

@dataclass
class RecurringWorkflow:
    """Scheduled recurring product job definition.

    Enables "every Monday run security audit on repo X" patterns.
    The cron loop checks for due workflows and submits intake automatically.
    """

    workflow_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    job_kind: JobKind = JobKind.REVIEW
    schedule: str = ""  # "daily", "weekly", "monthly", or cron-like "0 8 * * 1"
    intake_template: dict[str, Any] = field(default_factory=dict)
    status: str = "active"  # active, paused, failed
    next_run_at: str = ""
    last_run_at: str = ""
    last_job_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_count: int = 0
    error_count: int = 0
    max_consecutive_errors: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "job_kind": self.job_kind.value,
            "schedule": self.schedule,
            "intake_template": dict(self.intake_template),
            "status": self.status,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "last_job_id": self.last_job_id,
            "created_at": self.created_at,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "max_consecutive_errors": self.max_consecutive_errors,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecurringWorkflow:
        return cls(
            workflow_id=data.get("workflow_id", ""),
            name=data.get("name", ""),
            job_kind=JobKind(data.get("job_kind", "review")),
            schedule=data.get("schedule", ""),
            intake_template=dict(data.get("intake_template", {})),
            status=data.get("status", "active"),
            next_run_at=data.get("next_run_at", ""),
            last_run_at=data.get("last_run_at", ""),
            last_job_id=data.get("last_job_id", ""),
            created_at=data.get("created_at", ""),
            run_count=int(data.get("run_count", 0)),
            error_count=int(data.get("error_count", 0)),
            max_consecutive_errors=int(data.get("max_consecutive_errors", 3)),
            metadata=dict(data.get("metadata", {})),
        )


# ─────────────────────────────────────────────
# Job Pipelines — multi-job orchestration
# ─────────────────────────────────────────────

@dataclass
class PipelineStage:
    """Single stage in a multi-job pipeline."""

    stage_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    job_kind: JobKind = JobKind.REVIEW
    intake_template: dict[str, Any] = field(default_factory=dict)
    condition: str = "on_success"  # always, on_success, on_failure
    status: str = "pending"  # pending, running, completed, failed, skipped
    job_id: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "name": self.name,
            "job_kind": self.job_kind.value,
            "intake_template": dict(self.intake_template),
            "condition": self.condition,
            "status": self.status,
            "job_id": self.job_id,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineStage:
        return cls(
            stage_id=data.get("stage_id", ""),
            name=data.get("name", ""),
            job_kind=JobKind(data.get("job_kind", "review")),
            intake_template=dict(data.get("intake_template", {})),
            condition=data.get("condition", "on_success"),
            status=data.get("status", "pending"),
            job_id=data.get("job_id", ""),
            error=data.get("error", ""),
        )


@dataclass
class JobPipeline:
    """Multi-job pipeline: sequential stages with conditions.

    Enables review→build→verify→deliver chains where each stage
    executes based on the previous stage's outcome.
    """

    pipeline_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    stages: list[PipelineStage] = field(default_factory=list)
    status: str = "draft"  # draft, executing, completed, failed, cancelled
    triggered_by: str = ""  # workflow_id or manual
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str = ""
    completed_at: str = ""
    current_stage_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "name": self.name,
            "stages": [s.to_dict() for s in self.stages],
            "status": self.status,
            "triggered_by": self.triggered_by,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_stage_index": self.current_stage_index,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobPipeline:
        return cls(
            pipeline_id=data.get("pipeline_id", ""),
            name=data.get("name", ""),
            stages=[PipelineStage.from_dict(s) for s in data.get("stages", [])],
            status=data.get("status", "draft"),
            triggered_by=data.get("triggered_by", ""),
            created_at=data.get("created_at", ""),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            current_stage_index=int(data.get("current_stage_index", 0)),
            metadata=dict(data.get("metadata", {})),
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
