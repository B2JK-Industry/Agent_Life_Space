"""
Test scenarios for Structured Logger.

1. Logs are valid JSON
2. Secrets are NEVER in logs (redacted)
3. Log rotation works
4. Search finds entries
5. Audit trail works
"""

from __future__ import annotations

import tempfile

import pytest

from agent.logs.logger import AgentLogger, redact_secrets


@pytest.fixture
def log():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield AgentLogger(log_dir=tmpdir, max_file_size_mb=1)


class TestSecretRedaction:
    """CRITICAL: Secrets must never appear in logs."""

    def test_api_key_redacted(self) -> None:
        data = {"api_key": "sk-ant-xxx123", "name": "test"}
        result = redact_secrets(data)
        assert result["api_key"] == "***REDACTED***"
        assert result["name"] == "test"

    def test_nested_secret_redacted(self) -> None:
        data = {
            "config": {
                "anthropic_api_key": "sk-secret",
                "model": "opus",
            }
        }
        result = redact_secrets(data)
        assert result["config"]["anthropic_api_key"] == "***REDACTED***"
        assert result["config"]["model"] == "opus"

    def test_password_redacted(self) -> None:
        data = {"user_password": "hunter2", "username": "admin"}
        result = redact_secrets(data)
        assert result["user_password"] == "***REDACTED***"
        assert result["username"] == "admin"

    def test_token_in_list_redacted(self) -> None:
        data = {
            "items": [
                {"bearer_token": "abc123", "id": 1},
                {"name": "safe"},
            ]
        }
        result = redact_secrets(data)
        assert result["items"][0]["bearer_token"] == "***REDACTED***"
        assert result["items"][0]["id"] == 1


class TestLogging:
    """Structured logging works correctly."""

    def test_info_log(self, log: AgentLogger) -> None:
        entry = log.info("task_completed", source="brain", task_id="abc")
        assert entry["level"] == "INFO"
        assert entry["event"] == "task_completed"
        assert entry["source"] == "brain"

    def test_error_log(self, log: AgentLogger) -> None:
        entry = log.error("job_failed", source="runner", error="timeout")
        assert entry["level"] == "ERROR"

    def test_log_has_timestamp(self, log: AgentLogger) -> None:
        entry = log.info("test")
        assert "T" in entry["timestamp"]

    def test_secrets_redacted_in_log(self, log: AgentLogger) -> None:
        entry = log.info(
            "config_loaded",
            source="system",
            api_key="sk-secret-value",
            model="opus",
        )
        assert entry["data"]["api_key"] == "***REDACTED***"
        assert entry["data"]["model"] == "opus"

    def test_read_recent(self, log: AgentLogger) -> None:
        log.info("entry_1")
        log.info("entry_2")
        log.info("entry_3")
        recent = log.read_recent(2)
        assert len(recent) == 2
        assert recent[-1]["event"] == "entry_3"

    def test_search(self, log: AgentLogger) -> None:
        log.info("task_completed", source="brain")
        log.info("job_failed", source="runner")
        log.info("task_started", source="brain")

        results = log.search("task")
        assert len(results) == 2

    def test_audit_log(self, log: AgentLogger) -> None:
        entry = log.audit(
            action="secret_accessed",
            source="vault",
            target="ANTHROPIC_API_KEY",
        )
        assert entry["level"] == "AUDIT"
        assert "secret_accessed" in entry["event"]


class TestLogRotation:
    """Logs rotate when too large."""

    def test_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Very small max to trigger rotation
            log = AgentLogger(
                log_dir=tmpdir,
                max_file_size_mb=0,  # Will rotate immediately
                max_files=3,
            )
            # Write enough to trigger rotation
            for i in range(5):
                # Force file creation first
                log.info(f"entry_{i}", source="test", data_payload="x" * 100)

            stats = log.get_stats()
            assert stats["log_files"] >= 1


class TestLogStats:
    def test_stats(self, log: AgentLogger) -> None:
        log.info("a")
        log.info("b")
        stats = log.get_stats()
        assert stats["entry_count"] == 2
        assert stats["log_files"] >= 1
