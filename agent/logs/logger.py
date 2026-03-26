"""
Agent Life Space — Structured Logging

All agent activity is logged in structured JSON format.
Logs are rotatable, searchable, and never contain secrets.

Log levels:
    - DEBUG: Internal details (dev only)
    - INFO: Normal operations
    - WARNING: Recoverable issues
    - ERROR: Failed operations
    - CRITICAL: System-level failures

Security:
    - Secret values are NEVER logged (redacted)
    - PII is minimized
    - Audit trail for all sensitive operations
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson
import structlog

logger = structlog.get_logger(__name__)

# Patterns that should be redacted in logs
SECRET_PATTERNS = {
    "api_key", "api_secret", "password", "token", "secret",
    "private_key", "credential", "auth", "bearer",
}


def redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively redact any values whose keys match secret patterns.
    DETERMINISTIC — no randomness.
    """
    redacted = {}
    for key, value in data.items():
        key_lower = key.lower()
        if any(pattern in key_lower for pattern in SECRET_PATTERNS):
            redacted[key] = "***REDACTED***"
        elif isinstance(value, dict):
            redacted[key] = redact_secrets(value)
        elif isinstance(value, list):
            redacted[key] = [
                redact_secrets(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            redacted[key] = value
    return redacted


class AgentLogger:
    """
    Structured JSON logger with file rotation and secret redaction.
    """

    VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "AUDIT"}

    def __init__(
        self,
        log_dir: str = "agent/logs",
        max_file_size_mb: int = 10,
        max_files: int = 5,
    ) -> None:
        if not log_dir:
            msg = "log_dir cannot be empty"
            raise ValueError(msg)
        if max_file_size_mb < 0:
            msg = f"max_file_size_mb must be >= 0, got {max_file_size_mb}"
            raise ValueError(msg)
        if max_files < 1:
            msg = f"max_files must be >= 1, got {max_files}"
            raise ValueError(msg)

        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._max_file_size = max_file_size_mb * 1024 * 1024
        self._max_files = max_files
        self._current_file = self._log_dir / "agent.log"
        self._entry_count = 0

    def _rotate_if_needed(self) -> None:
        """Rotate log file if it exceeds max size."""
        if not self._current_file.exists():
            return
        if self._current_file.stat().st_size < self._max_file_size:
            return

        # Rotate: agent.log -> agent.1.log -> agent.2.log ...
        for i in range(self._max_files - 1, 0, -1):
            old = self._log_dir / f"agent.{i}.log"
            new = self._log_dir / f"agent.{i + 1}.log"
            if old.exists():
                if i + 1 >= self._max_files:
                    old.unlink()
                else:
                    old.rename(new)

        if self._current_file.exists():
            self._current_file.rename(self._log_dir / "agent.1.log")

    def log(
        self,
        level: str,
        event: str,
        source: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Write a structured log entry. Returns the entry for testing.
        """
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level.upper(),
            "event": event,
            "source": source,
            "data": redact_secrets(data) if data else {},
        }

        # Write to file
        self._rotate_if_needed()
        with open(self._current_file, "ab") as f:
            f.write(orjson.dumps(entry) + b"\n")

        self._entry_count += 1
        return entry

    def info(self, event: str, source: str = "", **data: Any) -> dict[str, Any]:
        return self.log("INFO", event, source, data)

    def warning(self, event: str, source: str = "", **data: Any) -> dict[str, Any]:
        return self.log("WARNING", event, source, data)

    def error(self, event: str, source: str = "", **data: Any) -> dict[str, Any]:
        return self.log("ERROR", event, source, data)

    def critical(self, event: str, source: str = "", **data: Any) -> dict[str, Any]:
        return self.log("CRITICAL", event, source, data)

    def audit(
        self,
        action: str,
        source: str,
        target: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Special audit log for sensitive operations."""
        return self.log(
            "AUDIT",
            f"audit.{action}",
            source,
            {
                "target": target,
                "details": redact_secrets(details) if details else {},
            },
        )

    def read_recent(self, count: int = 50) -> list[dict[str, Any]]:
        """Read recent log entries."""
        if not self._current_file.exists():
            return []

        lines = self._current_file.read_bytes().strip().split(b"\n")
        recent = lines[-count:]
        return [orjson.loads(line) for line in recent if line]

    def search(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search logs by keyword. Deterministic — searches all log files."""
        results: list[dict[str, Any]] = []
        keyword_lower = keyword.lower()

        # Search current and rotated files
        log_files = sorted(self._log_dir.glob("agent*.log"))
        for log_file in log_files:
            for line in log_file.read_bytes().split(b"\n"):
                if not line:
                    continue
                if keyword_lower.encode() in line.lower():
                    try:
                        results.append(orjson.loads(line))
                    except (orjson.JSONDecodeError, ValueError):
                        pass
                    if len(results) >= limit:
                        return results

        return results

    def get_stats(self) -> dict[str, Any]:
        """Log statistics."""
        total_size = sum(
            f.stat().st_size
            for f in self._log_dir.glob("agent*.log")
            if f.exists()
        )
        return {
            "entry_count": self._entry_count,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "log_files": len(list(self._log_dir.glob("agent*.log"))),
        }
