"""
Tests for Agent API audit trail and telemetry.
"""

from __future__ import annotations

from agent.social.agent_api import ApiAuditEntry, ApiAuditLog


class TestApiAuditLog:
    """API audit log tracks all requests."""

    def test_record_success(self):
        log = ApiAuditLog()
        log.record(ApiAuditEntry(sender="bot1", ip="1.2.3.4", status_code=200, duration_ms=150))
        assert log._total_requests == 1
        assert log._total_errors == 0

    def test_record_error(self):
        log = ApiAuditLog()
        log.record(ApiAuditEntry(status_code=500, error="internal"))
        assert log._total_errors == 1

    def test_record_rate_limited(self):
        log = ApiAuditLog()
        log.record(ApiAuditEntry(status_code=429, error="rate_limited"))
        assert log._total_rate_limited == 1

    def test_record_auth_failure(self):
        log = ApiAuditLog()
        log.record(ApiAuditEntry(status_code=401, error="bad_key"))
        assert log._total_auth_failures == 1

    def test_get_recent(self):
        log = ApiAuditLog()
        for i in range(5):
            log.record(ApiAuditEntry(sender=f"bot{i}"))
        recent = log.get_recent(3)
        assert len(recent) == 3
        assert recent[-1]["sender"] == "bot4"

    def test_ring_buffer(self):
        log = ApiAuditLog(max_entries=3)
        for i in range(5):
            log.record(ApiAuditEntry(sender=f"bot{i}"))
        assert len(log._entries) == 3
        assert log._total_requests == 5  # Total count not affected by eviction

    def test_stats(self):
        log = ApiAuditLog()
        log.record(ApiAuditEntry(sender="bot1", status_code=200))
        log.record(ApiAuditEntry(sender="bot1", status_code=200))
        log.record(ApiAuditEntry(sender="bot2", status_code=401))
        log.record(ApiAuditEntry(sender="bot1", status_code=429))

        stats = log.get_stats()
        assert stats["total_requests"] == 4
        assert stats["total_errors"] == 2
        assert stats["total_rate_limited"] == 1
        assert stats["total_auth_failures"] == 1
        assert stats["by_sender"]["bot1"] == 3
        assert stats["by_sender"]["bot2"] == 1

    def test_entry_to_dict(self):
        entry = ApiAuditEntry(
            sender="bot1", ip="1.2.3.4", intent="question",
            status_code=200, duration_ms=100,
        )
        d = entry.to_dict()
        assert d["sender"] == "bot1"
        assert d["duration_ms"] == 100
        assert d["status_code"] == 200
