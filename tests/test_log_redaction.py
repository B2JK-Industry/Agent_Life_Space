"""End-to-end test: structlog runtime path must redact secrets before file write."""

from __future__ import annotations

import tempfile
from pathlib import Path

import structlog

from agent.logs.logger import (
    _structlog_secret_redactor,
    setup_tiered_logging,
)


class TestStructlogSecretRedactor:
    """Unit tests for the structlog processor."""

    def test_redacts_api_key_field(self) -> None:
        event = {"event": "test", "api_key": "sk-ant-secret123"}
        result = _structlog_secret_redactor(None, "info", event)
        assert result["api_key"] == "***REDACTED***"

    def test_redacts_bearer_token_field(self) -> None:
        event = {"event": "test", "bearer_token": "abc123"}
        result = _structlog_secret_redactor(None, "info", event)
        assert result["bearer_token"] == "***REDACTED***"

    def test_redacts_password_field(self) -> None:
        event = {"event": "test", "password": "hunter2"}
        result = _structlog_secret_redactor(None, "info", event)
        assert result["password"] == "***REDACTED***"

    def test_scrubs_bearer_in_string_value(self) -> None:
        event = {"event": "test", "header": "Authorization: Bearer sk-ant-abc123xyz"}
        result = _structlog_secret_redactor(None, "info", event)
        assert "sk-ant-abc123xyz" not in result["header"]
        assert "REDACTED" in result["header"]

    def test_scrubs_api_key_in_url(self) -> None:
        event = {"event": "test", "url": "https://api.example.com?api_key=secret123&foo=bar"}
        result = _structlog_secret_redactor(None, "info", event)
        assert "secret123" not in result["url"]
        assert "REDACTED" in result["url"]

    def test_preserves_non_secret_fields(self) -> None:
        event = {"event": "test", "user": "daniel", "count": 42}
        result = _structlog_secret_redactor(None, "info", event)
        assert result["user"] == "daniel"
        assert result["count"] == 42


class TestEndToEndLogRedaction:
    """Integration test: secrets must not appear in log files."""

    def test_secret_not_written_to_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = setup_tiered_logging(tmpdir)

            # Log a message with a secret field
            log = structlog.get_logger("redaction_test")
            log.info("test_event", api_key="sk-ant-SUPERSECRET123", normal_field="hello")
            log.info("test_bearer", header="Authorization: Bearer TOPSECRETTOKEN")

            # Force flush
            import logging
            for h in logging.getLogger().handlers:
                if hasattr(h, "flush"):
                    h.flush()

            # Read all log files
            all_content = ""
            for log_file in Path(tmpdir).rglob("*.log"):
                all_content += log_file.read_text(encoding="utf-8", errors="replace")

            # Secrets must NOT appear
            assert "sk-ant-SUPERSECRET123" not in all_content
            assert "TOPSECRETTOKEN" not in all_content
            # But normal fields and event names should
            assert "test_event" in all_content
            assert "hello" in all_content
            assert "REDACTED" in all_content
