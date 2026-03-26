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
