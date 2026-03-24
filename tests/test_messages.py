"""
Test scenarios for JSON Message Protocol.

These are PRACTICAL tests — they verify real scenarios the agent will face:
1. Messages must be valid JSON at all times
2. Messages must not be tampered with (immutable)
3. Expired messages must be detected
4. Financial proposals MUST require approval (safety)
5. LLM requests enforce schema validation
6. Messages round-trip through serialization without data loss
"""

from __future__ import annotations

import time

import pytest

from agent.core.messages import (
    FinanceProposal,
    HealthStatus,
    LLMRequest,
    LLMResponse,
    Message,
    MessageType,
    ModuleID,
    Priority,
)


class TestMessageCreation:
    """Scenario: Creating messages between modules."""

    def test_basic_message_creation(self) -> None:
        """Agent brain sends a request to memory module."""
        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.MEMORY_QUERY,
            payload={"query": "last 5 tasks completed"},
        )
        assert msg.source == ModuleID.BRAIN
        assert msg.target == ModuleID.MEMORY
        assert msg.msg_type == MessageType.MEMORY_QUERY
        assert msg.priority == Priority.NORMAL
        assert len(msg.id) == 16
        assert msg.status.value == "pending"

    def test_message_has_timestamp(self) -> None:
        """Every message must have a UTC timestamp."""
        msg = Message(
            source=ModuleID.TASKS,
            target=ModuleID.BRAIN,
            msg_type=MessageType.TASK_CREATE,
            payload={"task": "research market"},
        )
        assert "T" in msg.timestamp  # ISO 8601
        assert msg.timestamp.endswith("+00:00") or "Z" in msg.timestamp

    def test_message_immutability(self) -> None:
        """Messages must be immutable after creation. Prevents tampering."""
        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.REQUEST,
            payload={"data": "test"},
        )
        with pytest.raises(Exception):
            msg.source = ModuleID.FINANCE  # type: ignore[misc]

    def test_priority_ordering(self) -> None:
        """Critical messages have lower number = processed first."""
        assert Priority.CRITICAL.value < Priority.HIGH.value
        assert Priority.HIGH.value < Priority.NORMAL.value
        assert Priority.NORMAL.value < Priority.LOW.value
        assert Priority.LOW.value < Priority.IDLE.value


class TestMessageTTL:
    """Scenario: Messages must expire to prevent zombie messages."""

    def test_fresh_message_not_expired(self) -> None:
        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.TASKS,
            msg_type=MessageType.TASK_CREATE,
            ttl_seconds=60,
        )
        assert not msg.is_expired()

    def test_expired_message_detected(self) -> None:
        """Simulate an old message — must be detected as expired."""
        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.TASKS,
            msg_type=MessageType.TASK_CREATE,
            ttl_seconds=1,
            created_at_mono=time.monotonic() - 10,  # 10 seconds ago
        )
        assert msg.is_expired()

    def test_ttl_bounds(self) -> None:
        """TTL must be between 1 and 86400 seconds."""
        with pytest.raises(Exception):
            Message(
                source=ModuleID.BRAIN,
                target=ModuleID.TASKS,
                msg_type=MessageType.REQUEST,
                ttl_seconds=0,
            )
        with pytest.raises(Exception):
            Message(
                source=ModuleID.BRAIN,
                target=ModuleID.TASKS,
                msg_type=MessageType.REQUEST,
                ttl_seconds=100000,
            )


class TestMessageSerialization:
    """Scenario: Messages must survive JSON round-trip without data loss."""

    def test_round_trip(self) -> None:
        """Serialize to JSON bytes and back — data must be identical."""
        original = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.MEMORY_STORE,
            priority=Priority.HIGH,
            payload={"key": "test_fact", "value": "Python is great"},
        )
        json_bytes = original.to_json_bytes()
        restored = Message.from_json_bytes(json_bytes)

        assert restored.id == original.id
        assert restored.source == original.source
        assert restored.target == original.target
        assert restored.msg_type == original.msg_type
        assert restored.priority == original.priority
        assert restored.payload == original.payload

    def test_payload_must_be_json_serializable(self) -> None:
        """Payloads with non-serializable types must be rejected."""
        with pytest.raises(Exception):
            Message(
                source=ModuleID.BRAIN,
                target=ModuleID.TASKS,
                msg_type=MessageType.REQUEST,
                payload={"bad": object()},
            )

    def test_nested_payload(self) -> None:
        """Complex nested payloads must work."""
        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.MEMORY_STORE,
            payload={
                "memories": [
                    {"type": "episodic", "content": "Completed task X"},
                    {"type": "semantic", "content": "API rate limit is 100/min"},
                ],
                "metadata": {"source": "task_completion", "confidence": 0.95},
            },
        )
        json_bytes = msg.to_json_bytes()
        restored = Message.from_json_bytes(json_bytes)
        assert len(restored.payload["memories"]) == 2


class TestResponseChaining:
    """Scenario: Request-response pairs must be linked via correlation_id."""

    def test_create_response(self) -> None:
        """Response must reference the original request."""
        request = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.MEMORY_QUERY,
            payload={"query": "what did I do yesterday"},
        )
        response = request.create_response(
            payload={"results": ["Completed 3 tasks", "Researched markets"]},
            msg_type=MessageType.MEMORY_RESULT,
        )
        assert response.correlation_id == request.id
        assert response.source == ModuleID.MEMORY  # Swapped
        assert response.target == ModuleID.BRAIN  # Swapped
        assert response.msg_type == MessageType.MEMORY_RESULT

    def test_create_error_response(self) -> None:
        """Error responses must carry error details."""
        request = Message(
            source=ModuleID.TASKS,
            target=ModuleID.LLM_ROUTER,
            msg_type=MessageType.LLM_REQUEST,
        )
        error = request.create_error("TIMEOUT", "LLM did not respond within 30s")
        assert error.correlation_id == request.id
        assert error.msg_type == MessageType.ERROR
        assert error.priority == Priority.HIGH
        assert error.payload["error_code"] == "TIMEOUT"


class TestFinanceSafety:
    """
    CRITICAL: Financial proposals must ALWAYS require human approval.
    This is the most important safety test in the entire system.
    """

    def test_proposal_requires_approval(self) -> None:
        """Cannot create a finance proposal without approval requirement."""
        proposal = FinanceProposal(
            action="Buy domain example.com",
            amount_usd=12.99,
            rationale="Good domain for the project",
            risk_assessment="Low risk, $12.99 one-time cost",
        )
        assert proposal.requires_approval is True

    def test_cannot_bypass_approval(self) -> None:
        """Attempting to set requires_approval=False MUST fail."""
        with pytest.raises(ValueError, match="safety constraint"):
            FinanceProposal(
                action="Transfer funds",
                amount_usd=100.0,
                rationale="Need to pay for service",
                risk_assessment="Medium risk",
                requires_approval=False,
            )

    def test_proposal_has_risk_assessment(self) -> None:
        """Every financial proposal must include risk assessment."""
        with pytest.raises(Exception):
            FinanceProposal(
                action="Buy something",
                rationale="I want it",
                # Missing risk_assessment — must fail
            )  # type: ignore[call-arg]


class TestLLMRequest:
    """Scenario: LLM requests must be structured, not free-form."""

    def test_structured_request(self) -> None:
        """LLM requests use templates, not raw prompts."""
        req = LLMRequest(
            template_id="task_breakdown",
            variables={"task": "research competitor pricing"},
            max_tokens=512,
            temperature=0.0,
        )
        assert req.require_json is True
        assert req.temperature == 0.0
        assert req.retry_on_invalid == 2

    def test_temperature_bounds(self) -> None:
        """Temperature must be 0.0-1.0."""
        with pytest.raises(Exception):
            LLMRequest(
                template_id="test",
                temperature=1.5,
            )

    def test_max_retries_bounded(self) -> None:
        """Cannot set unlimited retries — prevents infinite loops."""
        with pytest.raises(Exception):
            LLMRequest(
                template_id="test",
                retry_on_invalid=10,
            )

    def test_timeout_bounded(self) -> None:
        """Timeout must be reasonable — no 0s or infinite waits."""
        with pytest.raises(Exception):
            LLMRequest(template_id="test", timeout_seconds=1)
        with pytest.raises(Exception):
            LLMRequest(template_id="test", timeout_seconds=999)


class TestLLMResponse:
    """Scenario: LLM responses must be validated."""

    def test_valid_response(self) -> None:
        resp = LLMResponse(
            request_id="abc123",
            raw_text='{"result": "success"}',
            parsed={"result": "success"},
            is_valid=True,
            model_used="claude-opus-4-6",
            tokens_used=50,
            latency_ms=230,
        )
        assert resp.is_valid
        assert resp.parsed is not None

    def test_invalid_response_tracked(self) -> None:
        resp = LLMResponse(
            request_id="abc123",
            raw_text="This is not JSON at all",
            parsed=None,
            is_valid=False,
            validation_errors=["Expected JSON, got plain text"],
            model_used="claude-opus-4-6",
        )
        assert not resp.is_valid
        assert len(resp.validation_errors) == 1


class TestHealthStatus:
    """Scenario: Module health reports for the watchdog."""

    def test_health_report(self) -> None:
        status = HealthStatus(
            module=ModuleID.BRAIN,
            status="healthy",
            uptime_seconds=3600.0,
            last_heartbeat="2026-03-24T00:00:00Z",
            active_jobs=2,
            memory_mb=45.3,
            cpu_percent=12.5,
        )
        assert status.status == "healthy"
        assert status.module == ModuleID.BRAIN

    def test_invalid_status_rejected(self) -> None:
        """Only valid status strings allowed."""
        with pytest.raises(Exception):
            HealthStatus(
                module=ModuleID.BRAIN,
                status="maybe_ok",  # Invalid
                uptime_seconds=100.0,
                last_heartbeat="2026-03-24T00:00:00Z",
            )
