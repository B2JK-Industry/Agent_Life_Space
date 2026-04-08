"""
Agent Life Space — Tiered Log Retention.

Two retention tiers:

* **long** — kept for ``LONG_RETENTION_HOURS`` (default 30 days).
  Important operational and security history: agent boot, build/review
  jobs, deliveries, finance approvals, errors, criticals, audit events.

* **short** — kept for ``SHORT_RETENTION_HOURS`` (default 6 hours).
  Verbose, ephemeral diagnostics: dispatcher hits, cache hits, brain
  pipeline stage logs, typing indicators, polling. Useful only when
  actively debugging an issue, not worth keeping for the next sprint.

Tier resolution is *deterministic*:

1. If the log record's ``level`` is ``ERROR``, ``CRITICAL`` or
   ``AUDIT``, it always goes to **long**.
2. Otherwise, if the event name (or logger name) matches a long-tier
   prefix from ``_LONG_TIER_EVENTS``, it goes to **long**.
3. Otherwise, if the level is ``DEBUG`` or the event matches a
   short-tier prefix from ``_SHORT_TIER_EVENTS``, it goes to **short**.
4. INFO/WARNING that match neither rule default to **long** (we'd
   rather keep too much than lose history).

The categorisation is intentionally simple — every fancy classifier
adds room for bugs. Teams should evolve the prefix lists as their
operational experience grows.

This module also provides ``LogRetentionManager`` which deletes log
files past their tier retention window. It is invoked from the cron
loop on a regular schedule.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Tier configuration
# ─────────────────────────────────────────────────────────────────────

#: Long-term retention in hours. Default = 30 days.
LONG_RETENTION_HOURS = int(os.environ.get("AGENT_LOG_LONG_RETENTION_HOURS", "720"))

#: Short-term retention in hours. Default = 6 hours.
SHORT_RETENTION_HOURS = int(os.environ.get("AGENT_LOG_SHORT_RETENTION_HOURS", "6"))


# Event names (or prefixes) that should ALWAYS go to long-term, even
# if logged at INFO level. Anything related to:
#   - lifecycle (boot, shutdown)
#   - build / review / delivery jobs
#   - finance / approvals
#   - vault / secrets / auth
#   - security incidents
#   - persistence (storage, db)
_LONG_TIER_EVENTS: frozenset[str] = frozenset({
    # lifecycle
    "agent_started", "agent_stopped", "agent_initialized",
    "shutdown", "startup",
    # builds
    "build_started", "build_completed", "build_codegen_complete",
    "build_codegen_fallback", "build_failed", "codegen_fallback_guard",
    "build_acceptance", "build_delivery", "build_review",
    # reviews
    "review_started", "review_completed", "review_blocked",
    # deliveries / approvals
    "delivery_recorded", "delivery_handed_off", "approval_granted",
    "approval_denied", "approval_pending",
    # finance
    "finance_proposal", "finance_approved", "finance_completed",
    "finance_rejected", "finance_stale_cancelled",
    "budget_hard_cap", "budget_stop_loss",
    # vault / security
    "vault_initialized", "vault_secret_set", "vault_secret_get",
    "vault_secret_deleted", "auth_failure", "auth_success",
    "prompt_injection_blocked", "prompt_injection_soft",
    "command_blocked_non_owner",
    # gateway / external delivery
    "gateway_call_started", "gateway_call_succeeded",
    "gateway_call_failed", "gateway_denied",
    # cron / scheduled jobs
    "cron_health_alert_sent", "cron_morning_report_sent",
    "cron_data_cleanup_done", "cron_log_retention_pruned",
    # llm runtime
    "llm_runtime_state_updated", "llm_provider_created",
})


# Event names (or prefixes) that go to short-term — only useful while
# you are actively debugging.
_SHORT_TIER_EVENTS: frozenset[str] = frozenset({
    # brain pipeline internals
    "brain_pipeline_stage", "brain_dispatch_hit",
    "semantic_cache_hit", "semantic_cache_miss",
    "rag_retrieve",
    # routing details
    "router_classify", "tool_router_match",
    # noisy polling
    "telegram_poll_tick", "telegram_typing_tick",
    "agent_api_poll_tick",
    # job runner internals
    "job_runner_tick", "job_runner_dequeue",
    # health pings
    "watchdog_tick", "health_ping",
})


# ─────────────────────────────────────────────────────────────────────
# Tier resolution
# ─────────────────────────────────────────────────────────────────────


_TERMINAL_LONG_LEVELS = frozenset({"ERROR", "CRITICAL", "AUDIT"})
_DEBUG_LEVELS = frozenset({"DEBUG", "TRACE"})


def resolve_tier(level: str, event: str) -> str:
    """Return ``"long"`` or ``"short"``.

    Determinism rules — applied in order:

    1. ``ERROR`` / ``CRITICAL`` / ``AUDIT`` → long
    2. event listed in ``_LONG_TIER_EVENTS`` → long
    3. ``DEBUG`` / ``TRACE`` level → short
    4. event listed in ``_SHORT_TIER_EVENTS`` → short
    5. fallback (INFO/WARNING uncategorised) → long

    Both ``level`` and ``event`` are coerced to upper / lower case as
    needed before comparison so callers don't have to normalise.
    """
    lvl = (level or "").upper()
    evt = (event or "").lower()

    if lvl in _TERMINAL_LONG_LEVELS:
        return "long"
    if evt in _LONG_TIER_EVENTS:
        return "long"
    # Prefix match for grouped events (e.g. "build_*" → long).
    for prefix in ("build_", "review_", "delivery_", "approval_",
                   "finance_", "vault_", "auth_", "gateway_",
                   "security_", "incident_"):
        if evt.startswith(prefix):
            return "long"
    if lvl in _DEBUG_LEVELS:
        return "short"
    if evt in _SHORT_TIER_EVENTS:
        return "short"
    for prefix in ("brain_", "semantic_cache_", "rag_",
                   "router_", "telegram_poll_", "agent_api_poll_",
                   "watchdog_", "health_", "job_runner_"):
        if evt.startswith(prefix):
            return "short"
    return "long"


# ─────────────────────────────────────────────────────────────────────
# File retention
# ─────────────────────────────────────────────────────────────────────


@dataclass
class RetentionResult:
    """Outcome of a single retention sweep."""

    tier: str
    scanned: int
    pruned: int
    bytes_freed: int

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier,
            "scanned": self.scanned,
            "pruned": self.pruned,
            "bytes_freed": self.bytes_freed,
        }


class LogRetentionManager:
    """Deterministic file-based log retention sweeper.

    The cron loop calls ``prune_all()`` once per scheduled interval.
    The sweeper walks each tier directory, looks at file modification
    time, and deletes files older than the configured retention.

    No randomness, no globbing surprises — every operation is logged
    via structlog so the next sweep can reason about the previous one.
    """

    def __init__(
        self,
        log_dir: Path | str,
        long_retention_hours: int = LONG_RETENTION_HOURS,
        short_retention_hours: int = SHORT_RETENTION_HOURS,
    ) -> None:
        if long_retention_hours <= 0:
            msg = f"long_retention_hours must be > 0 (got {long_retention_hours})"
            raise ValueError(msg)
        if short_retention_hours <= 0:
            msg = f"short_retention_hours must be > 0 (got {short_retention_hours})"
            raise ValueError(msg)

        self._log_dir = Path(log_dir)
        self._long_retention_seconds = long_retention_hours * 3600
        self._short_retention_seconds = short_retention_hours * 3600

    @property
    def long_dir(self) -> Path:
        return self._log_dir / "long"

    @property
    def short_dir(self) -> Path:
        return self._log_dir / "short"

    def ensure_dirs(self) -> None:
        """Create the tier subdirectories if they don't exist."""
        self.long_dir.mkdir(parents=True, exist_ok=True)
        self.short_dir.mkdir(parents=True, exist_ok=True)

    def _prune_dir(
        self,
        tier: str,
        directory: Path,
        max_age_seconds: int,
        now: float | None = None,
    ) -> RetentionResult:
        scanned = 0
        pruned = 0
        bytes_freed = 0
        if not directory.is_dir():
            return RetentionResult(tier=tier, scanned=0, pruned=0, bytes_freed=0)

        cutoff = (now or time.time()) - max_age_seconds
        for entry in self._iter_log_files(directory):
            scanned += 1
            try:
                stat = entry.stat()
            except FileNotFoundError:
                # Concurrent rotation removed it — fine.
                continue
            if stat.st_mtime >= cutoff:
                continue
            try:
                entry.unlink()
            except FileNotFoundError:
                continue
            pruned += 1
            bytes_freed += stat.st_size

        if pruned:
            logger.info(
                "log_retention_pruned",
                tier=tier,
                scanned=scanned,
                pruned=pruned,
                bytes_freed=bytes_freed,
                directory=str(directory),
            )
        return RetentionResult(
            tier=tier,
            scanned=scanned,
            pruned=pruned,
            bytes_freed=bytes_freed,
        )

    @staticmethod
    def _iter_log_files(directory: Path) -> Iterable[Path]:
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            # Only sweep our own log files; refuse to touch random files
            # the operator might have dropped in the directory.
            if entry.suffix in (".log", ".jsonl") or entry.name.endswith(".log.gz"):
                yield entry

    def prune_all(self, now: float | None = None) -> dict[str, RetentionResult]:
        """Sweep both tiers. Returns per-tier results."""
        self.ensure_dirs()
        return {
            "long": self._prune_dir(
                tier="long",
                directory=self.long_dir,
                max_age_seconds=self._long_retention_seconds,
                now=now,
            ),
            "short": self._prune_dir(
                tier="short",
                directory=self.short_dir,
                max_age_seconds=self._short_retention_seconds,
                now=now,
            ),
        }

    def total_size_bytes(self) -> dict[str, int]:
        """Return current on-disk size per tier (best-effort)."""
        out = {"long": 0, "short": 0}
        for tier_name, directory in (("long", self.long_dir), ("short", self.short_dir)):
            if not directory.is_dir():
                continue
            for entry in self._iter_log_files(directory):
                try:
                    out[tier_name] += entry.stat().st_size
                except FileNotFoundError:
                    continue
        return out
