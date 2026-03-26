"""
Agent Life Space — JSON Message Protocol

Every module communicates through structured JSON messages.
No free-form text. No ambiguity. Schema-validated at every boundary.

Message flow:
    Module A -> JSON Message -> Message Router -> JSON Message -> Module B

Every message has:
    - Unique ID (deterministic, no randomness where avoidable)
    - Source and target module
    - Message type (enum, not free text)
    - Timestamp (UTC, ISO 8601)
    - Payload (typed per message type)
    - Priority level
    - Correlation ID (for request-response chains)
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ModuleID(str, Enum):
    """All agent modules. Deterministic routing — no guessing."""

    BRAIN = "brain"
    MEMORY = "memory"
    TASKS = "tasks"
    WORK = "work"
    PROJECTS = "projects"
    SOCIAL = "social"
    FINANCE = "finance"
    LOGS = "logs"
    WATCHDOG = "watchdog"
    LLM_ROUTER = "llm_router"
    JOB_RUNNER = "job_runner"
    SYSTEM = "system"  # For internal system messages


class MessageType(str, Enum):
    """Strict message types. Every message MUST have a known type."""

    # Core lifecycle
    REQUEST = "request"
    RESPONSE = "response"
    ERROR = "error"
    ACK = "ack"

    # Task management
    TASK_CREATE = "task.create"
    TASK_UPDATE = "task.update"
    TASK_COMPLETE = "task.complete"
    TASK_FAIL = "task.fail"

    # Memory operations
    MEMORY_STORE = "memory.store"
    MEMORY_QUERY = "memory.query"
    MEMORY_RESULT = "memory.result"

    # LLM operations
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    LLM_ERROR = "llm.error"

    # Job operations
    JOB_SCHEDULE = "job.schedule"
    JOB_START = "job.start"
    JOB_HEARTBEAT = "job.heartbeat"
    JOB_COMPLETE = "job.complete"
    JOB_FAIL = "job.fail"
    JOB_TIMEOUT = "job.timeout"

    # Brain decisions
    DECISION_REQUEST = "decision.request"
    DECISION_RESULT = "decision.result"

    # Watchdog
    HEALTH_CHECK = "health.check"
    HEALTH_REPORT = "health.report"
    PROCESS_KILL = "process.kill"
    PROCESS_RESTART = "process.restart"

    # Finance (always requires approval)
    FINANCE_PROPOSAL = "finance.proposal"
    FINANCE_APPROVAL = "finance.approval"
    FINANCE_REJECTION = "finance.rejection"

    # System
    SHUTDOWN = "system.shutdown"
    STARTUP = "system.startup"
    LOG = "system.log"


class Priority(int, Enum):
    """Message priority. Lower number = higher priority."""

    CRITICAL = 0  # System health, watchdog kills
    HIGH = 1  # Active task responses
    NORMAL = 2  # Standard operations
    LOW = 3  # Background tasks, logging
    IDLE = 4  # Maintenance, cleanup


class MessageStatus(str, Enum):
    """Message processing status."""

    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    FAILED = "failed"
    TIMEOUT = "timeout"


class Message(BaseModel):
    """
    Core message type for all inter-module communication.

    Immutable after creation. Schema-validated by Pydantic.
    Serialized to JSON via orjson for speed.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    correlation_id: str | None = Field(
        default=None,
        description="Links request-response pairs. Set on response to match request.id",
    )
    source: ModuleID
    target: ModuleID
    msg_type: MessageType
    priority: Priority = Priority.NORMAL
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    status: MessageStatus = MessageStatus.PENDING
    ttl_seconds: int = Field(
        default=300,
        description="Time-to-live. Message expires after this. Prevents zombie messages.",
        ge=1,
        le=86400,
    )
    retry_count: int = Field(default=0, ge=0, le=10)
    max_retries: int = Field(default=3, ge=0, le=10)
    created_at_mono: float = Field(
        default_factory=time.monotonic,
        description="Monotonic clock for timeout calculation. Not serialized to JSON.",
    )

    model_config = {"frozen": True}  # Immutable after creation

    @field_validator("payload")
    @classmethod
    def payload_must_be_serializable(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Ensure payload contains only JSON-serializable types."""
        import orjson

        try:
            orjson.dumps(v)
        except (TypeError, orjson.JSONEncodeError) as e:
            msg = f"Payload must be JSON-serializable: {e}"
            raise ValueError(msg) from e
        return v

    def is_expired(self) -> bool:
        """Check if message has exceeded its TTL."""
        elapsed = time.monotonic() - self.created_at_mono
        return elapsed > self.ttl_seconds

    def create_response(
        self,
        payload: dict[str, Any],
        msg_type: MessageType = MessageType.RESPONSE,
    ) -> Message:
        """Create a response message linked to this request."""
        return Message(
            correlation_id=self.id,
            source=self.target,  # Response goes back to sender
            target=self.source,
            msg_type=msg_type,
            priority=self.priority,
            payload=payload,
        )

    def create_error(self, error_code: str, error_message: str) -> Message:
        """Create an error response linked to this request."""
        return Message(
            correlation_id=self.id,
            source=self.target,
            target=self.source,
            msg_type=MessageType.ERROR,
            priority=Priority.HIGH,
            payload={
                "error_code": error_code,
                "error_message": error_message,
                "original_type": self.msg_type.value,
            },
        )

    def to_json_bytes(self) -> bytes:
        """Serialize to JSON bytes using orjson (fast)."""
        import orjson

        data = self.model_dump(exclude={"created_at_mono"})
        return orjson.dumps(data, option=orjson.OPT_UTC_Z | orjson.OPT_SORT_KEYS)

    @classmethod
    def from_json_bytes(cls, data: bytes) -> Message:
        """Deserialize from JSON bytes."""
        import orjson

        parsed = orjson.loads(data)
        return cls(**parsed)


class LLMRequest(BaseModel):
    """Structured LLM request. No free-form prompts allowed."""

    template_id: str = Field(
        description="ID of the prompt template to use. Templates are pre-defined, not generated."
    )
    variables: dict[str, str] = Field(
        default_factory=dict,
        description="Variables to fill into the template. Keys must match template placeholders.",
    )
    expected_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema that the LLM response MUST conform to. Validated after response.",
    )
    max_tokens: int = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="0.0 for deterministic tasks. Only raise for creative content.",
    )
    model: str = Field(
        default="",
        description="Model ID override. Empty = use router default (respects task→model mapping).",
    )
    require_json: bool = Field(
        default=True,
        description="Force JSON output mode. Almost always True.",
    )
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    retry_on_invalid: int = Field(
        default=2,
        ge=0,
        le=3,
        description="How many times to retry if LLM returns invalid JSON. Max 3.",
    )


class LLMResponse(BaseModel):
    """Validated LLM response."""

    request_id: str
    raw_text: str = Field(description="Raw LLM output before parsing")
    parsed: dict[str, Any] | None = Field(
        default=None, description="Parsed JSON if valid"
    )
    is_valid: bool = Field(description="Whether response passed schema validation")
    validation_errors: list[str] = Field(default_factory=list)
    model_used: str = Field(description="Which model actually responded")
    tokens_used: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)


class HealthStatus(BaseModel):
    """Health report from a module to the watchdog."""

    module: ModuleID
    status: str = Field(pattern="^(healthy|degraded|unhealthy|dead)$")
    uptime_seconds: float = Field(ge=0)
    last_heartbeat: str
    active_jobs: int = Field(default=0, ge=0)
    memory_mb: float = Field(default=0.0, ge=0)
    cpu_percent: float = Field(default=0.0, ge=0, le=100)
    error_count_last_hour: int = Field(default=0, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class FinanceProposal(BaseModel):
    """
    Financial action proposal. ALWAYS requires human approval.
    Agent proposes, human decides. No exceptions.
    """

    action: str = Field(description="What the agent wants to do")
    amount_usd: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD")
    rationale: str = Field(description="Why the agent thinks this is a good idea")
    risk_assessment: str = Field(description="Agent's own risk evaluation")
    requires_approval: bool = Field(
        default=True,
        description="Always True. Cannot be overridden. Safety constraint.",
    )
    deadline: str | None = Field(
        default=None, description="ISO 8601 deadline if time-sensitive"
    )

    @field_validator("requires_approval")
    @classmethod
    def must_require_approval(cls, v: bool) -> bool:
        """Financial actions ALWAYS require approval. Non-negotiable."""
        if not v:
            msg = "Financial proposals MUST require approval. This is a safety constraint."
            raise ValueError(msg)
        return True
