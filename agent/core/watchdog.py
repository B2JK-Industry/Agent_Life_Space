"""
Agent Life Space — Watchdog

Module health monitoring via heartbeat policy.

What it does:
    - Monitors heartbeats from registered modules
    - Marks unresponsive modules: DEGRADED → UNHEALTHY → UNRESPONSIVE
    - Invokes restart callback for unresponsive modules (if configured)
    - Tracks system resources (CPU, memory, disk)
    - Generates alerts with deduplication and expiry

What it does NOT do:
    - Does not kill OS processes (no os.kill, no signal sending)
    - Does not verify process liveness at OS level
    - "UNRESPONSIVE" means "no heartbeat received", not "process exited"

Design notes:
    - Pure Python (Rust rewrite possible for sub-ms monitoring later)
    - psutil.cpu_percent(interval=0) can be noisy on first call — accepted tradeoff
    - Restart cooldown prevents restart loops
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine

import psutil
import structlog

logger = structlog.get_logger(__name__)


class ModuleState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"       # 1-2× timeout without heartbeat
    UNHEALTHY = "unhealthy"     # 2-3× timeout without heartbeat
    UNRESPONSIVE = "unresponsive"  # >3× timeout — restart candidate
    UNKNOWN = "unknown"


@dataclass
class ModuleInfo:
    """Tracked info for a monitored module."""

    name: str
    last_heartbeat: float = 0.0
    state: ModuleState = ModuleState.UNKNOWN
    heartbeat_timeout: float = 30.0
    missed_heartbeats: int = 0  # Count of check cycles without heartbeat
    restart_callback: Callable[[], Coroutine[Any, Any, None]] | None = None
    restart_count: int = 0
    max_restarts: int = 5
    last_restart_time: float = 0.0
    restart_cooldown: float = 60.0


@dataclass
class SystemHealth:
    """System health snapshot. Created by snapshot_health(), read by get_last_health()."""

    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_available_mb: float = 0.0
    disk_percent: float = 0.0
    modules: dict[str, str] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)


@dataclass
class Alert:
    """Deduplicated alert with timestamp."""

    alert_type: str
    module: str
    message: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    count: int = 1  # How many times this alert fired


class Watchdog:
    """
    Monitors module heartbeats and system resources.
    Invokes restart callbacks for unresponsive modules.
    """

    def __init__(
        self,
        check_interval: float = 5.0,
        cpu_threshold: float = 90.0,
        memory_threshold: float = 85.0,
        max_alerts: int = 200,
    ) -> None:
        self._modules: dict[str, ModuleInfo] = {}
        self._check_interval = check_interval
        self._cpu_threshold = cpu_threshold
        self._memory_threshold = memory_threshold
        self._running = False
        self._health_history: list[SystemHealth] = []
        self._max_history = 100
        # Deduplicated alerts: key = (type, module)
        self._alerts: dict[tuple[str, str], Alert] = {}
        self._max_alerts = max_alerts

    def register_module(
        self,
        name: str,
        heartbeat_timeout: float = 30.0,
        restart_callback: Callable[[], Coroutine[Any, Any, None]] | None = None,
        max_restarts: int = 5,
        restart_cooldown: float = 60.0,
    ) -> None:
        """Register a module for monitoring."""
        self._modules[name] = ModuleInfo(
            name=name,
            last_heartbeat=time.monotonic(),
            state=ModuleState.HEALTHY,
            heartbeat_timeout=heartbeat_timeout,
            restart_callback=restart_callback,
            max_restarts=max_restarts,
            restart_cooldown=restart_cooldown,
        )
        logger.info("watchdog_module_registered", module=name)

    def heartbeat(self, name: str) -> None:
        """
        Receive heartbeat from a module.
        Resets missed heartbeat counter and recovers state.
        """
        if name in self._modules:
            module = self._modules[name]
            module.last_heartbeat = time.monotonic()
            module.missed_heartbeats = 0
            if module.state in (ModuleState.DEGRADED, ModuleState.UNHEALTHY):
                module.state = ModuleState.HEALTHY
                logger.info("module_recovered", module=name)

    def check_module_health(self, name: str) -> ModuleState:
        """
        Check module state based on heartbeat policy. DETERMINISTIC.
        Does NOT have side effects on alert list or health history.
        """
        if name not in self._modules:
            return ModuleState.UNKNOWN

        module = self._modules[name]
        elapsed = time.monotonic() - module.last_heartbeat

        if elapsed < module.heartbeat_timeout:
            module.state = ModuleState.HEALTHY
        elif elapsed < module.heartbeat_timeout * 2:
            module.state = ModuleState.DEGRADED
        elif elapsed < module.heartbeat_timeout * 3:
            module.state = ModuleState.UNHEALTHY
        else:
            module.state = ModuleState.UNRESPONSIVE

        return module.state

    def snapshot_health(self) -> SystemHealth:
        """
        Take a system health snapshot. Has side effects:
        - Checks all module states
        - Records to history
        - Generates alerts for threshold breaches

        Named 'snapshot' (not 'get') to signal it's not a pure getter.
        """
        mem = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage("/")
            disk_percent = disk.percent
        except (OSError, PermissionError):
            disk_percent = 0.0

        current_alerts: list[str] = []

        cpu = psutil.cpu_percent(interval=0)
        if cpu > self._cpu_threshold:
            msg = f"CPU at {cpu:.1f}% (threshold: {self._cpu_threshold}%)"
            current_alerts.append(msg)

        if mem.percent > self._memory_threshold:
            msg = f"Memory at {mem.percent:.1f}% (threshold: {self._memory_threshold}%)"
            current_alerts.append(msg)

        module_states = {}
        for name in self._modules:
            state = self.check_module_health(name)
            module_states[name] = state.value
            if state in (ModuleState.UNHEALTHY, ModuleState.UNRESPONSIVE):
                current_alerts.append(f"Module '{name}' is {state.value}")

        health = SystemHealth(
            cpu_percent=cpu,
            memory_percent=mem.percent,
            memory_used_mb=round(mem.used / (1024 * 1024), 1),
            memory_available_mb=round(mem.available / (1024 * 1024), 1),
            disk_percent=disk_percent,
            modules=module_states,
            alerts=current_alerts,
        )

        self._health_history.append(health)
        if len(self._health_history) > self._max_history:
            self._health_history.pop(0)

        return health

    def get_last_health(self) -> SystemHealth | None:
        """Pure getter — returns the last snapshot, no side effects."""
        return self._health_history[-1] if self._health_history else None

    # Keep backward compat alias
    get_system_health = snapshot_health

    async def _check_and_restart(self) -> None:
        """Check all modules and invoke restart callbacks for unresponsive ones."""
        for name, module in self._modules.items():
            state = self.check_module_health(name)

            if state == ModuleState.UNRESPONSIVE:
                module.missed_heartbeats += 1
                logger.error(
                    "module_unresponsive",
                    module=name,
                    missed_heartbeats=module.missed_heartbeats,
                    restarts=module.restart_count,
                )

                if (
                    module.restart_callback
                    and module.restart_count < module.max_restarts
                ):
                    now = time.monotonic()
                    since_last = now - module.last_restart_time
                    if since_last < module.restart_cooldown:
                        logger.warning(
                            "module_restart_cooldown",
                            module=name,
                            wait_seconds=round(module.restart_cooldown - since_last, 1),
                        )
                        continue

                    try:
                        logger.info(
                            "module_restarting",
                            module=name,
                            attempt=module.restart_count + 1,
                        )
                        await module.restart_callback()
                        module.restart_count += 1
                        module.last_heartbeat = now
                        module.last_restart_time = now
                        module.missed_heartbeats = 0  # Reset on successful restart
                        module.state = ModuleState.HEALTHY
                        logger.info("module_restarted", module=name)
                    except Exception as e:
                        logger.error(
                            "module_restart_failed",
                            module=name,
                            error=str(e),
                        )
                        self._add_alert("restart_failed", name, str(e))
                elif module.restart_count >= module.max_restarts:
                    self._add_alert(
                        "max_restarts_exceeded",
                        name,
                        f"Module '{name}' exceeded {module.max_restarts} restarts",
                    )

    def _add_alert(self, alert_type: str, module: str, message: str) -> None:
        """Add or deduplicate an alert."""
        key = (alert_type, module)
        existing = self._alerts.get(key)
        if existing:
            existing.count += 1
            existing.timestamp = datetime.now(timezone.utc).isoformat()
        else:
            if len(self._alerts) >= self._max_alerts:
                # Evict oldest
                oldest_key = next(iter(self._alerts))
                del self._alerts[oldest_key]
            self._alerts[key] = Alert(
                alert_type=alert_type,
                module=module,
                message=message,
            )

    async def start(self) -> None:
        """Start the watchdog monitoring loop."""
        if self._running:
            logger.warning("watchdog_already_running")
            return

        self._running = True
        logger.info("watchdog_started", interval=self._check_interval)

        while self._running:
            try:
                await self._check_and_restart()
                self.snapshot_health()
            except Exception:
                logger.exception("watchdog_check_error")

            await asyncio.sleep(self._check_interval)

    async def stop(self) -> None:
        """Stop the watchdog."""
        self._running = False
        logger.info("watchdog_stopped")

    def get_alerts(self) -> list[dict[str, Any]]:
        """Return deduplicated alerts."""
        return [
            {
                "type": alert.alert_type,
                "module": alert.module,
                "message": alert.message,
                "timestamp": alert.timestamp,
                "count": alert.count,
            }
            for alert in self._alerts.values()
        ]

    def get_module_states(self) -> dict[str, str]:
        return {
            name: self.check_module_health(name).value
            for name in self._modules
        }

    def get_stats(self) -> dict[str, Any]:
        return {
            "modules_registered": len(self._modules),
            "modules_healthy": sum(
                1
                for name in self._modules
                if self.check_module_health(name) == ModuleState.HEALTHY
            ),
            "total_alerts": sum(a.count for a in self._alerts.values()),
            "unique_alerts": len(self._alerts),
            "health_snapshots": len(self._health_history),
        }
