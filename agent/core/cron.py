"""
Agent Life Space — Cron / Initiative System

John sa sám prebudí a koná bez toho aby ho niekto oslovil.
Pravidelné úlohy bežia na pozadí.

Joby:
    - Ranný report (8:00) — zdravie, úlohy, plán dňa
    - Health check (každú hodinu) — CPU, RAM, alerty
    - Memory maintenance (každých 6h) — decay, cleanup
    - Task review (každých 4h) — čo je v rade, čo treba
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class AgentCron:
    """
    Periodic initiative system. John acts on his own.
    """

    def __init__(
        self,
        agent: Any,
        telegram_bot: Any = None,
        owner_chat_id: int = 0,
    ) -> None:
        self._agent = agent
        self._bot = telegram_bot
        self._owner_chat_id = owner_chat_id
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        self._tasks.append(asyncio.create_task(self._health_loop()))
        self._tasks.append(asyncio.create_task(self._memory_maintenance_loop()))
        self._tasks.append(asyncio.create_task(self._morning_report_loop()))
        self._tasks.append(asyncio.create_task(self._task_review_loop()))

        logger.info("cron_started", jobs=4)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("cron_stopped")

    # --- Health Check (every hour) ---

    async def _health_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(3600)  # 1 hour
                await self._do_health_check()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_health_error")

    async def _do_health_check(self) -> None:
        health = self._agent.watchdog.get_system_health()

        # Alert Daniel if something is wrong
        if health.alerts and self._bot and self._owner_chat_id:
            alert_text = (
                "⚠️ *John — Health Alert*\n\n"
                + "\n".join(health.alerts)
                + f"\n\nCPU: {health.cpu_percent:.0f}%, "
                f"RAM: {health.memory_percent:.0f}%"
            )
            await self._bot.send_message(self._owner_chat_id, alert_text)
            logger.info("cron_health_alert_sent", alerts=len(health.alerts))

        logger.info(
            "cron_health_check",
            cpu=health.cpu_percent,
            ram=health.memory_percent,
            alerts=len(health.alerts),
        )

    # --- Memory Maintenance (every 6 hours) ---

    async def _memory_maintenance_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(21600)  # 6 hours
                deleted = await self._agent.memory.apply_decay(decay_rate=0.005)
                stats = self._agent.memory.get_stats()
                logger.info(
                    "cron_memory_maintenance",
                    deleted=deleted,
                    total=stats["total_memories"],
                )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_memory_error")

    # --- Morning Report (daily at ~8:00 UTC) ---

    async def _morning_report_loop(self) -> None:
        while self._running:
            try:
                # Wait until next 8:00 UTC
                now = datetime.now(timezone.utc)
                next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
                if now.hour >= 8:
                    # Already past 8am, wait until tomorrow
                    next_8am = next_8am.replace(day=now.day + 1)
                wait_seconds = (next_8am - now).total_seconds()
                await asyncio.sleep(max(wait_seconds, 60))

                await self._do_morning_report()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_morning_error")

    async def _do_morning_report(self) -> None:
        """John sends Daniel a morning report — proactively."""
        if not self._bot or not self._owner_chat_id:
            return

        health = self._agent.watchdog.get_system_health()
        mem_stats = self._agent.memory.get_stats()
        task_stats = self._agent.tasks.get_stats()
        from agent.tasks.manager import TaskStatus
        queued = self._agent.tasks.get_tasks_by_status(TaskStatus.QUEUED)

        tasks_text = ""
        if queued:
            tasks_text = "\n*Úlohy v rade:*\n" + "\n".join(
                f"  • {t.name}" for t in queued[:5]
            )

        report = (
            f"☀️ *Dobré ráno, Daniel!*\n\n"
            f"*Môj stav:*\n"
            f"  CPU: {health.cpu_percent:.0f}%, RAM: {health.memory_percent:.0f}%\n"
            f"  Moduly: {'všetky OK' if not health.alerts else ', '.join(health.alerts)}\n"
            f"  Spomienky: {mem_stats['total_memories']}\n"
            f"  Úlohy: {task_stats['total_tasks']}"
            f"{tasks_text}\n\n"
            f"Čo robíme dnes?"
        )

        await self._bot.send_message(self._owner_chat_id, report)
        logger.info("cron_morning_report_sent")

    # --- Task Review (every 4 hours) ---

    async def _task_review_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(14400)  # 4 hours
                await self._do_task_review()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_task_review_error")

    async def _do_task_review(self) -> None:
        """Check if there are stale tasks."""
        from agent.tasks.manager import TaskStatus
        queued = self._agent.tasks.get_tasks_by_status(TaskStatus.QUEUED)
        running = self._agent.tasks.get_tasks_by_status(TaskStatus.RUNNING)

        if len(queued) > 10 and self._bot and self._owner_chat_id:
            await self._bot.send_message(
                self._owner_chat_id,
                f"📋 *John — Task Review*\n\n"
                f"Mám {len(queued)} úloh v rade a {len(running)} bežiacich.\n"
                f"Chceš niektoré prioretizovať alebo zrušiť?",
            )

        logger.info(
            "cron_task_review",
            queued=len(queued),
            running=len(running),
        )
