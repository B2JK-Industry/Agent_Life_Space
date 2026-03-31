"""
Tests for v1.22.0 Phase 3: provider delivery workflow + runtime telemetry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.control.models import TelemetrySnapshot, TraceRecordKind
from agent.social.telegram_handler import TelegramHandler

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.memory = MagicMock()
    agent.memory.store = AsyncMock()
    agent.review = MagicMock()
    agent.review._initialized = True
    agent.reporting = MagicMock()
    agent.submit_operator_intake = AsyncMock()
    agent.run_review_job = None
    # Delivery mocks
    agent.list_delivery_records = MagicMock(return_value=[])
    agent.get_review_delivery_bundle = MagicMock(return_value=None)
    agent.get_build_delivery_bundle = MagicMock(return_value=None)
    agent.send_review_delivery_via_gateway = AsyncMock()
    agent.send_build_delivery_via_gateway = AsyncMock()
    # Control plane state for telemetry
    agent.control_plane_state = MagicMock()
    return agent


@pytest.fixture
def handler(mock_agent):
    return TelegramHandler(agent=mock_agent)


def _delivery_record(
    job_id: str = "job-abc123",
    status: str = "handed_off",
    provider_outcome: str = "delivered",
    provider_id: str = "obolos",
    attention: bool = False,
    events: list | None = None,
) -> dict:
    """Build a delivery record dict for testing."""
    return {
        "bundle_id": f"bundle-{job_id}",
        "job_id": job_id,
        "job_kind": "review",
        "title": f"Delivery for {job_id}",
        "status": status,
        "approval_request_id": "",
        "summary": {
            "provider_delivery": {
                "provider_id": provider_id,
                "capability_id": "code_review",
                "route_id": "obolos-review",
                "outcome": provider_outcome,
                "provider_status": "completed",
                "attention_required": attention,
                "receipt": {"status": "delivered", "timestamp": "2026-03-31T10:00:00"},
                "target_url": "https://api.obolos.tech/v1/delivery",
            },
        },
        "events": events or [
            {"event_type": "gateway_requested", "status": "prepared", "detail": ""},
            {"event_type": "gateway_succeeded", "status": "handed_off", "detail": "delivered"},
        ],
    }


# ─────────────────────────────────────────────
# TelemetrySnapshot model tests
# ─────────────────────────────────────────────

class TestTelemetrySnapshotModel:

    def test_default_values(self):
        snap = TelemetrySnapshot()
        assert snap.jobs_completed == 0
        assert snap.jobs_failed == 0
        assert snap.avg_duration_ms == 0.0
        assert snap.total_cost_usd == 0.0
        assert snap.circuit_breaker_open is False
        assert snap.snapshot_id  # auto-generated

    def test_to_dict_roundtrip(self):
        snap = TelemetrySnapshot(
            jobs_completed=10,
            jobs_failed=2,
            avg_duration_ms=1500.5,
            total_cost_usd=0.042,
            deliveries_total=5,
            deliveries_delivered=3,
            memory_percent=45.2,
        )
        d = snap.to_dict()
        restored = TelemetrySnapshot.from_dict(d)
        assert restored.jobs_completed == 10
        assert restored.jobs_failed == 2
        assert restored.avg_duration_ms == 1500.5
        assert restored.total_cost_usd == 0.042
        assert restored.deliveries_delivered == 3
        assert restored.memory_percent == 45.2

    def test_from_dict_handles_missing_keys(self):
        snap = TelemetrySnapshot.from_dict({})
        assert snap.jobs_completed == 0
        assert snap.circuit_breaker_open is False


class TestTraceRecordKindTelemetry:

    def test_telemetry_kind_exists(self):
        assert TraceRecordKind.TELEMETRY == "telemetry"

    def test_all_trace_kinds_are_unique(self):
        values = [k.value for k in TraceRecordKind]
        assert len(values) == len(set(values))


# ─────────────────────────────────────────────
# /deliver command tests
# ─────────────────────────────────────────────

class TestDeliverCommand:

    async def test_deliver_list_empty(self, handler, mock_agent):
        result = await handler.handle("/deliver", user_id=1, chat_id=1)
        assert "Žiadne delivery záznamy" in result

    async def test_deliver_list_shows_provider_badge(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            _delivery_record(provider_outcome="delivered"),
            _delivery_record(job_id="job-def456", provider_outcome="failed", attention=True),
        ]
        result = await handler.handle("/deliver", user_id=1, chat_id=1)
        assert "Recent Deliveries" in result
        assert "[delivered]" in result
        assert "[failed" in result

    async def test_deliver_detail_shows_provider_info(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            _delivery_record(provider_outcome="delivered", provider_id="obolos"),
        ]
        result = await handler.handle("/deliver job-abc123", user_id=1, chat_id=1)
        assert "Provider:" in result
        assert "obolos" in result
        assert "Outcome: delivered" in result
        assert "Receipt:" in result

    async def test_deliver_detail_shows_attention_flag(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            _delivery_record(provider_outcome="pending", attention=True),
        ]
        result = await handler.handle("/deliver job-abc123", user_id=1, chat_id=1)
        assert "⚠️" in result

    async def test_deliver_detail_shows_event_details(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            _delivery_record(events=[
                {"event_type": "gateway_requested", "status": "prepared", "detail": "sending"},
                {"event_type": "gateway_failed", "status": "failed", "detail": "timeout"},
            ]),
        ]
        result = await handler.handle("/deliver job-abc123", user_id=1, chat_id=1)
        assert "gateway_failed" in result
        assert "timeout" in result

    async def test_deliver_failed_shows_retry_hint(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            _delivery_record(provider_outcome="failed", status="approved"),
        ]
        result = await handler.handle("/deliver job-abc123", user_id=1, chat_id=1)
        assert "retry" in result

    async def test_deliver_retry_triggers_gateway(self, handler, mock_agent):
        mock_agent.get_review_delivery_bundle.return_value = {"bundle_id": "b1"}
        mock_agent.send_review_delivery_via_gateway.return_value = {
            "ok": True,
            "provider_id": "obolos",
            "provider_outcome": "delivered",
        }
        result = await handler.handle("/deliver job-abc123 retry", user_id=1, chat_id=1)
        assert "Retry sent" in result
        assert "obolos" in result
        mock_agent.send_review_delivery_via_gateway.assert_awaited_once()

    async def test_deliver_send_triggers_gateway(self, handler, mock_agent):
        mock_agent.get_build_delivery_bundle.return_value = {"bundle_id": "b1"}
        mock_agent.send_build_delivery_via_gateway.return_value = {
            "ok": True,
            "provider_id": "obolos",
        }
        result = await handler.handle("/deliver job-abc123 send", user_id=1, chat_id=1)
        assert "Delivery sent" in result

    async def test_deliver_send_no_bundle(self, handler, mock_agent):
        result = await handler.handle("/deliver job-abc123 send", user_id=1, chat_id=1)
        assert "nemá delivery bundle" in result


class TestDeliverFilter:

    async def test_filter_pending(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            _delivery_record(job_id="j1", provider_outcome="pending"),
            _delivery_record(job_id="j2", provider_outcome="delivered"),
            _delivery_record(job_id="j3", provider_outcome="pending"),
        ]
        result = await handler.handle("/deliver pending", user_id=1, chat_id=1)
        assert "pending" in result
        assert "(2)" in result

    async def test_filter_failed(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            _delivery_record(job_id="j1", provider_outcome="failed"),
        ]
        result = await handler.handle("/deliver failed", user_id=1, chat_id=1)
        assert "failed" in result
        assert "(1)" in result

    async def test_filter_empty_result(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            _delivery_record(provider_outcome="delivered"),
        ]
        result = await handler.handle("/deliver pending", user_id=1, chat_id=1)
        assert "Žiadne deliveries" in result


# ─────────────────────────────────────────────
# /report delivery tests
# ─────────────────────────────────────────────

class TestReportDelivery:

    async def test_report_delivery_shows_summary(self, handler, mock_agent):
        mock_agent.reporting.get_report.return_value = {
            "summary": {},
            "inbox": [],
            "provider_delivery_summary": {
                "total": 5,
                "by_outcome": {"delivered": 3, "failed": 1, "pending": 1},
                "by_provider": {"obolos": 5},
            },
            "recent_provider_deliveries": [
                {
                    "bundle_id": "b1",
                    "job_id": "j1",
                    "title": "Test delivery",
                    "outcome": "failed",
                    "attention_required": True,
                },
                {
                    "bundle_id": "b2",
                    "job_id": "j2",
                    "title": "OK delivery",
                    "outcome": "delivered",
                    "attention_required": False,
                },
            ],
        }
        result = await handler.handle("/report delivery", user_id=1, chat_id=1)
        assert "Provider Delivery Summary" in result
        assert "delivered: 3" in result
        assert "failed: 1" in result
        assert "obolos: 5" in result
        assert "Attention required" in result

    async def test_report_delivery_empty(self, handler, mock_agent):
        mock_agent.reporting.get_report.return_value = {
            "summary": {},
            "inbox": [],
            "provider_delivery_summary": {},
            "recent_provider_deliveries": [],
        }
        result = await handler.handle("/report delivery", user_id=1, chat_id=1)
        assert "Žiadne provider deliveries" in result


# ─────────────────────────────────────────────
# /telemetry command tests
# ─────────────────────────────────────────────

class TestTelemetryCommand:

    async def test_telemetry_no_data(self, handler, mock_agent):
        mock_agent.control_plane_state.get_telemetry_summary.return_value = {
            "snapshots": 0,
            "latest": None,
            "window_hours": 24,
        }
        result = await handler.handle("/telemetry", user_id=1, chat_id=1)
        assert "Žiadne dáta" in result

    async def test_telemetry_with_latest_only(self, handler, mock_agent):
        mock_agent.control_plane_state.get_telemetry_summary.return_value = {
            "snapshots": 0,
            "latest": TelemetrySnapshot(
                jobs_completed=10,
                jobs_failed=1,
                avg_duration_ms=2000,
                total_cost_usd=0.05,
            ).to_dict(),
            "window_hours": 24,
        }
        result = await handler.handle("/telemetry", user_id=1, chat_id=1)
        assert "Runtime Telemetry" in result
        assert "10 completed" in result
        assert "1 failed" in result

    async def test_telemetry_with_full_summary(self, handler, mock_agent):
        mock_agent.control_plane_state.get_telemetry_summary.return_value = {
            "snapshots": 5,
            "window_hours": 24,
            "trend": "stable",
            "latest": TelemetrySnapshot(
                jobs_completed=20,
                jobs_failed=2,
                avg_duration_ms=1500,
                p95_duration_ms=3500,
                total_cost_usd=0.1,
                deliveries_total=5,
                deliveries_delivered=3,
                memory_percent=45.0,
                cpu_percent=22.0,
            ).to_dict(),
            "aggregated": {
                "max_jobs_completed": 20,
                "max_jobs_failed": 3,
                "max_cost_usd": 0.12,
                "avg_duration_ms": 1600,
                "total_snapshots_with_failures": 2,
                "circuit_breaker_triggered": 0,
            },
        }
        result = await handler.handle("/telemetry", user_id=1, chat_id=1)
        assert "24h window" in result
        assert "stable" in result
        assert "20 done" in result
        assert "Aggregated" in result

    async def test_telemetry_custom_window(self, handler, mock_agent):
        mock_agent.control_plane_state.get_telemetry_summary.return_value = {
            "snapshots": 0,
            "latest": None,
            "window_hours": 48,
        }
        await handler.handle("/telemetry 48", user_id=1, chat_id=1)
        mock_agent.control_plane_state.get_telemetry_summary.assert_called_once_with(
            window_hours=48,
        )

    async def test_telemetry_degrading_trend(self, handler, mock_agent):
        mock_agent.control_plane_state.get_telemetry_summary.return_value = {
            "snapshots": 6,
            "window_hours": 24,
            "trend": "degrading",
            "latest": TelemetrySnapshot(jobs_completed=5, jobs_failed=4).to_dict(),
            "aggregated": {
                "max_jobs_completed": 10,
                "max_jobs_failed": 5,
                "max_cost_usd": 0.2,
                "avg_duration_ms": 5000,
                "total_snapshots_with_failures": 4,
                "circuit_breaker_triggered": 1,
            },
        }
        result = await handler.handle("/telemetry", user_id=1, chat_id=1)
        assert "degrading" in result
        assert "Circuit breaker triggered: 1" in result


# ─────────────────────────────────────────────
# /report telemetry sub-command tests
# ─────────────────────────────────────────────

class TestReportTelemetry:

    async def test_report_telemetry_no_data(self, handler, mock_agent):
        mock_agent.reporting.get_report.return_value = {
            "summary": {},
            "inbox": [],
            "telemetry_summary": {"snapshots": 0, "latest": None, "window_hours": 24},
        }
        result = await handler.handle("/report telemetry", user_id=1, chat_id=1)
        assert "Žiadne dáta" in result

    async def test_report_telemetry_with_data(self, handler, mock_agent):
        mock_agent.reporting.get_report.return_value = {
            "summary": {},
            "inbox": [],
            "telemetry_summary": {
                "snapshots": 3,
                "window_hours": 24,
                "trend": "improving",
                "latest": TelemetrySnapshot(
                    jobs_completed=15,
                    total_cost_usd=0.08,
                ).to_dict(),
                "aggregated": {
                    "max_jobs_completed": 15,
                    "max_jobs_failed": 1,
                    "max_cost_usd": 0.09,
                    "avg_duration_ms": 1200,
                    "total_snapshots_with_failures": 1,
                    "circuit_breaker_triggered": 0,
                },
            },
        }
        result = await handler.handle("/report telemetry", user_id=1, chat_id=1)
        assert "Runtime Telemetry" in result
        assert "improving" in result
