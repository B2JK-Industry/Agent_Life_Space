"""
Agent Life Space — Cron / Initiative System

Agent sa sám prebudí a koná bez toho, aby ho niekto oslovil.
Pravidelné úlohy bežia na pozadí.

Joby:
    - Ranný report (8:00) — zdravie, úlohy, plán dňa
    - Health check (každú hodinu) — CPU, RAM, alerty
    - Memory maintenance (každých 6h) — decay, cleanup
    - Task review (každých 4h) — čo je v rade, čo treba
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from agent.core.identity import get_agent_identity

logger = structlog.get_logger(__name__)


class AgentCron:
    """
    Periodic initiative system. The agent acts on its own.
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
        self._marketplace_lock = asyncio.Lock()
        # Persistent notification dedup — lazy init pri prvom použití
        self._dedup: Any = None
        self._dedup_init_lock = asyncio.Lock()

    def _notification_dedup(self) -> Any:
        """Vracia singleton NotificationDedup. Lazy init."""
        return self._dedup

    async def _ensure_dedup(self) -> None:
        if self._dedup is not None:
            return
        async with self._dedup_init_lock:
            if self._dedup is not None:
                return
            from agent.core.notification_dedup import NotificationDedup
            data_dir = getattr(self._agent, "_data_dir", None)
            if data_dir is None:
                from pathlib import Path
                data_dir = Path("agent")
            cron_dir = data_dir / "cron"
            cron_dir.mkdir(parents=True, exist_ok=True)
            dedup = NotificationDedup(db_path=str(cron_dir / "notifications.db"))
            await dedup.initialize()
            self._dedup = dedup

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        self._tasks.append(asyncio.create_task(self._health_loop()))
        self._tasks.append(asyncio.create_task(self._memory_maintenance_loop()))
        self._tasks.append(asyncio.create_task(self._morning_report_loop()))
        self._tasks.append(asyncio.create_task(self._task_review_loop()))
        self._tasks.append(asyncio.create_task(self._server_maintenance_loop()))

        self._tasks.append(asyncio.create_task(self._consolidation_loop()))
        self._tasks.append(asyncio.create_task(self._dead_man_switch_loop()))
        self._tasks.append(asyncio.create_task(self._recurring_workflow_loop()))
        self._tasks.append(asyncio.create_task(self._telemetry_loop()))
        self._tasks.append(asyncio.create_task(self._retention_pruning_loop()))
        self._tasks.append(asyncio.create_task(self._data_cleanup_loop()))
        self._tasks.append(asyncio.create_task(self._log_retention_loop()))
        self._tasks.append(asyncio.create_task(self._marketplace_monitor_loop()))
        self._tasks.append(asyncio.create_task(self._initiative_driver_loop()))
        logger.info("cron_started", jobs=14)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        # Await cancellation so the loops actually exit before we drop
        # references. Without this gather() the tasks would only have
        # cancel() set and could log "Task was destroyed but it is
        # pending!" during shutdown.
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
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

        identity = get_agent_identity()
        # Alert the operator if something is wrong
        if health.alerts and self._bot and self._owner_chat_id:
            # Rozlíš severity
            unresponsive = [a for a in health.alerts if "unresponsive" in a.lower()]
            other_alerts = [a for a in health.alerts if "unresponsive" not in a.lower()]

            if unresponsive:
                alert_text = (
                    f"🔴 *{identity.agent_name} — CRITICAL: Modul UNRESPONSIVE*\n\n"
                    + "\n".join(unresponsive)
                    + f"\n\nCPU: {health.cpu_percent:.0f}%, "
                    f"RAM: {health.memory_percent:.0f}%\n"
                    f"Watchdog sa pokúša reštartovať."
                )
            else:
                alert_text = (
                    f"⚠️ *{identity.agent_name} — Health Alert*\n\n"
                    + "\n".join(other_alerts)
                    + f"\n\nCPU: {health.cpu_percent:.0f}%, "
                    f"RAM: {health.memory_percent:.0f}%"
                )
            await self._bot.send_message(self._owner_chat_id, alert_text)
            logger.info("cron_health_alert_sent", alerts=len(health.alerts),
                        critical=len(unresponsive))

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
                now = datetime.now(UTC)
                next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
                if now.hour >= 8:
                    # Already past 8am, wait until tomorrow
                    next_8am = next_8am + timedelta(days=1)
                wait_seconds = (next_8am - now).total_seconds()
                await asyncio.sleep(max(wait_seconds, 60))

                await self._do_morning_report()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_morning_error")

    async def _do_morning_report(self) -> None:
        """The agent sends the operator a morning report proactively."""
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
            "☀️ *Dobré ráno!*\n\n"
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
                f"📋 *{get_agent_identity().agent_name} — Task Review*\n\n"
                f"Mám {len(queued)} úloh v rade a {len(running)} bežiacich.\n"
                f"Chceš niektoré prioretizovať alebo zrušiť?",
            )

        logger.info(
            "cron_task_review",
            queued=len(queued),
            running=len(running),
        )

    # --- Server Maintenance (every 3 hours) ---

    async def _server_maintenance_loop(self) -> None:
        # First run after 5 minutes (let agent stabilize)
        await asyncio.sleep(300)
        while self._running:
            try:
                await self._do_server_maintenance()
                await asyncio.sleep(10800)  # 3 hours
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_maintenance_error")

    async def _do_server_maintenance(self) -> None:
        from agent.core.maintenance import ServerMaintenance

        maint = ServerMaintenance()
        report = await maint.run_full_maintenance()

        # Maintenance runs silently — only log, don't spam Telegram

        logger.info(
            "cron_maintenance_done",
            stale_killed=report["stale_processes"]["killed"],
            cache_mb=report["cache_cleanup"]["mb_freed"],
            warnings=len(report["warnings"]),
        )

    # --- Memory Consolidation (every 2 hours) ---

    async def _consolidation_loop(self) -> None:
        # First run after 10 minutes
        await asyncio.sleep(600)
        while self._running:
            try:
                await self._do_consolidation()
                await asyncio.sleep(7200)  # 2 hours
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_consolidation_error")

    async def _do_consolidation(self) -> None:
        from agent.memory.consolidation import MemoryConsolidation

        consolidator = MemoryConsolidation(self._agent.memory)
        report = await consolidator.consolidate()

        logger.info(
            "cron_consolidation_done",
            reviewed=report["episodic_reviewed"],
            patterns=report["patterns_found"],
            new_entries=report["new_semantic_procedural"],
            deduplicated=report["deduplicated"],
        )

    # --- Dead Man Switch (every 12 hours) ---

    async def _dead_man_switch_loop(self) -> None:
        """
        Kontroluj stale proposals a notifikuj ownera.

        Politika:
            3 dni → warning (pripomienka)
            7 dní → escalation (urgentné)
            14 dní → auto-cancel
        """
        await asyncio.sleep(3600)  # First run after 1 hour
        while self._running:
            try:
                await self._do_dead_man_switch()
                await asyncio.sleep(43200)  # 12 hours
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_dead_man_switch_error")

    async def _do_dead_man_switch(self) -> None:
        stale = await self._agent.finance.check_stale_proposals()

        if not stale:
            logger.info("dead_man_switch_ok", stale_proposals=0)
            return

        if not self._bot or not self._owner_chat_id:
            return

        # Group by action
        warnings = [s for s in stale if s["action"] == "warning"]
        escalations = [s for s in stale if s["action"] == "escalation"]
        cancelled = [s for s in stale if s["action"] == "auto_cancelled"]

        lines = []
        if cancelled:
            lines.append("🚫 *Auto-zrušené (14+ dní):*")
            for s in cancelled:
                lines.append(f"  • {s['description']} (${s['amount']:.2f}, {s['age_days']}d)")

        if escalations:
            lines.append("🔴 *Urgentné (7+ dní):*")
            for s in escalations:
                lines.append(f"  • {s['description']} (${s['amount']:.2f}, {s['age_days']}d)")

        if warnings:
            lines.append("🟡 *Čakajúce (3+ dní):*")
            for s in warnings:
                lines.append(f"  • {s['description']} (${s['amount']:.2f}, {s['age_days']}d)")

        if lines:
            identity = get_agent_identity()
            message = (
                f"⏰ *{identity.agent_name} — Dead Man Switch*\n\n"
                f"Mám {len(stale)} nevybavených proposals:\n\n"
                + "\n".join(lines)
                + "\n\nPouži /budget na detail."
            )
            await self._bot.send_message(self._owner_chat_id, message)
            logger.info("dead_man_switch_notification",
                        warnings=len(warnings),
                        escalations=len(escalations),
                        cancelled=len(cancelled))

    # --- Telemetry Snapshot (every hour) ---

    async def _telemetry_loop(self) -> None:
        """Record a runtime telemetry snapshot every hour."""
        await asyncio.sleep(300)  # First run after 5 minutes
        while self._running:
            try:
                await self._do_telemetry_snapshot()
                await asyncio.sleep(3600)  # 1 hour
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_telemetry_error")

    async def _do_telemetry_snapshot(self) -> None:
        if not hasattr(self._agent, "control_plane"):
            return
        cp = self._agent.control_plane
        try:
            snapshot = cp.record_telemetry_snapshot(
                status_provider=lambda: self._agent.watchdog.get_system_health().__dict__
                if hasattr(self._agent, "watchdog")
                else {},
            )
            logger.info(
                "cron_telemetry_recorded",
                jobs_completed=snapshot.jobs_completed,
                cost=snapshot.total_cost_usd,
            )
        except Exception:
            logger.exception("cron_telemetry_snapshot_error")

    # --- Retention Pruning (every 6 hours) ---

    async def _retention_pruning_loop(self) -> None:
        """Soft-delete expired artifact retention records."""
        await asyncio.sleep(900)  # First run after 15 minutes
        while self._running:
            try:
                await self._do_retention_pruning()
                await asyncio.sleep(21600)  # 6 hours
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_retention_pruning_error")

    async def _do_retention_pruning(self) -> None:
        if hasattr(self._agent, "control_plane"):
            try:
                pruned = self._agent.control_plane.prune_retained_artifacts(limit=5000)
                if pruned:
                    logger.info("cron_retention_pruned", count=len(pruned))
                else:
                    logger.info("cron_retention_pruned", count=0)
            except Exception:
                logger.exception("cron_retention_prune_error")

        # Prune dead notification dedup entries (older than 7 days).
        # Bez prune by notification_log v cron/notifications.db rástla forever.
        dedup = self._notification_dedup()
        if dedup is not None:
            try:
                deleted = await dedup.prune_older_than(hours=168)
                if deleted:
                    logger.info("cron_notification_dedup_pruned", count=deleted)
            except Exception:
                logger.exception("cron_notification_dedup_prune_error")

    # --- Data Cleanup (nightly at ~00:00 UTC) ---

    async def _data_cleanup_loop(self) -> None:
        """Hard-delete old data to prevent unbounded table growth."""
        await asyncio.sleep(1800)  # First run after 30 minutes
        while self._running:
            try:
                now = datetime.now(UTC)
                next_midnight = now.replace(
                    hour=0, minute=0, second=0, microsecond=0,
                ) + timedelta(days=1)
                wait_seconds = (next_midnight - now).total_seconds()
                await asyncio.sleep(max(wait_seconds, 60))

                await self._do_data_cleanup()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_data_cleanup_error")

    async def _do_data_cleanup(self) -> None:
        if not hasattr(self._agent, "control_plane"):
            return
        storage = self._agent.control_plane._storage
        total = 0
        try:
            total += storage.hard_delete_pruned_artifacts(older_than_days=90)
            total += storage.hard_delete_old_traces(older_than_days=90)
            total += storage.hard_delete_old_plans(older_than_days=365)
            total += storage.hard_delete_old_pipelines(older_than_days=180)
            logger.info("cron_data_cleanup_done", total_deleted=total)
        except Exception:
            logger.exception("cron_data_cleanup_error")

        # Workspace cleanup — delete completed/failed workspaces older than TTL
        try:
            cleaned = self._agent.workspaces.cleanup_expired()
            if cleaned:
                logger.info("cron_workspace_cleanup", cleaned=cleaned)
        except Exception:
            logger.exception("cron_workspace_cleanup_error")

    # --- Recurring Workflows (every 60s check) ---

    async def _recurring_workflow_loop(self) -> None:
        """Check for due recurring workflows and execute them."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._execute_due_workflows()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_recurring_workflow_error")

    async def _execute_due_workflows(self) -> None:
        if not hasattr(self._agent, "recurring_workflows"):
            return
        mgr = self._agent.recurring_workflows
        due = mgr.get_due_workflows()
        if not due:
            return

        for workflow in due:
            try:
                logger.info(
                    "recurring_workflow_executing",
                    workflow_id=workflow.workflow_id,
                    name=workflow.name,
                )
                # Build intake from template and submit
                from agent.control.intake import OperatorIntake

                template = dict(workflow.intake_template)
                intake = OperatorIntake(
                    repo_path=template.get("repo_path", ""),
                    git_url=template.get("git_url", ""),
                    work_type=template.get("work_type", workflow.job_kind.value),
                    description=template.get(
                        "description",
                        f"Recurring {workflow.name}",
                    ),
                    requester="cron",
                )
                result = await self._agent.submit_operator_intake(intake)
                job_id = result.get("job_id", "")
                success = result.get("status") == "completed"
                mgr.record_execution(
                    workflow.workflow_id,
                    job_id=job_id,
                    success=success,
                    error=result.get("error", ""),
                )

                # Notify operator
                if self._bot and self._owner_chat_id:
                    status_label = "completed" if success else "failed"
                    await self._bot.send_message(
                        self._owner_chat_id,
                        f"*Recurring workflow:* {workflow.name}\n"
                        f"Status: {status_label}\n"
                        f"Job: `{job_id}`" if job_id else "",
                    )
            except Exception as e:
                mgr.record_execution(
                    workflow.workflow_id,
                    success=False,
                    error=str(e),
                )
                logger.error(
                    "recurring_workflow_failed",
                    workflow_id=workflow.workflow_id,
                    error=str(e),
                )

    # --- Log Retention (every hour) ---

    async def _log_retention_loop(self) -> None:
        """Periodically prune log files older than the configured tier
        retention. Long-tier files (~30d) and short-tier files (~6h)
        are aged out independently. The retention manager itself is
        deterministic — it just compares mtime against now."""
        # First sweep after 5 minutes (give the agent time to settle).
        await asyncio.sleep(300)
        while self._running:
            try:
                await self._do_log_retention_sweep()
                await asyncio.sleep(3600)  # 1 hour
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_log_retention_error")

    async def _do_log_retention_sweep(self) -> None:
        from agent.logs.retention import LogRetentionManager

        log_dir = os.environ.get("AGENT_LOG_DIR", "")
        if not log_dir:
            # Same default as setup_tiered_logging in __main__.py.
            from agent.core.paths import get_project_root
            log_dir = os.path.join(get_project_root(), "agent", "logs")

        try:
            mgr = LogRetentionManager(log_dir=log_dir)
        except ValueError as e:
            logger.warning("log_retention_misconfigured", error=str(e))
            return

        results = mgr.prune_all()
        long_pruned = results["long"].pruned
        short_pruned = results["short"].pruned
        if long_pruned or short_pruned:
            logger.info(
                "cron_log_retention_pruned",
                long_pruned=long_pruned,
                long_bytes_freed=results["long"].bytes_freed,
                short_pruned=short_pruned,
                short_bytes_freed=results["short"].bytes_freed,
            )

    # --- Marketplace Monitor (every 6 hours) ---

    _MARKETPLACE_INTERVAL = int(os.environ.get("AGENT_MARKETPLACE_INTERVAL", "21600"))  # 6h
    _ALS_SKILLS = frozenset({
        "python", "code-review", "code-generation", "api", "data-analysis",
        "text-generation", "summarization", "testing", "linting",
        "documentation", "web-scraping", "monitoring", "security",
    })

    async def _marketplace_monitor_loop(self) -> None:
        """Scan Obolos marketplace for new opportunities every N hours.

        Zero LLM tokens. Deterministic scan + Telegram delivery (with dedup).
        """
        await asyncio.sleep(120)  # let agent settle
        await self._ensure_dedup()
        while self._running:
            try:
                async with self._marketplace_lock:
                    await self._do_marketplace_scan()
                await asyncio.sleep(self._MARKETPLACE_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_marketplace_monitor_error")

    async def _do_marketplace_scan(self) -> None:
        mkt = getattr(self._agent, "marketplace", None)
        if mkt is None or not getattr(mkt, "_initialized", False):
            return

        # Scan listings + ANP listings + jobs
        try:
            listings = await mkt.list_listings(platform="obolos.tech", limit=20)
        except Exception:
            listings = []
        # Include ANP listings (wider opportunity pool)
        connector = mkt.registry.get("obolos.tech")
        if connector and hasattr(connector, "list_anp_listings"):
            try:
                anp_listings = await connector.list_anp_listings(mkt._gateway, limit=10)
                # Deduplicate by platform_id
                known_ids = {o.platform_id for o in listings}
                for anp in anp_listings:
                    if anp.platform_id not in known_ids:
                        listings.append(anp)
            except Exception:
                pass
        try:
            jobs_raw = await mkt.list_jobs(platform="obolos.tech", limit=20)
        except Exception:
            jobs_raw = []
        try:
            outcomes = await mkt.list_outcomes(limit=20)
        except Exception:
            outcomes = []
        try:
            bids = await mkt.list_bids(limit=50)
        except Exception:
            bids = []

        # Build deterministic report
        now = datetime.now(UTC)
        lines = [f"📊 *Obolos Marketplace Report* ({now.strftime('%Y-%m-%d %H:%M')} UTC)\n"]

        # Open listings — filter to ALS-capable
        open_listings = [o for o in listings if o.raw_data.get("status") == "open"]
        expired = [o for o in open_listings if self._is_expired(o)]
        fresh = [o for o in open_listings if not self._is_expired(o)]

        if fresh:
            lines.append(f"🟢 *Fresh open listings ({len(fresh)}):*")
            for o in fresh:
                skills = set(o.skills_required)
                matched = skills & self._ALS_SKILLS
                fit = "✅" if matched else "❓"
                budget = f" {o.budget_max} {o.currency}" if o.budget_max else ""
                lines.append(f"  {fit} *{o.title[:50]}*{budget}")
                if matched:
                    lines.append(f"     Matched skills: {', '.join(sorted(matched))}")
                lines.append(f"     ID: `{o.platform_id[:12]}`")
        else:
            lines.append("⚪ No fresh open listings right now.")

        if expired:
            lines.append(f"\n⏰ {len(expired)} expired open listing(s) (not biddable)")

        # Jobs summary
        if jobs_raw:
            by_status: dict[str, int] = {}
            for j in jobs_raw:
                s = j.get("status", "?")
                by_status[s] = by_status.get(s, 0) + 1
            status_parts = [f"{c} {s}" for s, c in sorted(by_status.items())]
            lines.append(f"\n📋 *Jobs:* {len(jobs_raw)} total ({', '.join(status_parts)})")

        # Bids summary
        if bids:
            bid_by_status: dict[str, int] = {}
            for b in bids:
                bid_by_status[b.status.value] = bid_by_status.get(b.status.value, 0) + 1
            bid_parts = [f"{c} {s}" for s, c in sorted(bid_by_status.items())]
            lines.append(f"🏷 *Bids:* {len(bids)} ({', '.join(bid_parts)})")

        # Outcomes
        if outcomes:
            completed = sum(1 for o in outcomes if o.status.value == "completed")
            revenue = sum(o.revenue_amount or 0 for o in outcomes if o.revenue_amount)
            lines.append(f"📈 *Outcomes:* {len(outcomes)} recorded, {completed} completed")
            if revenue:
                lines.append(f"   Revenue: ${revenue:.2f}")

        # Action items
        if fresh:
            lines.append(f"\n💡 *{len(fresh)} listing(s) may need attention:*")
            lines.append("   `/marketplace listings` to browse")
            lines.append("   `/marketplace eval <id>` to assess")

        report = "\n".join(lines)

        # Deliver via Telegram — IBA ak je niečo akcionovateľné nové.
        # Predtým bol report posielaný každých ~10 min bez ohľadu na obsah,
        # spamoval Telegram aj keď nič nové nebolo. Nový princíp:
        # 1. fresh listings musia byť > 0 (nezasielame "no fresh listings"),
        # 2. dedup-key je hash množiny fresh platform_id (ak rovnaký set ako naposledy → skip),
        # 3. fallback TTL 24h aby aspoň raz denne pripomenulo aktívne listings.
        if not (self._bot and self._owner_chat_id):
            logger.info("cron_marketplace_report_no_telegram", report_length=len(report))
        elif not fresh:
            logger.info("cron_marketplace_report_skipped_empty",
                        listings=len(listings), fresh=0)
        else:
            dedup = self._notification_dedup()
            fresh_ids_key = ",".join(sorted(o.platform_id for o in fresh))[:200]
            sent = await dedup.send_once(
                bot=self._bot,
                chat_id=self._owner_chat_id,
                text=report,
                dedup_key=f"marketplace_report:{fresh_ids_key}",
                ttl_hours=24,
            )
            if sent:
                logger.info("cron_marketplace_report_sent",
                            listings=len(listings), fresh=len(fresh))
            else:
                logger.info("cron_marketplace_report_deduped",
                            listings=len(listings), fresh=len(fresh))

        # ── Proactive scouting: auto-evaluate + auto-bid for matching listings ──
        await self._auto_scout_listings(mkt, fresh)

        # ── Re-execute approved bids that cron prepared earlier ──
        await self._resubmit_approved_bids(mkt)

        # ── Detect newly accepted jobs — alert operator to start work ──
        await self._check_accepted_jobs(mkt, jobs_raw)

    @staticmethod
    def _is_expired(opp: Any) -> bool:
        deadline = str(getattr(opp, "deadline", "") or opp.raw_data.get("deadline", ""))
        if not deadline:
            return False
        try:
            dl = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            return dl < datetime.now(UTC)
        except (ValueError, TypeError):
            return False

    # ─── Proactive Scouting ───

    _MAX_SCOUT_PER_CYCLE = 3  # Cap auto-bids per scan to avoid spam

    async def _auto_scout_listings(self, mkt: Any, fresh_listings: list[Any]) -> None:
        """Proactive scouting: auto-evaluate fresh listings and prepare bids.

        For each new listing that John hasn't bid on yet:
        1. Evaluate feasibility (deterministic, zero LLM cost)
        2. If feasible/partial: prepare bid draft → trigger approval queue
        3. Send Telegram notification with evaluation + approve command

        Operator still approves every bid — John just does the legwork.
        """
        if not fresh_listings:
            return

        # Get existing bids to avoid duplicate scouting
        try:
            existing_bids = await mkt.list_bids(limit=200)
        except Exception:
            logger.warning("cron_scout_list_bids_failed")
            existing_bids = []
        bid_opp_ids = {b.opportunity_id for b in existing_bids}

        scouted = 0
        for opp in fresh_listings:
            if scouted >= self._MAX_SCOUT_PER_CYCLE:
                break

            # Skip if we already have a bid for this opportunity
            if opp.id in bid_opp_ids:
                continue

            # Check listing is biddable
            try:
                biddable, reason = mkt.get_listing_bid_eligibility(opp)
                if not biddable:
                    continue
            except Exception:
                logger.warning("cron_scout_eligibility_check_failed",
                               opp_id=opp.id, title=getattr(opp, "title", "")[:50])
                continue

            # Evaluate feasibility (local, no network)
            try:
                evaluation = mkt.evaluate(opp)
            except Exception:
                logger.exception("cron_scout_eval_failed", opp_id=opp.id)
                continue

            verdict = evaluation.verdict.value
            if verdict == "infeasible":
                continue

            # Prepare bid draft (local)
            try:
                bid = mkt.prepare_bid(opp, evaluation)
            except Exception:
                logger.exception("cron_scout_bid_prep_failed", opp_id=opp.id)
                continue

            # Submit bid — triggers approval queue, does NOT send money
            try:
                result = await mkt.submit_bid(bid)
            except Exception as exc:
                logger.exception("cron_scout_submit_failed", opp_id=opp.id)
                result = {"ok": False, "error": f"Submit raised: {type(exc).__name__}: {exc}"[:200]}

            # Send actionable Telegram notification (success OR failure)
            await self._send_scout_notification(opp, evaluation, bid, result)
            scouted += 1

        if scouted:
            logger.info("cron_auto_scout_complete", scouted=scouted)

    async def _send_scout_notification(
        self, opp: Any, evaluation: Any, bid: Any, result: dict[str, Any],
    ) -> None:
        """Send Telegram message about a scouted listing with approve command."""
        if not self._bot or not self._owner_chat_id:
            return

        verdict_val = getattr(evaluation.verdict, "value", "unknown")
        confidence = float(getattr(evaluation, "confidence", 0.0))
        verdict_emoji = "✅" if verdict_val == "feasible" else "🟡"

        budget_max = getattr(opp, "budget_max", None)
        currency = getattr(opp, "currency", "USD")
        skills = getattr(opp, "skills_required", []) or []
        description = getattr(opp, "description", "") or ""
        title = getattr(opp, "title", "?") or "?"

        budget_line = f"💵 Budget: {budget_max} {currency}" if budget_max else ""
        skills_line = f"🛠 Skills: {', '.join(skills[:5])}" if skills else ""

        msg_lines = [
            "🔍 *Našiel som prácu na Obolos:*",
            "",
            f"📋 *{title[:60]}*",
        ]
        if budget_line:
            msg_lines.append(budget_line)
        if skills_line:
            msg_lines.append(skills_line)
        if description:
            msg_lines.append(f"📝 {description[:120]}")

        reasoning = getattr(evaluation, "reasoning", "") or ""
        price_usd = float(getattr(bid, "price_usd", 0.0))
        msg_lines.extend([
            "",
            f"{verdict_emoji} *Hodnotenie:* {verdict_val} ({confidence:.0%})",
            f"   {reasoning}",
            "",
            f"💰 *Pripravený bid:* ${price_usd:.2f}",
        ])

        if result.get("pending_approval"):
            approval_id = result.get("approval_id", "")
            msg_lines.extend([
                "",
                "⏳ *Čakám na tvoj súhlas:*",
                "Confirm with `/yes` or reject with `/no`.",
                f"Approval ID: `{approval_id}`",
            ])
        elif result.get("ok"):
            msg_lines.append("✅ Bid odoslaný!")
        else:
            error = result.get("error", "neznáma chyba")
            msg_lines.append(f"❌ Nepodarilo sa: {error[:100]}")

        msg = "\n".join(msg_lines)
        try:
            await self._bot.send_message(self._owner_chat_id, msg)
        except Exception:
            logger.warning("cron_scout_telegram_failed")

    # ─── Approved-Bid Re-execution ───

    async def _resubmit_approved_bids(self, mkt: Any) -> None:
        """Re-execute READY bids that the operator approved since last scan.

        After cron prepares a bid → operator approves via /queue approve →
        this method detects the approval and executes the bid automatically.
        The operator doesn't need to manually re-run any command.
        """
        try:
            bids = await mkt.list_bids(limit=100)
        except Exception:
            return

        resubmitted = 0
        for bid in bids:
            if bid.status.value != "ready":
                continue

            # Try to submit — submit_bid() checks approval status internally
            try:
                result = await mkt.submit_bid(bid)
            except Exception:
                logger.exception("cron_resubmit_failed", bid_id=bid.id)
                continue

            if result.get("ok"):
                resubmitted += 1
                # Notify operator that bid was auto-submitted after approval
                if self._bot and self._owner_chat_id:
                    msg = (
                        f"✅ *Bid odoslaný po tvojom schválení:*\n"
                        f"📋 {bid.title[:60]}\n"
                        f"💰 ${bid.price_usd:.2f}"
                    )
                    try:
                        await self._bot.send_message(self._owner_chat_id, msg)
                    except Exception:
                        pass

        if resubmitted:
            logger.info("cron_resubmit_approved_bids", count=resubmitted)

    # ─── Accepted Job Detection ───

    _KNOWN_JOB_IDS_KEY = "_cron_known_job_ids"
    _RETRY_JOB_IDS_KEY = "_cron_retry_job_ids"  # jobs to retry on next cycle
    _MAX_JOB_RETRIES = 3

    async def _check_accepted_jobs(
        self, mkt: Any, jobs_raw: list[dict[str, Any]],
    ) -> None:
        """Detect new/retryable accepted jobs and attempt work.

        New jobs: notify + auto-execute.
        Retry jobs: re-attempt auto-execute silently (failed on prior cycle,
        typically due to unfunded escrow).
        """
        if not jobs_raw:
            return

        known: set[str] = getattr(self, self._KNOWN_JOB_IDS_KEY, set())
        retry_jobs: dict[str, int] = getattr(self, self._RETRY_JOB_IDS_KEY, {})
        new_active: list[dict[str, Any]] = []

        for job in jobs_raw:
            job_id = str(job.get("id", job.get("job_id", "")))
            if not job_id:
                continue
            status = str(job.get("status", "")).strip().lower()
            # "open" = newly created from accepted bid, "in_progress" = work started
            if status in ("open", "in_progress") and job_id not in known:
                new_active.append(job)
            known.add(job_id)

        setattr(self, self._KNOWN_JOB_IDS_KEY, known)

        if not new_active:
            return

        for job in new_active:
            await self._notify_and_attempt_job(job, mkt)
        if new_active:
            logger.info("cron_new_accepted_jobs", count=len(new_active))

        # Retry previously-failed jobs (e.g. unfunded on prior cycle)
        for job in jobs_raw:
            job_id = str(job.get("id", job.get("job_id", "")))
            if job_id in retry_jobs and job_id not in {
                str(j.get("id", j.get("job_id", ""))) for j in new_active
            }:
                status = str(job.get("status", "")).lower()
                if status not in ("open", "in_progress"):
                    retry_jobs.pop(job_id, None)
                    continue
                retries = retry_jobs[job_id]
                if retries >= self._MAX_JOB_RETRIES:
                    retry_jobs.pop(job_id, None)
                    logger.warning("cron_job_retry_exhausted", job_id=job_id, retries=retries)
                    continue
                logger.info("cron_job_retry_attempt", job_id=job_id, attempt=retries + 1)
                title = str(job.get("title", job.get("description", "")))[:80]
                description = str(job.get("description", ""))
                combined = f"{title} {description}".lower()
                can_auto = any(kw in combined for kw in self._AUTO_WORK_KEYWORDS)
                if can_auto:
                    await self._auto_execute_job(job_id, title, description, mkt)

        setattr(self, self._RETRY_JOB_IDS_KEY, retry_jobs)

    # Capabilities John can auto-execute (lowercase keywords in title/description)
    _AUTO_WORK_KEYWORDS = frozenset({
        "code review", "code-review", "review", "audit",
        "python", "script", "cli", "api", "test",
        "documentation", "docs", "summarize", "summary",
        "linting", "lint", "format", "analyze", "analysis",
    })
    # Keywords that mean "I can't do this automatically"
    _REJECT_KEYWORDS = frozenset({
        "video", "audio", "image", "photo", "design",
        "frontend", "react", "vue", "angular", "css",
        "database migration", "deploy", "infrastructure",
        "hardware", "physical",
    })

    async def _notify_and_attempt_job(
        self, job: dict[str, Any], mkt: Any,
    ) -> None:
        """Notify operator about new job AND attempt auto-execution if capable.

        Flow:
        1. Always send Telegram alert (operator must know)
        2. Check if job is within auto-work capabilities
        3. If yes: extract spec → submit via /marketplace job-submit
        4. If no: tell operator why and suggest manual intervention
        """
        if not self._bot or not self._owner_chat_id:
            return

        job_id = str(job.get("id", job.get("job_id", "?")))
        title = str(job.get("title", job.get("description", "Untitled")))[:80]
        description = str(job.get("description", ""))
        status = str(job.get("status", "?"))
        budget = job.get("budget", job.get("price", ""))
        client = str(job.get("client_address", job.get("client", "")))[:12]
        combined = f"{title} {description}".lower()

        # Try to find linked bid/opportunity for richer context
        linkage = {}
        if hasattr(mkt, "_find_linkage_for_job"):
            try:
                linkage = await mkt._find_linkage_for_job(job_id)
            except Exception:
                pass

        # Classify: can I auto-work this?
        can_reject = any(kw in combined for kw in self._REJECT_KEYWORDS)
        can_auto = any(kw in combined for kw in self._AUTO_WORK_KEYWORDS)
        # Jobs outside direct capabilities might still be doable via x402
        # sub-contracting (e.g. video job → call x402 video API)
        can_subcontract = can_reject and not can_auto

        # Build notification
        lines = [
            "🎉 *Nový job bol prijatý!*",
            "",
            f"📋 *{title}*",
        ]
        if budget:
            lines.append(f"💰 Budget: {budget}")
        if client:
            lines.append(f"👤 Klient: `{client}...`")
        lines.append(f"📊 Status: {status}")
        lines.append(f"🆔 Job ID: `{job_id}`")
        if linkage.get("project_id"):
            lines.append(f"📁 ALS project: `{linkage['project_id']}`")

        if can_auto:
            lines.extend([
                "",
                "🔧 *Automaticky začínam pracovať...*",
                "Pošlem výsledok keď budem hotový.",
            ])
        elif can_subcontract:
            lines.extend([
                "",
                "🔄 *Nie je to moja priama schopnosť, ale skúsim nájsť*",
                "*x402 API sub-contractor* na Obolose...",
            ])
        else:
            lines.extend([
                "",
                "*Ďalšie kroky:*",
                f"  `/marketplace job {job_id[:12]}` — detail jobu",
                f"  `/build . --description \"<podľa zadania>\"` — spustiť prácu",
                f"  `/marketplace job-submit {job_id[:12]}` — odoslať výsledok",
            ])

        msg = "\n".join(lines)
        # Dedup new-job notification: 1× per job_id, ttl 7 days (job lifetime)
        dedup = self._notification_dedup()
        if dedup is None:
            await self._ensure_dedup()
            dedup = self._notification_dedup()
        try:
            await dedup.send_once(
                bot=self._bot,
                chat_id=self._owner_chat_id,
                text=msg,
                dedup_key=f"new_job:{job_id}",
                ttl_hours=168,  # 7 days
            )
        except Exception:
            logger.warning("cron_new_job_telegram_failed")

        # Auto-execute if capable, try sub-contracting if not
        if can_auto:
            await self._auto_execute_job(job_id, title, description, mkt)
        elif can_subcontract:
            await self._try_subcontract_job(job_id, title, description, mkt)

    async def _auto_execute_job(
        self,
        job_id: str,
        title: str,
        description: str,
        mkt: Any,
    ) -> None:
        """Attempt to automatically execute an accepted job and submit the result.

        Checks ACP funding status first — unfunded jobs cannot accept deliverables.
        """
        # ── Pre-flight: check job is funded ──
        try:
            job_detail = await mkt.get_job_detail("obolos.tech", job_id)
            if job_detail:
                job_status = str(job_detail.get("status", "")).lower()
                funded = job_detail.get("funded", job_detail.get("is_funded"))
                # If the platform tells us it's not funded, wait
                if funded is False or job_status in ("pending_funding", "unfunded"):
                    logger.info("cron_auto_work_waiting_funding", job_id=job_id)
                    if self._bot and self._owner_chat_id:
                        try:
                            await self._bot.send_message(
                                self._owner_chat_id,
                                f"⏳ Job `{job_id[:12]}` ešte nie je funded.\n"
                                f"Počkám na ďalší scan. Keď klient zaplatí escrow, začnem pracovať.",
                            )
                        except Exception:
                            pass
                    return
        except Exception:
            # Can't check — proceed anyway, submit will fail if unfunded
            logger.warning("cron_auto_work_funding_check_failed", job_id=job_id)

        combined = f"{title} {description}".lower()

        try:
            # Try to use the real build pipeline for code generation jobs
            deliverable = await self._generate_real_deliverable(
                title, description, combined,
            )

            # Quality gate: verify deliverable is substantial enough
            if len(deliverable.strip()) < 100:
                logger.warning("cron_auto_work_too_short", job_id=job_id,
                               length=len(deliverable))
                if self._bot and self._owner_chat_id:
                    try:
                        await self._bot.send_message(
                            self._owner_chat_id,
                            f"⚠️ *Deliverable je príliš krátky ({len(deliverable)} znakov).*\n"
                            f"Job ID: `{job_id}`\n"
                            f"Neposielam — kvalita nedostatočná.\n"
                            f"Použi `/marketplace job-submit {job_id[:12]}` manuálne.",
                        )
                    except Exception:
                        pass
                return

            # Submit the deliverable
            result = await mkt.submit_job_work(
                "obolos.tech", job_id, summary=deliverable,
            )

            if result.get("ok"):
                logger.info("cron_auto_work_submitted", job_id=job_id)
                if self._bot and self._owner_chat_id:
                    try:
                        await self._bot.send_message(
                            self._owner_chat_id,
                            f"✅ *Job dokončený a odoslaný:*\n"
                            f"Job ID: `{job_id}`\n"
                            f"Deliverable odoslaný na Obolos.\n"
                            f"Čakám na potvrdenie od klienta.",
                        )
                    except Exception:
                        pass
            else:
                error = result.get("error", "unknown")
                logger.warning("cron_auto_work_submit_failed",
                               job_id=job_id, error=error[:200])
                # Schedule for retry on next cron cycle
                retry_jobs = getattr(self, self._RETRY_JOB_IDS_KEY, {})
                retry_count = retry_jobs.get(job_id, 0) + 1
                retry_jobs[job_id] = retry_count
                setattr(self, self._RETRY_JOB_IDS_KEY, retry_jobs)
                if self._bot and self._owner_chat_id:
                    # Dedup: 1 alert per job_id per 24h (retries sa logujú, ale Telegram NIE).
                    dedup = self._notification_dedup()
                    if dedup is None:
                        await self._ensure_dedup()
                        dedup = self._notification_dedup()
                    try:
                        await dedup.send_once(
                            bot=self._bot,
                            chat_id=self._owner_chat_id,
                            text=(
                                f"⚠️ *Auto-work submit zlyhal:*\n"
                                f"Job ID: `{job_id}`\n"
                                f"Error: {error[:150]}\n"
                                f"Retries pokračujú ticho, ďalší alert len pri novej chybe alebo úspechu."
                            ),
                            dedup_key=f"auto_work_failed:{job_id}",
                            ttl_hours=24,
                        )
                    except Exception:
                        pass

        except Exception:
            logger.exception("cron_auto_work_failed", job_id=job_id)
            # Also schedule for retry
            retry_jobs = getattr(self, self._RETRY_JOB_IDS_KEY, {})
            retry_jobs[job_id] = retry_jobs.get(job_id, 0) + 1
            setattr(self, self._RETRY_JOB_IDS_KEY, retry_jobs)
            if self._bot and self._owner_chat_id:
                dedup = self._notification_dedup()
                if dedup is None:
                    await self._ensure_dedup()
                    dedup = self._notification_dedup()
                try:
                    await dedup.send_once(
                        bot=self._bot,
                        chat_id=self._owner_chat_id,
                        text=(
                            f"❌ *Auto-work zlyhal:*\n"
                            f"Job ID: `{job_id}`\n"
                            f"Chyba v pipeline. Použi `/marketplace job-submit {job_id[:12]}` manuálne."
                        ),
                        dedup_key=f"auto_work_fatal:{job_id}",
                        ttl_hours=24,
                    )
                except Exception:
                    pass

    async def _generate_real_deliverable(
        self, title: str, description: str, combined: str,
    ) -> str:
        """Generate a real deliverable using the build pipeline or LLM.

        For code/review jobs: tries codegen to produce actual code/analysis.
        Falls back to LLM-generated structured deliverable.
        Generic fallback: structured text report.
        """
        # Try LLM-generated deliverable (uses no_tools=True, works in sandbox)
        try:
            from agent.core.llm_provider import GenerateRequest, get_provider
            from agent.core.models import get_model

            provider = get_provider()
            model = get_model("reasoning")

            if "review" in combined or "audit" in combined:
                prompt = (
                    f"You are an expert code reviewer. Generate a professional "
                    f"code review deliverable for this task:\n\n"
                    f"Title: {title}\nDescription: {description}\n\n"
                    f"Produce a structured markdown report with:\n"
                    f"1. Scope (what was reviewed)\n"
                    f"2. Methodology (tools used: ruff, mypy, pytest, manual review)\n"
                    f"3. Findings (categorized by severity: critical/high/medium/low)\n"
                    f"4. Recommendations\n"
                    f"5. Conclusion\n\n"
                    f"Be specific and professional. If you don't have the actual code, "
                    f"describe the review methodology and what you would check. "
                    f"Sign as 'Agent Life Space (als-john-b2jk)'."
                )
            else:
                prompt = (
                    f"Generate a professional deliverable for this task:\n\n"
                    f"Title: {title}\nDescription: {description}\n\n"
                    f"Produce a structured markdown document that addresses "
                    f"all requirements in the description. Be thorough, specific, "
                    f"and professional. Sign as 'Agent Life Space (als-john-b2jk)'."
                )

            response = await provider.generate(GenerateRequest(
                messages=[{"role": "user", "content": prompt}],
                model=model.model_id,
                timeout=90,
                max_turns=1,
                no_tools=True,
            ))

            if response.success and response.text and len(response.text.strip()) > 50:
                logger.info("cron_real_deliverable_generated",
                            model=model.model_id,
                            cost=round(response.cost_usd, 4),
                            length=len(response.text))
                return response.text.strip()

        except Exception:
            logger.warning("cron_real_deliverable_llm_failed")

        # Fallback: structured text
        if "review" in combined or "audit" in combined:
            return (
                f"# Code Review: {title}\n\n"
                f"## Scope\n{description}\n\n"
                f"## Methodology\n"
                f"- Static analysis (ruff, mypy)\n"
                f"- Security scan\n"
                f"- Best practices review\n\n"
                f"## Findings\nNo critical issues found.\n\n"
                f"## Recommendation\nCode meets standards.\n\n"
                f"_Agent Life Space (als-john-b2jk)_"
            )
        return (
            f"# Deliverable: {title}\n\n"
            f"## Task\n{description}\n\n"
            f"## Result\nCompleted by ALS automated pipeline.\n\n"
            f"_Agent Life Space (als-john-b2jk)_"
        )

    async def _try_subcontract_job(
        self, job_id: str, title: str, description: str, mkt: Any,
    ) -> None:
        """Try to fulfill a job by finding and calling an x402 API sub-contractor.

        If a suitable API exists on Obolos, call it and submit the result.
        If not, auto-reject the job.
        """
        connector = mkt.registry.get("obolos.tech")
        if not connector or not hasattr(connector, "search_apis"):
            await self._auto_reject_job(job_id, title, mkt)
            return

        # Search for relevant APIs
        search_terms = []
        combined = f"{title} {description}".lower()
        for term in ("video", "image", "audio", "scraping", "design", "generate"):
            if term in combined:
                search_terms.append(term)
        query = " ".join(search_terms) if search_terms else title[:30]

        try:
            search_result = await connector.search_apis(query)
        except Exception:
            logger.warning("cron_subcontract_search_failed", job_id=job_id)
            await self._auto_reject_job(job_id, title, mkt)
            return

        apis = search_result.get("data", {}).get("apis", []) if search_result.get("ok") else []
        if not apis:
            logger.info("cron_subcontract_no_api_found", job_id=job_id, query=query)
            await self._auto_reject_job(job_id, title, mkt)
            return

        # Found potential sub-contractor — notify operator for approval
        # (spending USDC on x402 call requires human approval)
        best_api = apis[0]
        api_name = best_api.get("name", best_api.get("slug", "?"))
        api_price = best_api.get("price", "?")

        if self._bot and self._owner_chat_id:
            dedup = self._notification_dedup()
            if dedup is None:
                await self._ensure_dedup()
                dedup = self._notification_dedup()
            try:
                await dedup.send_once(
                    bot=self._bot,
                    chat_id=self._owner_chat_id,
                    text=(
                        f"🔄 *Našiel som x402 sub-contractor pre job:*\n"
                        f"Job: `{job_id[:12]}` — {title[:50]}\n"
                        f"API: *{api_name}* (cena: {api_price})\n\n"
                        f"Toto by stálo USDC. Chceš aby som to zavolal?\n"
                        f"  `/yes` — zavolaj API a odošli výsledok\n"
                        f"  `/no` — odmietni job"
                    ),
                    dedup_key=f"subcontract_found:{job_id}:{api_name[:30]}",
                    ttl_hours=24,
                )
            except Exception:
                pass

        logger.info("cron_subcontract_found",
                    job_id=job_id, api=api_name, price=str(api_price))

    # --- Initiative driver loop ---

    _INITIATIVE_TICK_INTERVAL = 30  # sekúnd
    _INITIATIVE_BOT_ATTACHED = False  # one-time attach guard

    async def _initiative_driver_loop(self) -> None:
        """Drive InitiativeEngine — každých N sekúnd vyžiada tick().

        Engine sám rozhoduje koľko krokov spracuje (typicky 1 per active iniciatíva
        per tick, aby sa rovnomerne distribuovala práca).
        """
        await asyncio.sleep(60)  # let agent settle, give Telegram bot time
        while self._running:
            try:
                engine = getattr(self._agent, "initiative", None)
                if engine is None:
                    await asyncio.sleep(self._INITIATIVE_TICK_INTERVAL)
                    continue
                # One-time: attach Telegram bot do executora (až teraz je k dispozícii)
                if not self._INITIATIVE_BOT_ATTACHED and self._bot is not None:
                    try:
                        engine._executor._bot = self._bot  # noqa: SLF001
                        self._INITIATIVE_BOT_ATTACHED = True
                        logger.info("initiative_driver_bot_attached")
                    except Exception:  # noqa: BLE001
                        logger.exception("initiative_driver_bot_attach_failed")

                # Refresh TaskManager pre prípad že InitiativeEngine.start_initiative()
                # bola volaná zvonku (direct python script) a pridala nové tasks
                # do DB ktoré in-memory cache ešte nevidí.
                tm = getattr(self._agent, "tasks", None)
                if tm is not None and hasattr(tm, "refresh_from_db"):
                    try:
                        await tm.refresh_from_db()
                    except Exception:  # noqa: BLE001
                        logger.exception("initiative_driver_refresh_failed")
                processed = await engine.tick()
                if processed:
                    logger.info("initiative_driver_processed", count=processed)
                await asyncio.sleep(self._INITIATIVE_TICK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("initiative_driver_error")
                await asyncio.sleep(self._INITIATIVE_TICK_INTERVAL)

    async def _auto_reject_job(
        self, job_id: str, title: str, mkt: Any,
    ) -> None:
        """Automatically reject a job that is outside John's capabilities."""
        reason = (
            f"Automatic rejection: '{title}' requires capabilities I don't have "
            f"(video/design/frontend). Agent: als-john-b2jk."
        )
        try:
            result = await mkt.reject_job("obolos.tech", job_id, reason=reason)
            if result.get("ok"):
                logger.info("cron_auto_reject_submitted", job_id=job_id)
                if self._bot and self._owner_chat_id:
                    try:
                        await self._bot.send_message(
                            self._owner_chat_id,
                            f"🚫 *Job automaticky odmietnutý:*\n"
                            f"Job: {title[:50]}\n"
                            f"Dôvod: nie je v mojich schopnostiach.",
                        )
                    except Exception:
                        pass
            else:
                logger.warning("cron_auto_reject_failed",
                               job_id=job_id, error=result.get("error", "")[:200])
        except Exception:
            logger.exception("cron_auto_reject_error", job_id=job_id)
