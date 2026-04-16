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

import logging
import re as _re
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import orjson
import structlog

from agent.logs.retention import resolve_tier

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Tiered file logging setup (structlog → stdlib logging → file sinks)
# ─────────────────────────────────────────────────────────────────────


class _TierRouter(logging.Handler):
    """Stdlib handler that fans a record out to one of two file handlers
    based on the structured event name + level. The decision is made
    by ``agent.logs.retention.resolve_tier`` so it stays consistent
    with the cron-side cleanup logic.

    structlog records carry the structured fields on
    ``record.msg`` (after our ``JSONRenderer``) — we look at the raw
    event name there. If we cannot find one we fall back to the
    record's level alone.
    """

    def __init__(
        self,
        long_handler: logging.Handler,
        short_handler: logging.Handler,
    ) -> None:
        super().__init__()
        self._long = long_handler
        self._short = short_handler

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = record.getMessage()
            event = ""
            if payload.startswith("{"):
                try:
                    parsed = orjson.loads(payload)
                    event = str(parsed.get("event", ""))
                except (orjson.JSONDecodeError, ValueError):
                    pass
            tier = resolve_tier(record.levelname, event)
            target = self._long if tier == "long" else self._short
            target.handle(record)
        except Exception:  # pragma: no cover - last-resort safety
            self.handleError(record)

    def close(self) -> None:
        """Close the wrapped tier handlers as well as ourselves.

        ``logging.Handler.close`` only releases this handler's own
        resources; without overriding it, the inner long/short
        ``TimedRotatingFileHandler`` instances would keep their
        underlying file objects open until GC, which then fires
        ResourceWarning under pytest's unraisable-exception capture.
        """
        try:
            self._long.close()
        except Exception:  # pragma: no cover
            pass
        try:
            self._short.close()
        except Exception:  # pragma: no cover
            pass
        super().close()


def setup_tiered_logging(
    log_dir: str | Path,
    *,
    long_retention_hours: int = 720,
    short_retention_hours: int = 6,
    rotate_when: str = "midnight",
) -> dict[str, str]:
    """Configure structlog + stdlib logging to write to two tier sinks.

    Both retention windows are expressed in **hours**, matching the
    contract used by ``agent.logs.retention``. Previously this function
    accepted ``long_retention_days`` while the cron-side
    ``LogRetentionManager`` read ``AGENT_LOG_LONG_RETENTION_HOURS`` —
    operators who set only one variable saw the rotating handler
    using one value and the prune sweep using another. Unifying on
    hours removes that footgun.

    Side effects:

    * Creates ``<log_dir>/long`` and ``<log_dir>/short`` directories.
    * Removes any existing root StreamHandlers (so logs don't double-emit).
    * Adds a single ``_TierRouter`` to the root logger; that router
      forwards each record to ``agent-long.log`` or ``agent-short.log``.
    * Both file handlers are ``TimedRotatingFileHandler`` so daily
      rotation happens for free; the dated suffix files are then aged
      out by ``LogRetentionManager`` (cron loop).

    Returns the resolved file paths so the caller can log them.
    """
    base = Path(log_dir)
    long_dir = base / "long"
    short_dir = base / "short"
    long_dir.mkdir(parents=True, exist_ok=True)
    short_dir.mkdir(parents=True, exist_ok=True)

    long_path = long_dir / "agent-long.log"
    short_path = short_dir / "agent-short.log"

    # Long tier rotates daily; backupCount is "long_retention_hours
    # rounded up to the nearest day, plus a safety margin". The real
    # source of truth for "how long do we keep this" is
    # LogRetentionManager (cron), but we still need backupCount large
    # enough that the rotating handler does not delete files before
    # the prune sweep gets a chance to consider them.
    long_retention_days = max((long_retention_hours + 23) // 24, 1)
    long_handler = TimedRotatingFileHandler(
        long_path,
        when=rotate_when,
        interval=1,
        backupCount=long_retention_days + 1,
        encoding="utf-8",
        utc=True,
        delay=True,
    )
    long_handler.setLevel(logging.INFO)
    long_handler.setFormatter(logging.Formatter("%(message)s"))

    # Short tier accepts DEBUG so we get all the noisy diagnostics.
    short_handler = TimedRotatingFileHandler(
        short_path,
        when="H",  # hourly rotation; retention manager kills > N hours
        interval=1,
        backupCount=max(short_retention_hours + 1, 2),
        encoding="utf-8",
        utc=True,
        delay=True,
    )
    short_handler.setLevel(logging.DEBUG)
    short_handler.setFormatter(logging.Formatter("%(message)s"))

    router = _TierRouter(long_handler=long_handler, short_handler=short_handler)
    router.setLevel(logging.DEBUG)

    root = logging.getLogger()
    # Drop pre-existing stream handlers — we want JSON to file, not
    # interleaved with anything else. We deliberately keep custom
    # FileHandlers in case the operator wired their own. Also drop any
    # _TierRouter from a previous setup_tiered_logging() call so we do
    # not double-emit when this function is invoked twice (e.g. tests).
    for h in list(root.handlers):
        if isinstance(h, _TierRouter):
            root.removeHandler(h)
        elif isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            root.removeHandler(h)
    root.addHandler(router)
    root.setLevel(logging.DEBUG)

    # CRITICAL: route structlog through stdlib so the _TierRouter on
    # the root logger actually receives the records. Without this the
    # process keeps the PrintLoggerFactory configured at startup and
    # every event goes to stdout, never to disk. We also use the stdlib
    # BoundLogger wrapper so .info()/.warning()/.error() map onto the
    # matching stdlib level (the tier router needs the levelname).
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _structlog_secret_redactor,  # redact before serialization
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    return {"long_log": str(long_path), "short_log": str(short_path)}

# Patterns that should be redacted in logs
SECRET_PATTERNS = {
    "api_key", "api_secret", "password", "token", "secret",
    "private_key", "credential", "auth", "bearer",
    "wallet_address", "wallet_addr", "eth_address", "btc_address",
    "account_address",
}

# Regex patterns for secrets that may appear as free text in string values
_FREE_TEXT_SECRET_PATTERNS = (
    _re.compile(r"(Authorization:\s*Bearer\s+)\S+", _re.IGNORECASE),
    _re.compile(r"([?&](?:api_key|token|key|secret|password)=)[^\s&]+", _re.IGNORECASE),
    _re.compile(r"(sk-ant-)[a-zA-Z0-9_-]{10,}"),
    _re.compile(r"(sk-)[a-zA-Z0-9_-]{10,}"),
    _re.compile(r"(agent_api_)[a-zA-Z0-9_-]{10,}"),
    # Ethereum / EVM addresses (0x + 40 hex)
    _re.compile(r"(0x)[a-fA-F0-9]{40}\b"),
)


def _scrub_string_value(value: str) -> str:
    """Scrub known secret patterns from a string value."""
    for pattern in _FREE_TEXT_SECRET_PATTERNS:
        value = pattern.sub(r"\g<1>***REDACTED***", value)
    return value


def _structlog_secret_redactor(
    _logger: Any, _method: str, event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor: redact secret keys and free-text secrets."""
    for key in list(event_dict):
        key_lower = key.lower()
        if any(p in key_lower for p in SECRET_PATTERNS):
            event_dict[key] = "***REDACTED***"
        elif isinstance(event_dict[key], str):
            event_dict[key] = _scrub_string_value(event_dict[key])
    return event_dict


def redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively redact any values whose keys match secret patterns.
    DETERMINISTIC — no randomness.
    """
    redacted: dict[str, Any] = {}
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
