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
        logger.info("cron_started", jobs=13)

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
        if not hasattr(self._agent, "control_plane"):
            return
        try:
            pruned = self._agent.control_plane.prune_retained_artifacts(limit=5000)
            if pruned:
                logger.info("cron_retention_pruned", count=len(pruned))
            else:
                logger.info("cron_retention_pruned", count=0)
        except Exception:
            logger.exception("cron_retention_prune_error")

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

        Zero LLM tokens. Deterministic scan + Telegram delivery.
        """
        await asyncio.sleep(120)  # let agent settle
        while self._running:
            try:
                await self._do_marketplace_scan()
                await asyncio.sleep(self._MARKETPLACE_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cron_marketplace_monitor_error")

    async def _do_marketplace_scan(self) -> None:
        mkt = getattr(self._agent, "marketplace", None)
        if mkt is None:
            return

        # Scan listings + jobs
        try:
            listings = await mkt.list_listings(platform="obolos.tech", limit=20)
        except Exception:
            listings = []
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

        # Deliver via Telegram (use self._bot, not self._agent.telegram)
        if self._bot and self._owner_chat_id:
            try:
                await self._bot.send_message(self._owner_chat_id, report)
                logger.info("cron_marketplace_report_sent", listings=len(listings), fresh=len(fresh))
            except Exception:
                logger.warning("cron_marketplace_telegram_send_failed")
        else:
            logger.info("cron_marketplace_report_no_telegram", report_length=len(report))

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
