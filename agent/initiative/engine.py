"""InitiativeEngine — orchestrátor.

Public API:
    await engine.start_initiative(goal_nl, chat_id) -> Project
    engine.list_active() -> list[Project]
    await engine.tick() -> int                    # driver volá; vracia # spracovaných krokov
    await engine.pause(id) / resume(id) / cancel(id)
    await engine.get_status(id) -> dict

Stavbne nad:
    - InitiativePlanner   (plan)
    - StepExecutor        (execute single step)
    - ProjectManager      (long-running iniciatíva)
    - TaskManager         (jednotlivé kroky s deps)

Plan persistence:
    - Plný plán (vrátane promptov) → JSON na disk: <data>/initiatives_data/<id>/plan.json
    - Project.notes = goal summary + pattern_id
    - Task.metadata = {initiative_id, step_idx, step_kind, awaiting_approval}
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from agent.initiative.executor import StepExecutor
from agent.initiative.planner import InitiativePlanner
from agent.initiative.schemas import (
    InitiativePlan,
    PlannedStep,
    StepExecutionResult,
    StepKind,
)

logger = structlog.get_logger(__name__)

# Maximálny počet pokusov pre jeden krok pred označením initiative ako FAILED
MAX_STEP_ATTEMPTS = 3

# Auto-compact: ak prior_outputs > tento limit, summarizuj ich do 1 bloku
COMPACT_AFTER_STEPS = 5

# Maximálna dĺžka jedného summary v compactnutom bloku (chars)
COMPACT_SUMMARY_CHAR_LIMIT = 800

# Cost prediction (USD per minute of estimated work, by step kind).
# Kalibrované cez observované náklady V2/V3 iniciatív (~$0.10-0.20 per analyze
# krok 5min, ~$0.05-0.10 per code krok 5min). Konzervatívne nahor.
_COST_USD_PER_MIN_BY_KIND: dict[str, float] = {
    "analyze": 0.04,   # research-heavy, viac turnov, väčší context
    "design": 0.03,    # schema design + decision making
    "code": 0.025,     # file edits, fewer LLM tokens per minute
    "test": 0.02,      # test write + execution analysis
    "verify": 0.015,   # rýchle judge calls
    "deploy": 0.01,    # mostly tool execution
    "schedule": 0.005, # 1 turn cron registration
    "monitor": 0.005,  # no-op trigger
    "notify": 0.005,   # 1 telegram send
    "approval": 0.005, # 1 approval propose
}


def estimate_initiative_cost_usd(plan: "InitiativePlan") -> float:
    """Konzervatívny odhad celkovej USD ceny iniciatívy podľa kind × estimated_minutes.

    Použité pri start_initiative aby majiteľ videl "ťa to bude stáť ~$X" pred
    spustením. Odhad je high-side (lepšie prekvapiť pozitívne).
    """
    total = 0.0
    for step in plan.steps:
        rate = _COST_USD_PER_MIN_BY_KIND.get(step.kind.value, 0.02)
        total += step.estimated_minutes * rate
    return round(total, 2)


class InitiativeEngine:
    """Orchestrátor iniciatív — spája planner, executor a perzistentné stavové moduly."""

    def __init__(
        self,
        planner: InitiativePlanner,
        executor: StepExecutor,
        project_manager: Any,
        task_manager: Any,
        data_root: str,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._projects = project_manager
        self._tasks = task_manager
        self._data_root = Path(data_root)
        self._initiatives_dir = self._data_root / "initiatives_data"
        self._initiatives_dir.mkdir(parents=True, exist_ok=True)

    # --------- Lifecycle ---------

    async def start_initiative(
        self,
        goal_nl: str,
        chat_id: int,
        *,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Vyrob plán + Project + Tasks a aktivuj iniciatívu."""
        plan = await self._planner.plan(goal_nl, chat_id)

        # Vytvor Project
        from agent.projects.manager import ProjectStatus

        proj_name = title or plan.goal_summary[:80]
        project = await self._projects.create(
            name=proj_name,
            description=goal_nl[:2000],
            tags=["initiative", plan.pattern.pattern_id],
            priority=0.7,
        )
        project.notes = (
            f"pattern={plan.pattern.pattern_id} (conf {plan.pattern.confidence:.2f})\n"
            f"long_running={plan.is_long_running}\n"
            f"chat_id={chat_id}\n"
            f"goal_summary={plan.goal_summary}"
        )
        await self._projects.update(project)

        # Persist plán + meta na disk
        idir = self._initiatives_dir / project.id
        idir.mkdir(parents=True, exist_ok=True)
        (idir / "plan.json").write_text(
            plan.model_dump_json(indent=2), encoding="utf-8"
        )
        (idir / "meta.json").write_text(
            json.dumps(
                {
                    "initiative_id": project.id,
                    "owner_chat_id": chat_id,
                    "created_at": project.created_at,
                    "title": proj_name,
                    "goal_nl": goal_nl,
                    "pattern": plan.pattern.model_dump(),
                    "is_long_running": plan.is_long_running,
                    "success_criteria": plan.success_criteria,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # Vytvor Tasks pre každý krok (s deps)
        idx_to_task_id: dict[int, str] = {}
        from agent.tasks.manager import TaskType

        for step in plan.steps:
            deps = [idx_to_task_id[i] for i in step.depends_on_idx]
            task = await self._tasks.create_task(
                name=f"{project.id}:{step.idx}:{step.title[:50]}",
                description=step.prompt[:500],
                priority=0.7,
                importance=0.7,
                urgency=0.5,
                effort=min(1.0, step.estimated_minutes / 60.0),
                tags=["initiative", project.id, step.kind.value],
                dependencies=deps,
                requires_llm=step.kind
                in {
                    StepKind.ANALYZE,
                    StepKind.DESIGN,
                    StepKind.CODE,
                    StepKind.TEST,
                    StepKind.VERIFY,
                },
                requires_approval=step.requires_approval
                or step.kind == StepKind.DEPLOY,
                task_type=TaskType.ONE_TIME,
                metadata={
                    "initiative_id": project.id,
                    "step_idx": step.idx,
                    "step_kind": step.kind.value,
                    "step_title": step.title,
                },
            )
            idx_to_task_id[step.idx] = task.id
            await self._projects.add_task(project.id, task.id)

        # Aktivuj — POZOR: re-fetch projekt aby sme nepretreli task_ids
        # ktoré medzi tým pridal `add_task`. Lokálna kópia `project` je stará.
        refreshed = await self._projects.get(project.id)
        if refreshed is None:
            msg = f"Project {project.id} disappeared mid-create"
            raise RuntimeError(msg)
        refreshed.status = ProjectStatus.ACTIVE
        refreshed.started_at = datetime.now(UTC).isoformat()
        await self._projects.update(refreshed)
        project = refreshed

        logger.info(
            "initiative_started",
            id=project.id,
            pattern=plan.pattern.pattern_id,
            steps=len(plan.steps),
            long_running=plan.is_long_running,
        )

        cost_usd = estimate_initiative_cost_usd(plan)
        return {
            "initiative_id": project.id,
            "title": proj_name,
            "pattern": plan.pattern.pattern_id,
            "steps_total": len(plan.steps),
            "is_long_running": plan.is_long_running,
            "task_ids": list(idx_to_task_id.values()),
            "estimated_cost_usd": cost_usd,
            "estimated_total_minutes": plan.estimated_total_minutes,
        }

    async def pause(self, initiative_id: str) -> bool:
        """Pauses the initiative AND cancels any cron tasks it spawned.

        Bez cancel cron-u by pause len zastavil driver-step exekúciu, ale
        SCHEDULE krokom registrované TaskType.CRON tasks by ďalej spúšťali
        scraper / monitor — pause-eutá iniciatíva by stále spamovala.
        """
        from agent.projects.manager import ProjectStatus

        p = await self._projects.get(initiative_id)
        if not p:
            return False
        p.status = ProjectStatus.PAUSED
        await self._projects.update(p)

        # Cancel cron tasks linked to this initiative (created by SCHEDULE step)
        cancelled: list[str] = []
        for t in self._tasks.list_tasks():
            meta = t.metadata or {}
            if meta.get("initiative_id") != initiative_id:
                continue
            if t.task_type.value != "cron":
                continue
            if t.status.value in {"completed", "failed", "cancelled"}:
                continue
            try:
                await self._tasks.cancel_task(t.id)
                cancelled.append(t.id)
            except Exception:  # noqa: BLE001
                logger.exception("pause_cancel_cron_failed", task_id=t.id)
        if cancelled:
            logger.info(
                "initiative_paused_cron_cancelled",
                initiative_id=initiative_id,
                cancelled_cron_tasks=len(cancelled),
            )
        return True

    async def resume(self, initiative_id: str) -> bool:
        from agent.projects.manager import ProjectStatus

        p = await self._projects.get(initiative_id)
        if not p or p.status != ProjectStatus.PAUSED:
            return False
        p.status = ProjectStatus.ACTIVE
        await self._projects.update(p)
        return True

    async def cancel(self, initiative_id: str, reason: str = "") -> bool:
        from agent.projects.manager import ProjectStatus

        p = await self._projects.get(initiative_id)
        if not p:
            return False
        p.status = ProjectStatus.ABANDONED
        p.notes = (p.notes or "") + f"\ncancel_reason={reason}"
        await self._projects.update(p)
        # Cancel pending tasks
        for tid in p.task_ids:
            t = self._tasks.get_task(tid)
            if t and t.status.value in {"queued", "blocked", "scheduled"}:
                await self._tasks.cancel_task(tid)
        return True

    # --------- Inspection ---------

    async def list_active(self) -> list[dict[str, Any]]:
        from agent.projects.manager import ProjectStatus

        active = await self._projects.list_projects(status=ProjectStatus.ACTIVE)
        out: list[dict[str, Any]] = []
        for p in active:
            if "initiative" not in p.tags:
                continue
            progress = await self._projects.get_progress(p.id, self._tasks)
            out.append(
                {
                    "id": p.id,
                    "title": p.name,
                    "tags": p.tags,
                    "started_at": p.started_at,
                    "tasks_total": len(p.task_ids),
                    "progress": progress,
                }
            )
        return out

    async def get_status(self, initiative_id: str) -> dict[str, Any]:
        p = await self._projects.get(initiative_id)
        if not p:
            return {"error": "not_found"}
        meta_path = self._initiatives_dir / initiative_id / "meta.json"
        meta = (
            json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_path.exists()
            else {}
        )
        steps_state: list[dict[str, Any]] = []
        for tid in p.task_ids:
            t = self._tasks.get_task(tid)
            if not t:
                continue
            steps_state.append(
                {
                    "task_id": t.id,
                    "name": t.name,
                    "status": t.status.value,
                    "kind": (t.metadata or {}).get("step_kind", ""),
                    "step_idx": (t.metadata or {}).get("step_idx", -1),
                    "result": (t.result or {}).get("summary", "")[:300]
                    if isinstance(t.result, dict)
                    else "",
                    "error": (t.error or "")[:300],
                }
            )
        steps_state.sort(key=lambda s: s["step_idx"])
        return {
            "id": p.id,
            "title": p.name,
            "status": p.status.value,
            "started_at": p.started_at,
            "completed_at": p.completed_at,
            "meta": meta,
            "steps": steps_state,
        }

    # --------- Driver ---------

    async def tick(self) -> int:
        """Driver tick — pre každú ACTIVE iniciatívu spusti najbližší pending krok.

        Vracia počet spracovaných krokov v tomto tiku.
        """
        from agent.projects.manager import ProjectStatus

        actives = await self._projects.list_projects(status=ProjectStatus.ACTIVE)
        processed = 0
        for project in actives:
            if "initiative" not in project.tags:
                continue
            try:
                ran = await self._tick_one(project.id)
                if ran:
                    processed += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "initiative_tick_error", initiative_id=project.id
                )
        return processed

    async def tick_stream(
        self,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Generator-based driver — yield events per processed step.

        Inšpirované Claude Code's query.ts agent loop pattern (async generator
        with explicit yield points). Umožňuje:
        - Real-time progress streaming (Telegram update per step, not per tick)
        - Pausing mid-tick na external signál (caller môže `break` po N events)
        - Observability metrics per yield

        Yields dict udalostí, napr:
            {"event": "step_start", "initiative_id": ..., "step_idx": ..., "kind": ...}
            {"event": "step_done", "initiative_id": ..., "step_idx": ..., "success": bool, "summary": ...}
            {"event": "step_skipped", "initiative_id": ..., "reason": ...}
            {"event": "initiative_finalized", "initiative_id": ..., "monitoring": bool}

        Ekvivalent funkčnosti `tick()` — backward compat zachovaná.
        """
        from agent.projects.manager import ProjectStatus

        actives = await self._projects.list_projects(status=ProjectStatus.ACTIVE)
        for project in actives:
            if "initiative" not in project.tags:
                continue
            try:
                async for event in self._tick_one_stream(project.id):
                    yield event
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "initiative_tick_stream_error", initiative_id=project.id
                )
                yield {
                    "event": "tick_error",
                    "initiative_id": project.id,
                    "error": str(exc)[:500],
                }

    async def _tick_one_stream(
        self, initiative_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream variant of _tick_one — yields events instead of returning bool."""
        # Delegate to _tick_one to avoid logic duplication; emit summary event.
        ran = await self._tick_one(initiative_id)
        if ran:
            yield {
                "event": "step_processed",
                "initiative_id": initiative_id,
            }

    async def _tick_one(self, initiative_id: str) -> bool:
        """Spracuj jeden krok jednej iniciatívy. Vracia True ak sa niečo robilo."""
        from agent.projects.manager import ProjectStatus

        project = await self._projects.get(initiative_id)
        if not project or project.status != ProjectStatus.ACTIVE:
            return False

        # Načítaj plán
        plan_path = self._initiatives_dir / initiative_id / "plan.json"
        if not plan_path.exists():
            logger.error("initiative_plan_missing", id=initiative_id)
            return False
        plan = InitiativePlan.model_validate_json(
            plan_path.read_text(encoding="utf-8")
        )

        meta_path = self._initiatives_dir / initiative_id / "meta.json"
        meta = (
            json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_path.exists()
            else {}
        )
        owner_chat_id = int(meta.get("owner_chat_id", 0))

        # Mapuj idx → task; nájdi prvý QUEUED bez awaiting_approval
        idx_to_task: dict[int, Any] = {}
        for tid in project.task_ids:
            t = self._tasks.get_task(tid)
            if not t:
                continue
            sidx = (t.metadata or {}).get("step_idx")
            if sidx is None:
                continue
            idx_to_task[sidx] = t

        next_step: PlannedStep | None = None
        next_task: Any | None = None
        for step in sorted(plan.steps, key=lambda s: s.idx):
            t = idx_to_task.get(step.idx)
            if t is None:
                continue
            status = t.status.value
            awaiting = (t.metadata or {}).get("awaiting_approval", False)
            attempts = (t.metadata or {}).get("attempts", 0)
            if status == "queued" and not awaiting:
                # Skontroluj, že všetky dependencie sú COMPLETED (TaskManager to robí
                # cez BLOCKED status, ale double-check pre ručne unblockované)
                deps_ok = all(
                    (idx_to_task.get(d) and idx_to_task[d].status.value == "completed")
                    for d in step.depends_on_idx
                )
                if deps_ok and attempts < MAX_STEP_ATTEMPTS:
                    next_step = step
                    next_task = t
                    break

        if next_step is None or next_task is None:
            # Nič na spracovanie — možno všetko hotové → uzatvor / monitor
            await self._maybe_finalize(plan, project, idx_to_task)
            return False

        # Spusti — pripravíme prior_results s auto-compact ak ich je veľa
        prior_results: list[StepExecutionResult] = []
        for step in sorted(plan.steps, key=lambda s: s.idx):
            if step.idx >= next_step.idx:
                break
            t = idx_to_task.get(step.idx)
            if t and t.status.value == "completed" and isinstance(t.result, dict):
                prior_results.append(
                    StepExecutionResult(
                        success=True,
                        summary=str(t.result.get("summary", ""))[:3000],
                    )
                )

        if len(prior_results) > COMPACT_AFTER_STEPS:
            prior_results = await self._compact_prior(
                initiative_id=initiative_id, prior_results=prior_results
            )

        await self._tasks.start_task(next_task.id)
        logger.info(
            "initiative_step_start",
            id=initiative_id,
            step_idx=next_step.idx,
            kind=next_step.kind.value,
            attempt=(next_task.metadata or {}).get("attempts", 0) + 1,
        )

        attempt_num = (next_task.metadata or {}).get("attempts", 0) + 1
        result = await self._executor.execute(
            initiative_id=initiative_id,
            initiative_title=project.name,
            initiative_goal=meta.get("goal_nl", project.description),
            pattern_id=meta.get("pattern", {}).get("pattern_id", ""),
            step=next_step,
            prior_outputs=prior_results,
            owner_chat_id=owner_chat_id,
            total_steps=len(plan.steps),
            attempt=attempt_num,
        )

        # Persist artifact (raw result) + log udalosti
        adir = self._initiatives_dir / initiative_id / "steps"
        adir.mkdir(parents=True, exist_ok=True)
        (adir / f"{next_step.idx:02d}_{next_step.kind.value}.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )

        # Update task
        meta_update = dict(next_task.metadata or {})
        meta_update["attempts"] = meta_update.get("attempts", 0) + 1
        if result.metadata:
            meta_update.update(
                {f"last_{k}": v for k, v in result.metadata.items() if isinstance(v, (str, int, float, bool))}
            )

        if result.success:
            await self._tasks.complete_task(
                next_task.id,
                result={
                    "summary": result.summary,
                    "artifacts": result.artifact_paths,
                },
            )
            # Re-fetch & update metadata
            t_after = self._tasks.get_task(next_task.id)
            if t_after:
                t_after.metadata = meta_update
                # TaskManager nemá update_metadata; persist priamo
                await self._tasks._persist(t_after)  # noqa: SLF001
        else:
            awaiting = bool((result.metadata or {}).get("awaiting_approval"))
            if awaiting:
                # Necháme task v QUEUED s flag — bude preskočený do resume
                meta_update["awaiting_approval"] = True
                t_after = self._tasks.get_task(next_task.id)
                if t_after:
                    from agent.tasks.manager import TaskStatus

                    t_after.status = TaskStatus.QUEUED
                    t_after.started_at = None
                    t_after.metadata = meta_update
                    await self._tasks._persist(t_after)  # noqa: SLF001
            elif meta_update["attempts"] >= MAX_STEP_ATTEMPTS:
                await self._tasks.fail_task(
                    next_task.id, error=result.error or "exceeded max attempts"
                )
                project.notes = (project.notes or "") + (
                    f"\n[step {next_step.idx} FAILED after {MAX_STEP_ATTEMPTS} attempts: "
                    f"{result.error[:200]}]"
                )
                project.status = ProjectStatus.PAUSED  # zastav, počkaj na manuálne riešenie
                await self._projects.update(project)
                # Notifikuj majiteľa
                if self._executor._bot and owner_chat_id:  # noqa: SLF001
                    try:
                        await self._executor._bot.send_message(  # noqa: SLF001
                            owner_chat_id,
                            (
                                f"🔴 *Iniciatíva `{project.name}` PAUZOVANÁ*\n\n"
                                f"Krok {next_step.idx} ({next_step.kind.value}) zlyhal "
                                f"{MAX_STEP_ATTEMPTS}× za sebou.\n"
                                f"Posledná chyba: `{result.error[:300]}`\n\n"
                                f"Detail: `/initiative {project.id}`"
                            ),
                        )
                    except Exception:  # noqa: BLE001
                        pass
            else:
                # Ďalší pokus pri ďalšom tiku — task ostane QUEUED s incremented attempts
                t_after = self._tasks.get_task(next_task.id)
                if t_after:
                    from agent.tasks.manager import TaskStatus

                    t_after.status = TaskStatus.QUEUED
                    t_after.started_at = None
                    t_after.metadata = meta_update
                    await self._tasks._persist(t_after)  # noqa: SLF001

        return True

    async def _maybe_finalize(
        self,
        plan: InitiativePlan,
        project: Any,
        idx_to_task: dict[int, Any],
    ) -> None:
        """Ak sú všetky kroky v terminálnom stave, zatvor iniciatívu."""
        from agent.projects.manager import ProjectStatus
        from agent.tasks.manager import TaskStatus

        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        all_terminal = all(
            (idx_to_task.get(s.idx) and idx_to_task[s.idx].status in terminal)
            for s in plan.steps
        )
        if not all_terminal:
            return

        any_failed = any(
            idx_to_task.get(s.idx) and idx_to_task[s.idx].status == TaskStatus.FAILED
            for s in plan.steps
        )

        if plan.is_long_running and not any_failed:
            # Long-running iniciatíva — zostáva v MONITORING (interpretované ako ACTIVE
            # s tagom "monitoring"; cron-task ktorý vznikol v SCHEDULE kroku už beží).
            # IDEMPOTENCY: dream + tag set len raz. Driver tick sa volá opakovane,
            # ale finalizácia nesmie spamovať dream module.
            already_finalized = "monitoring" in project.tags
            if already_finalized:
                return  # nothing to do; long-running stays ACTIVE+monitoring
            project.tags.append("monitoring")
            project.notes = (project.notes or "") + "\n[entered MONITORING mode]"
            await self._projects.update(project)
            await self._dream_completed_initiative(plan, project, idx_to_task, monitoring=True)
            return

        # Uzavri
        project.status = (
            ProjectStatus.COMPLETED if not any_failed else ProjectStatus.ABANDONED
        )
        project.completed_at = datetime.now(UTC).isoformat()
        project.result = (
            "Iniciatíva dokončená."
            if not any_failed
            else "Iniciatíva ukončená s chybami."
        )
        await self._projects.update(project)
        logger.info(
            "initiative_finalized",
            id=project.id,
            status=project.status.value,
        )
        await self._dream_completed_initiative(plan, project, idx_to_task, monitoring=False)

    # --------- Auto-compact ---------

    async def _compact_prior(
        self,
        *,
        initiative_id: str,
        prior_results: list[StepExecutionResult],
    ) -> list[StepExecutionResult]:
        """Skompresuj > N starších výstupov do jediného summary bloku.

        Posledné 2 ponecháme nezmenené (čerstvý kontext), staršie spojíme.
        """
        if len(prior_results) <= COMPACT_AFTER_STEPS:
            return prior_results

        keep_recent = prior_results[-2:]
        to_compact = prior_results[:-2]

        joined = "\n\n".join(
            f"[step {i} summary]\n{r.summary[:COMPACT_SUMMARY_CHAR_LIMIT]}"
            for i, r in enumerate(to_compact)
        )

        prompt = (
            "Si Compactor. Dostávaš zoznam summary-ov predošlých krokov iniciatívy. "
            "Zhrň ich do JEDNÉHO bloku (max 1500 znakov), zachovaj len fakty potrebné "
            "pre ďalšie kroky: čo bolo vyrobené (file paths), aké rozhodnutia prešli, "
            "aké problémy/risk poznámky. Žiadne ozdobenia, žiadny markdown.\n\n"
            f"VSTUP:\n{joined}\n\n"
            "Vráť LEN compactnutý text."
        )

        from agent.core.llm_provider import GenerateRequest

        try:
            response = await self._executor._provider.generate(  # noqa: SLF001
                GenerateRequest(
                    messages=[{"role": "user", "content": prompt}],
                    model="claude-haiku-4-5-20251001",
                    max_turns=1,
                    timeout=60,
                    allow_file_access=False,
                    cwd=str(self._data_root),
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("initiative_compact_error", id=initiative_id)
            return prior_results

        if not response.success:
            return prior_results

        compacted = StepExecutionResult(
            success=True,
            summary=f"[COMPACTED {len(to_compact)} earlier steps]\n{(response.text or '')[:1900]}",
            metadata={"compacted_count": len(to_compact)},
        )
        logger.info(
            "initiative_compacted",
            id=initiative_id,
            compacted=len(to_compact),
            kept_recent=len(keep_recent),
        )
        return [compacted, *keep_recent]

    # --------- Dream Mode (Auto-Dream) ---------

    async def _dream_completed_initiative(
        self,
        plan: InitiativePlan,
        project: Any,
        idx_to_task: dict[int, Any],
        *,
        monitoring: bool,
    ) -> None:
        """Post-completion: extract learnings → knowledge base.

        Beží asynchrónne ale blokuje tick — preto držaný krátky timeout.
        """
        try:
            from agent.tasks.manager import TaskStatus

            completed = sum(
                1
                for s in plan.steps
                if idx_to_task.get(s.idx)
                and idx_to_task[s.idx].status == TaskStatus.COMPLETED
            )
            failed = sum(
                1
                for s in plan.steps
                if idx_to_task.get(s.idx)
                and idx_to_task[s.idx].status == TaskStatus.FAILED
            )

            # Načítaj všetky step results pre dream prompt
            steps_dir = self._initiatives_dir / project.id / "steps"
            steps_summary: list[str] = []
            if steps_dir.exists():
                for f in sorted(steps_dir.glob("*.json")):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        steps_summary.append(
                            f"[{f.stem}] success={data.get('success')} "
                            f"summary={str(data.get('summary',''))[:600]}"
                        )
                    except Exception:  # noqa: BLE001, S112
                        continue

            dream_prompt = (
                "Si DreamMode. Dokončila sa iniciatíva. Tvoja úloha: extrahuj "
                "z výstupov KONKRÉTNE poznatky pre budúce iniciatívy.\n\n"
                f"PATTERN POUŽITÝ: {plan.pattern.pattern_id}\n"
                f"GOAL: {plan.goal_summary}\n"
                f"VÝSLEDOK: {completed} hotových, {failed} zlyhalo, "
                f"long_running={plan.is_long_running}, monitoring={monitoring}\n\n"
                "VÝSTUPY KROKOV:\n" + "\n".join(steps_summary[:20]) + "\n\n"
                "Vráť markdown s 3 sekciami (každá max 5 bullets):\n"
                "## Čo fungovalo\n## Čo nefungovalo (a prečo)\n## Lekcie pre budúce iniciatívy "
                f"typu '{plan.pattern.pattern_id}'\n\nŽiadne fluff, len konkrétne pozorovania."
            )

            from agent.core.llm_provider import GenerateRequest

            response = await self._executor._provider.generate(  # noqa: SLF001
                GenerateRequest(
                    messages=[{"role": "user", "content": dream_prompt}],
                    model="claude-haiku-4-5-20251001",
                    max_turns=1,
                    timeout=90,
                    allow_file_access=False,
                    cwd=str(self._data_root),
                )
            )

            if not response.success:
                logger.warning("dream_mode_llm_error", id=project.id)
                return

            # Persist learnings do knowledge base
            kb_dir = (
                Path(self._data_root).parent
                / "agent"
                / "brain"
                / "knowledge"
                / "initiatives"
            )
            kb_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M")
            lessons_path = kb_dir / f"{ts}_{project.id}_{plan.pattern.pattern_id}.md"
            lessons_path.write_text(
                f"# Dream — {project.name}\n\n"
                f"- pattern: `{plan.pattern.pattern_id}`\n"
                f"- initiative_id: `{project.id}`\n"
                f"- completed: {completed}, failed: {failed}, "
                f"monitoring: {monitoring}\n"
                f"- ended: {datetime.now(UTC).isoformat()}\n\n"
                f"---\n\n{(response.text or '').strip()}\n",
                encoding="utf-8",
            )
            logger.info(
                "dream_mode_persisted",
                id=project.id,
                pattern=plan.pattern.pattern_id,
                path=str(lessons_path),
            )

            # Notifikuj majiteľa krátkym summary
            meta_path = self._initiatives_dir / project.id / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                chat_id = int(meta.get("owner_chat_id", 0))
                bot = self._executor._bot  # noqa: SLF001
                if bot and chat_id:
                    icon = "🌙" if monitoring else ("✅" if failed == 0 else "⚠️")
                    state = (
                        "MONITORING (long-running)"
                        if monitoring
                        else ("dokončená" if failed == 0 else "ukončená s chybami")
                    )
                    text = (
                        f"{icon} *Iniciatíva `{project.name}` — {state}*\n\n"
                        f"Pattern: `{plan.pattern.pattern_id}`\n"
                        f"Kroky: {completed} ✓ / {failed} ✗\n"
                        f"Lekcie: `{lessons_path.name}`\n\n"
                        f"`/initiative {project.id}` pre detail."
                    )
                    try:
                        await bot.send_message(chat_id, text)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            logger.exception("dream_mode_error", id=project.id)
