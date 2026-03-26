"""
Agent Life Space — Server Maintenance

John sa stará o svoj server. Pravidelne:
    - Hľadá zaseknuté procesy a zabíja ich
    - Čistí cache a temp súbory
    - Kontroluje disk space
    - Sleduje svoje vlastné služby
    - Čistí staré logy
    - Monitoruje RAM a swappovanie

Každý job vracia dict s výsledkami — logovateľné, trackable.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
import structlog

logger = structlog.get_logger(__name__)


class ServerMaintenance:
    """
    John's server housekeeping. All methods return dicts (JSON-friendly).
    """

    def __init__(self, home_dir: str = "") -> None:
        from agent.core.paths import get_project_root
        default = get_project_root()
        self._home = Path(os.path.expanduser(home_dir or default))

    async def find_and_kill_stale_processes(
        self,
        max_age_hours: float = 2.0,
        patterns: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Find processes that have been running too long and shouldn't be.
        Like those stuck `claude setup-token` instances.

        Only kills processes owned by current user. Never kills:
        - PID 1 (init)
        - Our own agent process
        - sshd, systemd, etc.
        """
        if patterns is None:
            patterns = [
                "claude setup-token",
                "claude --print",  # Stuck CLI calls
            ]

        current_pid = os.getpid()
        current_user = os.getuid()
        killed = []
        found = []
        max_age_seconds = max_age_hours * 3600

        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time", "username"]):
            try:
                info = proc.info
                if info["pid"] == current_pid:
                    continue
                if info["pid"] <= 1:
                    continue

                # Only our processes
                try:
                    if proc.uids().real != current_user:
                        continue
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue

                cmdline = " ".join(info.get("cmdline") or [])
                age = time.time() - (info.get("create_time") or time.time())

                # Check if matches any stale pattern
                for pattern in patterns:
                    if pattern in cmdline and age > max_age_seconds:
                        found.append({
                            "pid": info["pid"],
                            "cmdline": cmdline[:100],
                            "age_hours": round(age / 3600, 1),
                        })
                        try:
                            proc.terminate()
                            killed.append(info["pid"])
                            logger.info(
                                "maintenance_killed_stale",
                                pid=info["pid"],
                                cmdline=cmdline[:80],
                                age_hours=round(age / 3600, 1),
                            )
                        except (psutil.AccessDenied, psutil.NoSuchProcess):
                            pass
                        break

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return {
            "found_stale": len(found),
            "killed": len(killed),
            "killed_pids": killed,
            "details": found,
        }

    async def clean_cache_and_temp(self) -> dict[str, Any]:
        """
        Clean __pycache__, .pytest_cache, temp files.
        Only within agent home directory.
        """
        cleaned_dirs = 0
        cleaned_bytes = 0

        # __pycache__ directories
        for pycache in self._home.rglob("__pycache__"):
            if pycache.is_dir():
                size = sum(f.stat().st_size for f in pycache.rglob("*") if f.is_file())
                for f in pycache.rglob("*"):
                    if f.is_file():
                        f.unlink()
                try:
                    pycache.rmdir()
                except OSError:
                    pass
                cleaned_dirs += 1
                cleaned_bytes += size

        # .pytest_cache
        pytest_cache = self._home / ".pytest_cache"
        if pytest_cache.exists():
            size = sum(f.stat().st_size for f in pytest_cache.rglob("*") if f.is_file())
            for f in pytest_cache.rglob("*"):
                if f.is_file():
                    f.unlink()
            cleaned_bytes += size
            cleaned_dirs += 1

        # .mypy_cache
        mypy_cache = self._home / ".mypy_cache"
        if mypy_cache.exists() and mypy_cache.is_dir():
            size = sum(f.stat().st_size for f in mypy_cache.rglob("*") if f.is_file())
            for f in mypy_cache.rglob("*"):
                if f.is_file():
                    f.unlink()
            cleaned_bytes += size
            cleaned_dirs += 1

        logger.info(
            "maintenance_cache_cleaned",
            dirs=cleaned_dirs,
            bytes_freed=cleaned_bytes,
        )

        return {
            "cleaned_dirs": cleaned_dirs,
            "bytes_freed": cleaned_bytes,
            "mb_freed": round(cleaned_bytes / (1024 * 1024), 2),
        }

    async def check_disk_health(self) -> dict[str, Any]:
        """Check disk usage and warn if getting full."""
        disk = psutil.disk_usage("/")
        home_size = sum(
            f.stat().st_size
            for f in self._home.rglob("*")
            if f.is_file()
        )

        result = {
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "disk_used_gb": round(disk.used / (1024**3), 1),
            "disk_free_gb": round(disk.free / (1024**3), 1),
            "disk_percent": disk.percent,
            "agent_home_mb": round(home_size / (1024**2), 1),
            "warning": None,
        }

        if disk.percent > 90:
            result["warning"] = "CRITICAL: Disk usage over 90%!"
        elif disk.percent > 80:
            result["warning"] = "WARNING: Disk usage over 80%"

        return result

    async def check_memory_health(self) -> dict[str, Any]:
        """Check RAM and swap usage."""
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        result = {
            "ram_total_mb": round(mem.total / (1024**2)),
            "ram_used_mb": round(mem.used / (1024**2)),
            "ram_available_mb": round(mem.available / (1024**2)),
            "ram_percent": mem.percent,
            "swap_total_mb": round(swap.total / (1024**2)),
            "swap_used_mb": round(swap.used / (1024**2)),
            "swap_percent": swap.percent,
            "warning": None,
        }

        if mem.percent > 90:
            result["warning"] = "CRITICAL: RAM usage over 90%!"
        elif swap.used > 0:
            result["warning"] = f"Swap in use: {result['swap_used_mb']}MB — server under memory pressure"

        return result

    async def check_own_service(self) -> dict[str, Any]:
        """Check if agent service is running properly."""
        # Find our own process
        current = psutil.Process()
        agent_info = {
            "pid": current.pid,
            "memory_mb": round(current.memory_info().rss / (1024**2), 1),
            "cpu_percent": current.cpu_percent(),
            "threads": current.num_threads(),
            "uptime_hours": round((time.time() - current.create_time()) / 3600, 1),
            "status": current.status(),
        }

        # Check for zombie children
        children = current.children(recursive=True)
        zombies = [c for c in children if c.status() == "zombie"]
        if zombies:
            agent_info["zombie_children"] = len(zombies)
            for z in zombies:
                try:
                    z.wait(timeout=1)
                except psutil.TimeoutExpired:
                    pass

        # Count total child processes
        agent_info["child_processes"] = len(children)

        return agent_info

    async def check_network(self) -> dict[str, Any]:
        """Basic network connectivity check."""
        import subprocess

        checks = {}

        # DNS resolution
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "3", "8.8.8.8"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            checks["internet"] = result.returncode == 0
        except Exception:
            checks["internet"] = False

        # GitHub API
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5",
                 "https://api.github.com"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            checks["github_api"] = result.stdout.strip() == "200"
        except Exception:
            checks["github_api"] = False

        # Telegram API
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5",
                 "https://api.telegram.org"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            checks["telegram_api"] = result.stdout.strip() in ("200", "404", "301")
        except Exception:
            checks["telegram_api"] = False

        checks["all_ok"] = all(checks.values())
        return checks

    async def run_full_maintenance(self) -> dict[str, Any]:
        """Run all maintenance tasks. Returns combined report."""
        stale = await self.find_and_kill_stale_processes()
        cache = await self.clean_cache_and_temp()
        disk = await self.check_disk_health()
        memory = await self.check_memory_health()
        service = await self.check_own_service()
        network = await self.check_network()

        warnings = []
        if disk.get("warning"):
            warnings.append(disk["warning"])
        if memory.get("warning"):
            warnings.append(memory["warning"])
        if stale["killed"] > 0:
            warnings.append(f"Killed {stale['killed']} stale processes")
        if not network.get("all_ok"):
            failed = [k for k, v in network.items() if not v and k != "all_ok"]
            warnings.append(f"Network issues: {', '.join(failed)}")

        report = {
            "timestamp": datetime.now(UTC).isoformat(),
            "stale_processes": stale,
            "cache_cleanup": cache,
            "disk": disk,
            "memory": memory,
            "service": service,
            "network": network,
            "warnings": warnings,
            "status": "clean" if not warnings else "needs_attention",
        }

        logger.info(
            "maintenance_full_report",
            stale_killed=stale["killed"],
            cache_freed_mb=cache["mb_freed"],
            disk_percent=disk["disk_percent"],
            ram_percent=memory["ram_percent"],
            warnings=len(warnings),
        )

        return report
