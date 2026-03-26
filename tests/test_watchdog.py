"""
Test scenarios for Watchdog.

1. Heartbeat keeps modules healthy
2. Missing heartbeat → degraded → unhealthy → unresponsive
3. Unresponsive modules get restart callback invoked (not killed)
4. Max restart limit respected
5. Restart cooldown prevents restart loops
6. Alerts are deduplicated
7. Double start is guarded
8. snapshot_health has side effects, get_last_health does not
9. missed_heartbeats reset on successful restart
"""

from __future__ import annotations

import time

import pytest

from agent.core.watchdog import ModuleState, Watchdog


@pytest.fixture
def watchdog() -> Watchdog:
    return Watchdog(check_interval=1.0)


class TestHeartbeat:
    """Heartbeat monitoring is the core anti-hang mechanism."""

    def test_registered_module_starts_healthy(self, watchdog: Watchdog) -> None:
        watchdog.register_module("brain", heartbeat_timeout=10.0)
        assert watchdog.check_module_health("brain") == ModuleState.HEALTHY

    def test_heartbeat_keeps_healthy(self, watchdog: Watchdog) -> None:
        watchdog.register_module("brain", heartbeat_timeout=10.0)
        watchdog.heartbeat("brain")
        assert watchdog.check_module_health("brain") == ModuleState.HEALTHY

    def test_missing_heartbeat_degrades(self, watchdog: Watchdog) -> None:
        watchdog.register_module("brain", heartbeat_timeout=1.0)
        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 1.5
        assert watchdog.check_module_health("brain") == ModuleState.DEGRADED

    def test_long_missing_heartbeat_unhealthy(self, watchdog: Watchdog) -> None:
        watchdog.register_module("brain", heartbeat_timeout=1.0)
        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 2.5
        assert watchdog.check_module_health("brain") == ModuleState.UNHEALTHY

    def test_very_long_missing_heartbeat_unresponsive(self, watchdog: Watchdog) -> None:
        watchdog.register_module("brain", heartbeat_timeout=1.0)
        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 5.0
        assert watchdog.check_module_health("brain") == ModuleState.UNRESPONSIVE

    def test_unknown_module(self, watchdog: Watchdog) -> None:
        assert watchdog.check_module_health("nonexistent") == ModuleState.UNKNOWN

    def test_heartbeat_recovers_degraded(self, watchdog: Watchdog) -> None:
        watchdog.register_module("brain", heartbeat_timeout=1.0)
        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 1.5
        watchdog.check_module_health("brain")
        assert watchdog._modules["brain"].state == ModuleState.DEGRADED

        watchdog.heartbeat("brain")
        assert watchdog._modules["brain"].state == ModuleState.HEALTHY
        assert watchdog._modules["brain"].missed_heartbeats == 0


class TestModuleRestart:
    """Unresponsive modules get restart callback invoked (not killed)."""

    @pytest.mark.asyncio
    async def test_unresponsive_module_restarted(self, watchdog: Watchdog) -> None:
        restart_called = [False]

        async def restart() -> None:
            restart_called[0] = True

        watchdog.register_module("brain", heartbeat_timeout=1.0, restart_callback=restart)
        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 5.0

        await watchdog._check_and_restart()
        assert restart_called[0] is True
        assert watchdog._modules["brain"].restart_count == 1
        assert watchdog._modules["brain"].missed_heartbeats == 0  # Reset on success

    @pytest.mark.asyncio
    async def test_max_restarts_respected(self, watchdog: Watchdog) -> None:
        async def restart() -> None:
            pass

        watchdog.register_module(
            "brain", heartbeat_timeout=1.0, restart_callback=restart, max_restarts=2,
        )
        watchdog._modules["brain"].restart_count = 2
        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 5.0

        await watchdog._check_and_restart()
        assert watchdog._modules["brain"].restart_count == 2
        alerts = watchdog.get_alerts()
        assert any(a["type"] == "max_restarts_exceeded" for a in alerts)


class TestRestartCooldown:
    """Prevent restart loops with cooldown."""

    @pytest.mark.asyncio
    async def test_cooldown_prevents_rapid_restart(self, watchdog: Watchdog) -> None:
        restart_count = [0]

        async def restart() -> None:
            restart_count[0] += 1

        watchdog.register_module(
            "brain", heartbeat_timeout=1.0, restart_callback=restart, restart_cooldown=60.0,
        )
        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 5.0

        await watchdog._check_and_restart()
        assert restart_count[0] == 1

        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 5.0
        await watchdog._check_and_restart()
        assert restart_count[0] == 1  # Blocked by cooldown


class TestAlertDeduplication:
    """Alerts must not grow unboundedly."""

    @pytest.mark.asyncio
    async def test_alerts_deduplicated(self, watchdog: Watchdog) -> None:
        async def restart() -> None:
            pass

        watchdog.register_module(
            "brain", heartbeat_timeout=1.0, restart_callback=restart, max_restarts=0,
        )
        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 5.0

        # Trigger same alert multiple times
        await watchdog._check_and_restart()
        await watchdog._check_and_restart()
        await watchdog._check_and_restart()

        alerts = watchdog.get_alerts()
        max_restart_alerts = [a for a in alerts if a["type"] == "max_restarts_exceeded"]
        assert len(max_restart_alerts) == 1  # Deduplicated
        assert max_restart_alerts[0]["count"] == 3  # But count tracks occurrences


class TestDoubleStart:
    """Starting twice must not create two loops."""

    @pytest.mark.asyncio
    async def test_double_start_guarded(self, watchdog: Watchdog) -> None:

        watchdog._running = True
        # Second start should return immediately (logged warning)
        await watchdog.start()  # Should be a no-op since already running
        # If we get here without hanging, the guard works


class TestHealthSnapshot:
    """snapshot_health has side effects, get_last_health is pure getter."""

    def test_snapshot_has_metrics(self, watchdog: Watchdog) -> None:
        health = watchdog.snapshot_health()
        assert health.cpu_percent >= 0
        assert health.memory_percent >= 0
        assert health.memory_used_mb > 0

    def test_snapshot_records_history(self, watchdog: Watchdog) -> None:
        watchdog.snapshot_health()
        watchdog.snapshot_health()
        assert len(watchdog._health_history) == 2

    def test_get_last_health_pure_getter(self, watchdog: Watchdog) -> None:
        """get_last_health does not create new snapshots."""
        assert watchdog.get_last_health() is None
        watchdog.snapshot_health()
        h = watchdog.get_last_health()
        assert h is not None
        assert len(watchdog._health_history) == 1  # No extra snapshot

    def test_snapshot_includes_module_states(self, watchdog: Watchdog) -> None:
        watchdog.register_module("brain")
        watchdog.register_module("memory")
        health = watchdog.snapshot_health()
        assert "brain" in health.modules
        assert "memory" in health.modules

    def test_alerts_on_unresponsive_module(self, watchdog: Watchdog) -> None:
        watchdog.register_module("brain", heartbeat_timeout=1.0)
        watchdog._modules["brain"].last_heartbeat = time.monotonic() - 5.0
        health = watchdog.snapshot_health()
        assert any("brain" in alert for alert in health.alerts)


class TestWatchdogStats:
    def test_stats(self, watchdog: Watchdog) -> None:
        watchdog.register_module("brain")
        watchdog.register_module("memory")
        stats = watchdog.get_stats()
        assert stats["modules_registered"] == 2
        assert stats["modules_healthy"] == 2
        assert stats["unique_alerts"] == 0
