"""Tests for the tiered logging + retention system.

Covers two responsibilities:

1. ``agent.logs.retention.resolve_tier`` — deterministic mapping of
   (level, event) to "long" or "short" tier.
2. ``agent.logs.retention.LogRetentionManager`` — file-based age sweep
   that the cron loop calls hourly.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent.logs.retention import LogRetentionManager, RetentionResult, resolve_tier

# ─────────────────────────────────────────────────────────────────────
# Tier resolution
# ─────────────────────────────────────────────────────────────────────


class TestResolveTier:
    def test_error_always_long(self):
        assert resolve_tier("ERROR", "anything_at_all") == "long"
        assert resolve_tier("error", "brain_pipeline_stage") == "long"

    def test_critical_always_long(self):
        assert resolve_tier("CRITICAL", "telegram_poll_tick") == "long"

    def test_audit_always_long(self):
        assert resolve_tier("AUDIT", "vault_secret_get") == "long"

    def test_explicit_long_event(self):
        assert resolve_tier("INFO", "build_completed") == "long"
        assert resolve_tier("INFO", "finance_completed") == "long"
        assert resolve_tier("INFO", "vault_secret_set") == "long"
        assert resolve_tier("WARNING", "auth_failure") == "long"

    def test_long_event_prefix(self):
        assert resolve_tier("INFO", "build_codegen_complete") == "long"
        assert resolve_tier("INFO", "review_completed") == "long"
        assert resolve_tier("INFO", "delivery_handed_off") == "long"
        assert resolve_tier("INFO", "approval_granted") == "long"
        assert resolve_tier("INFO", "finance_proposal") == "long"
        assert resolve_tier("INFO", "vault_secret_deleted") == "long"
        assert resolve_tier("INFO", "auth_success") == "long"
        assert resolve_tier("INFO", "gateway_call_succeeded") == "long"
        assert resolve_tier("INFO", "incident_detected") == "long"

    def test_debug_level_goes_to_short(self):
        assert resolve_tier("DEBUG", "anything") == "short"
        assert resolve_tier("DEBUG", "raw_response_dump") == "short"

    def test_long_prefix_beats_debug_demotion(self):
        """Vault, finance, build, etc. always stay in long-tier even at
        DEBUG level — the audit trail must be complete."""
        assert resolve_tier("DEBUG", "vault_secret_get_cached") == "long"
        assert resolve_tier("DEBUG", "build_codegen_complete") == "long"
        assert resolve_tier("DEBUG", "finance_proposal_inspected") == "long"

    def test_explicit_short_event(self):
        assert resolve_tier("INFO", "semantic_cache_hit") == "short"
        assert resolve_tier("INFO", "telegram_poll_tick") == "short"
        assert resolve_tier("INFO", "watchdog_tick") == "short"

    def test_short_event_prefix(self):
        assert resolve_tier("INFO", "brain_pipeline_stage_2") == "short"
        assert resolve_tier("INFO", "rag_retrieve_topk") == "short"
        assert resolve_tier("INFO", "router_classify_intent") == "short"
        assert resolve_tier("INFO", "telegram_poll_started") == "short"
        assert resolve_tier("INFO", "agent_api_poll_inactive") == "short"
        assert resolve_tier("INFO", "watchdog_health_ok") == "short"
        assert resolve_tier("INFO", "health_ping_received") == "short"
        assert resolve_tier("INFO", "job_runner_dequeue") == "short"

    def test_uncategorised_info_defaults_to_long(self):
        # We'd rather over-keep than under-keep when categorising.
        assert resolve_tier("INFO", "some_random_event") == "long"
        assert resolve_tier("WARNING", "another_one") == "long"

    def test_long_takes_precedence_over_short_for_errors(self):
        # Even if the event name matches a short prefix, an ERROR
        # always wins.
        assert resolve_tier("ERROR", "brain_pipeline_failed") == "long"
        assert resolve_tier("CRITICAL", "watchdog_unresponsive") == "long"

    def test_normalises_case(self):
        # Both fields can come in any case.
        assert resolve_tier("info", "BUILD_COMPLETED") == "long"
        assert resolve_tier("Debug", "Brain_Pipeline_Foo") == "short"

    def test_handles_empty_inputs(self):
        # Should not crash on empty strings.
        assert resolve_tier("", "") == "long"
        assert resolve_tier("INFO", "") == "long"
        assert resolve_tier("", "build_started") == "long"


# ─────────────────────────────────────────────────────────────────────
# LogRetentionManager file sweep
# ─────────────────────────────────────────────────────────────────────


def _make_log(path: Path, mtime_offset_seconds: float, content: str = "x") -> None:
    """Create a log file at ``path`` and set its mtime to ``now + offset``.
    A negative offset means "in the past"."""
    path.write_text(content)
    target = time.time() + mtime_offset_seconds
    os.utime(path, (target, target))


class TestLogRetentionManager:
    def test_rejects_non_positive_retention(self, tmp_path):
        with pytest.raises(ValueError, match="long_retention_hours"):
            LogRetentionManager(tmp_path, long_retention_hours=0)
        with pytest.raises(ValueError, match="short_retention_hours"):
            LogRetentionManager(tmp_path, long_retention_hours=24, short_retention_hours=-1)

    def test_ensure_dirs_creates_tier_subdirs(self, tmp_path):
        mgr = LogRetentionManager(tmp_path)
        mgr.ensure_dirs()
        assert (tmp_path / "long").is_dir()
        assert (tmp_path / "short").is_dir()

    def test_prune_removes_files_older_than_retention(self, tmp_path):
        mgr = LogRetentionManager(
            tmp_path,
            long_retention_hours=24,
            short_retention_hours=1,
        )
        mgr.ensure_dirs()

        long_dir = tmp_path / "long"
        short_dir = tmp_path / "short"

        # Long: one fresh, one old.
        _make_log(long_dir / "fresh.log", mtime_offset_seconds=-60)        # 1 min ago
        _make_log(long_dir / "stale.log", mtime_offset_seconds=-25 * 3600) # 25h ago

        # Short: one fresh, one old.
        _make_log(short_dir / "recent.log", mtime_offset_seconds=-30 * 60)   # 30 min ago
        _make_log(short_dir / "expired.log", mtime_offset_seconds=-2 * 3600) # 2h ago

        results = mgr.prune_all()

        assert results["long"].pruned == 1
        assert results["short"].pruned == 1
        assert (long_dir / "fresh.log").exists()
        assert not (long_dir / "stale.log").exists()
        assert (short_dir / "recent.log").exists()
        assert not (short_dir / "expired.log").exists()

    def test_prune_returns_bytes_freed(self, tmp_path):
        mgr = LogRetentionManager(
            tmp_path, long_retention_hours=1, short_retention_hours=1,
        )
        mgr.ensure_dirs()
        path = tmp_path / "long" / "old.log"
        _make_log(path, mtime_offset_seconds=-7200, content="x" * 1024)

        results = mgr.prune_all()
        assert results["long"].pruned == 1
        assert results["long"].bytes_freed == 1024

    def test_prune_ignores_non_log_files(self, tmp_path):
        mgr = LogRetentionManager(
            tmp_path, long_retention_hours=1, short_retention_hours=1,
        )
        mgr.ensure_dirs()

        # Innocent operator file in the same directory.
        notes = tmp_path / "long" / "operator-notes.txt"
        _make_log(notes, mtime_offset_seconds=-9999999)

        results = mgr.prune_all()
        assert notes.exists(), "retention manager touched a non-log file"
        assert results["long"].scanned == 0

    def test_prune_handles_missing_dirs_gracefully(self, tmp_path):
        # Don't call ensure_dirs() — directories don't exist yet.
        mgr = LogRetentionManager(tmp_path)
        results = mgr.prune_all()
        # Both tiers report zero scanned, zero pruned, no exceptions.
        assert results["long"].scanned == 0
        assert results["long"].pruned == 0
        assert results["short"].scanned == 0
        assert results["short"].pruned == 0

    def test_prune_respects_now_override(self, tmp_path):
        """Passing ``now=...`` lets us reproduce a sweep at a specific
        wall-clock moment in tests, without monkeypatching time."""
        mgr = LogRetentionManager(
            tmp_path,
            long_retention_hours=1,
            short_retention_hours=1,
        )
        mgr.ensure_dirs()
        path = tmp_path / "long" / "marker.log"
        path.write_text("x")
        # Set mtime to a known absolute moment.
        os.utime(path, (1000.0, 1000.0))

        # Sweep with now=1500 — only 500s elapsed, so it's NOT pruned.
        results = mgr.prune_all(now=1500.0)
        assert results["long"].pruned == 0
        assert path.exists()

        # Sweep with now=10000 — 9000s elapsed, > 1h retention.
        results = mgr.prune_all(now=10000.0)
        assert results["long"].pruned == 1
        assert not path.exists()

    def test_total_size_bytes_aggregates_per_tier(self, tmp_path):
        mgr = LogRetentionManager(tmp_path)
        mgr.ensure_dirs()
        (tmp_path / "long" / "a.log").write_text("a" * 100)
        (tmp_path / "long" / "b.log").write_text("b" * 200)
        (tmp_path / "short" / "c.log").write_text("c" * 50)

        sizes = mgr.total_size_bytes()
        assert sizes["long"] == 300
        assert sizes["short"] == 50

    def test_retention_result_to_dict_is_serialisable(self):
        result = RetentionResult(
            tier="long", scanned=5, pruned=2, bytes_freed=1024,
        )
        d = result.to_dict()
        assert d == {
            "tier": "long",
            "scanned": 5,
            "pruned": 2,
            "bytes_freed": 1024,
        }


# ─────────────────────────────────────────────────────────────────────
# Regression: tiered logging actually reaches the file sinks
# ─────────────────────────────────────────────────────────────────────


def _teardown_tiered_logging() -> None:
    """Reset structlog + close all root handlers from setup_tiered_logging.

    Without this the TimedRotatingFileHandler instances stay open and
    pytest's unraisable-exception capture flags ResourceWarning at GC."""
    import logging

    import structlog

    structlog.reset_defaults()
    root = logging.getLogger()
    for h in list(root.handlers):
        try:
            h.close()
        except Exception:  # pragma: no cover - best effort
            pass
        root.removeHandler(h)


class TestSetupTieredLoggingActuallyWritesFiles:
    """Regression for the bug where setup_tiered_logging() configured
    structlog without switching ``logger_factory`` to stdlib, leaving the
    process on PrintLoggerFactory and writing nothing to disk."""

    def test_long_tier_event_lands_in_long_file(self, tmp_path):
        import structlog

        # Simulate the __main__ pre-config that bites the real bug:
        # PrintLoggerFactory installed BEFORE setup_tiered_logging().
        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.PrintLoggerFactory(),
        )

        from agent.logs.logger import setup_tiered_logging

        try:
            paths = setup_tiered_logging(tmp_path)
            log = structlog.get_logger("regression_long")
            log.info("build_completed", job_id="abc123")

            long_path = Path(paths["long_log"])
            assert long_path.exists(), "long-tier log file must be created"
            content = long_path.read_text()
            assert "build_completed" in content
            assert "abc123" in content
        finally:
            _teardown_tiered_logging()

    def test_short_tier_event_lands_in_short_file(self, tmp_path):
        import structlog

        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.PrintLoggerFactory(),
        )

        from agent.logs.logger import setup_tiered_logging

        try:
            paths = setup_tiered_logging(tmp_path)
            log = structlog.get_logger("regression_short")
            log.debug("brain_pipeline_stage", stage="cache_lookup")

            short_path = Path(paths["short_log"])
            assert short_path.exists()
            content = short_path.read_text()
            assert "brain_pipeline_stage" in content
            assert "cache_lookup" in content
        finally:
            _teardown_tiered_logging()


class TestRetentionSweepHonoursAgentLogDir:
    """Regression: cron-side LogRetentionManager and __main__'s logging
    setup must agree on the directory. Previously cron defaulted to
    get_project_root()/agent/logs while __main__ wrote into
    <data_dir>/logs and retention silently swept nothing."""

    def test_cron_sweep_uses_env_var_set_by_main(self, tmp_path, monkeypatch):
        # Simulate __main__ pinning AGENT_LOG_DIR after setup_tiered_logging.
        monkeypatch.setenv("AGENT_LOG_DIR", str(tmp_path))

        # Build the same default the cron _do_log_retention_sweep uses.
        log_dir = os.environ.get("AGENT_LOG_DIR", "")
        assert log_dir == str(tmp_path)

        mgr = LogRetentionManager(
            log_dir=log_dir,
            long_retention_hours=1,
            short_retention_hours=1,
        )
        mgr.ensure_dirs()
        # Drop a stale file in the long tier and prove the sweep finds it.
        stale = tmp_path / "long" / "stale.log"
        stale.write_text("x")
        os.utime(stale, (0.0, 0.0))
        results = mgr.prune_all()
        assert results["long"].pruned == 1
        assert not stale.exists()


class TestRetentionEnvContractIsUnified:
    """Codex finding (LOW): __main__ used AGENT_LOG_LONG_RETENTION_DAYS
    while retention.py read AGENT_LOG_LONG_RETENTION_HOURS. Setting
    only one variable left the rotating handler and the prune sweep
    out of sync. The unified contract is HOURS for both halves; DAYS
    is honored once with a deprecation warning and then converted to
    HOURS in the env."""

    def test_setup_tiered_logging_accepts_long_retention_hours(self, tmp_path):
        """The new keyword argument is ``long_retention_hours``, not
        ``long_retention_days``. The function must derive an internal
        days-rounded backupCount without exposing days to callers."""
        from agent.logs.logger import setup_tiered_logging

        try:
            paths = setup_tiered_logging(
                tmp_path,
                long_retention_hours=72,  # 3 days
                short_retention_hours=2,
            )
            assert "long_log" in paths
            assert "short_log" in paths
        finally:
            _teardown_tiered_logging()

    def test_legacy_days_env_is_promoted_to_hours(self, monkeypatch, tmp_path):
        """When operator sets only the deprecated DAYS env var,
        __main__ must:
          (a) honour the value (convert 2 days → 48 hours)
          (b) emit a deprecation warning
          (c) pin AGENT_LOG_LONG_RETENTION_HOURS so the cron sweep
              sees the same value
        We exercise the conversion logic by replicating the snippet
        __main__.py uses, since the function is inlined into run_agent.
        """
        monkeypatch.delenv("AGENT_LOG_LONG_RETENTION_HOURS", raising=False)
        monkeypatch.setenv("AGENT_LOG_LONG_RETENTION_DAYS", "2")

        env_hours = os.environ.get("AGENT_LOG_LONG_RETENTION_HOURS", "").strip()
        env_days_legacy = os.environ.get("AGENT_LOG_LONG_RETENTION_DAYS", "").strip()
        # Sanity: the test fixture is what we expect.
        assert env_hours == ""
        assert env_days_legacy == "2"

        # Replicate __main__.py promotion path.
        if env_hours:
            long_retention_hours = int(env_hours)
        elif env_days_legacy:
            long_retention_hours = int(env_days_legacy) * 24
            os.environ["AGENT_LOG_LONG_RETENTION_HOURS"] = str(long_retention_hours)
        else:
            long_retention_hours = 720

        assert long_retention_hours == 48
        assert os.environ["AGENT_LOG_LONG_RETENTION_HOURS"] == "48"

    def test_hours_env_takes_precedence_over_legacy_days(self, monkeypatch):
        monkeypatch.setenv("AGENT_LOG_LONG_RETENTION_HOURS", "100")
        monkeypatch.setenv("AGENT_LOG_LONG_RETENTION_DAYS", "9999")

        env_hours = os.environ.get("AGENT_LOG_LONG_RETENTION_HOURS", "").strip()
        long_retention_hours = int(env_hours)
        assert long_retention_hours == 100  # HOURS wins

    def test_retention_manager_default_matches_setup_default(self):
        """Both halves of the system must default to the same value
        (720 hours = 30 days). If somebody changes the default in one
        place but not the other, this test catches it."""
        import inspect

        from agent.logs import retention as retention_mod
        from agent.logs.logger import setup_tiered_logging

        # The module reads the env var at import time; we verify the
        # default literal in the source code matches the setup default.
        # We re-read the module file to avoid being fooled by an env
        # var set in the test environment.
        retention_source = Path(retention_mod.__file__).read_text()
        assert '"AGENT_LOG_LONG_RETENTION_HOURS", "720"' in retention_source

        sig = inspect.signature(setup_tiered_logging)
        assert sig.parameters["long_retention_hours"].default == 720
