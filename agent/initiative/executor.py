"""StepExecutor — vykoná jeden PlannedStep.

Routing podľa StepKind:
    ANALYZE/DESIGN/CODE/TEST → LLM call (file access podľa FILE_TOUCHING_KINDS)
    VERIFY                   → LLM call s VERIFIER_SYSTEM_PROMPT
    SCHEDULE                 → vytvorí recurring TaskManager task
    NOTIFY                   → pošle správu cez TelegramBot
    DEPLOY/APPROVAL          → vytvorí approval request, krok zostane PENDING_APPROVAL
    MONITOR                  → no-op pri prvej exekúcii (initiative prejde do MONITORING)

Žiadny krok nepúšťa shell priamo — všetko cez existujúce moduly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from agent.initiative.planner import load_pattern_detail
from agent.initiative.prompts import (
    EXECUTOR_STEP_PROMPT_TEMPLATE,
    VERIFIER_SYSTEM_PROMPT,
)
from agent.initiative.schemas import (
    APPROVAL_REQUIRED_KINDS,
    FILE_TOUCHING_KINDS,
    PlannedStep,
    StepExecutionResult,
    StepKind,
)

logger = structlog.get_logger(__name__)


class StepExecutor:
    """Vykonávač jedného kroku iniciatívy."""

    def __init__(
        self,
        provider: Any,
        agent_name: str,
        project_root: str,
        data_root: str,
        telegram_bot: Any = None,
        task_manager: Any = None,
        approval_queue: Any = None,
        executor_model_id: str = "claude-sonnet-4-6",
        executor_max_turns: int = 6,
        executor_timeout: int = 600,
    ) -> None:
        self._provider = provider
        self._agent_name = agent_name
        self._project_root = project_root
        self._data_root = data_root
        self._bot = telegram_bot
        self._tasks = task_manager
        self._approvals = approval_queue
        self._model_id = executor_model_id
        self._max_turns = executor_max_turns
        self._timeout = executor_timeout

    def initiative_data_dir(self, initiative_id: str) -> Path:
        d = Path(self._data_root) / "initiatives_data" / initiative_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def execute(
        self,
        *,
        initiative_id: str,
        initiative_title: str,
        initiative_goal: str,
        pattern_id: str,
        step: PlannedStep,
        prior_outputs: list[StepExecutionResult],
        owner_chat_id: int,
        total_steps: int,
    ) -> StepExecutionResult:
        """Vykonaj krok. Routing podľa kind."""
        kind = step.kind

        # Schválenie potrebné — vytvor approval request a vráť pending
        if kind in APPROVAL_REQUIRED_KINDS or step.requires_approval:
            return await self._handle_approval(
                initiative_id=initiative_id,
                initiative_title=initiative_title,
                step=step,
                owner_chat_id=owner_chat_id,
            )

        if kind == StepKind.SCHEDULE:
            return await self._handle_schedule(
                initiative_id=initiative_id, step=step
            )

        if kind == StepKind.NOTIFY:
            return await self._handle_notify(
                initiative_id=initiative_id,
                step=step,
                owner_chat_id=owner_chat_id,
            )

        if kind == StepKind.MONITOR:
            return StepExecutionResult(
                success=True,
                summary=(
                    "Monitor krok aktivovaný. Iniciatíva prejde do MONITORING. "
                    "Driver bude periodicky spúšťať schedule-task."
                ),
                metadata={"monitor_active": True},
            )

        if kind == StepKind.VERIFY:
            return await self._handle_verify(
                initiative_id=initiative_id,
                step=step,
                prior_outputs=prior_outputs,
            )

        # ANALYZE / DESIGN / CODE / TEST → LLM
        return await self._handle_llm_step(
            initiative_id=initiative_id,
            initiative_title=initiative_title,
            initiative_goal=initiative_goal,
            pattern_id=pattern_id,
            step=step,
            prior_outputs=prior_outputs,
            total_steps=total_steps,
        )

    async def _handle_llm_step(
        self,
        *,
        initiative_id: str,
        initiative_title: str,
        initiative_goal: str,
        pattern_id: str,
        step: PlannedStep,
        prior_outputs: list[StepExecutionResult],
        total_steps: int,
    ) -> StepExecutionResult:
        prior_summary = "\n".join(
            f"- step {i}: {r.summary[:300]}"
            for i, r in enumerate(prior_outputs[-5:])
        ) or "(žiadne predošlé kroky)"

        pattern_block = load_pattern_detail(pattern_id)
        pattern_section = (
            f"\n\nRELEVANTNÝ PATTERN ({pattern_id}):\n{pattern_block[:4000]}"
            if pattern_block
            else ""
        )

        prompt = EXECUTOR_STEP_PROMPT_TEMPLATE.format(
            agent_name=self._agent_name,
            initiative_title=initiative_title,
            initiative_goal=initiative_goal,
            step_idx=step.idx,
            total_steps=total_steps,
            step_kind=step.kind.value,
            step_title=step.title,
            prior_outputs=prior_summary,
            step_prompt=step.prompt,
            project_root=self._project_root,
            data_root=self._data_root,
            initiative_id=initiative_id,
        ) + pattern_section

        from agent.core.llm_provider import GenerateRequest

        response = await self._provider.generate(
            GenerateRequest(
                messages=[{"role": "user", "content": prompt}],
                model=self._model_id,
                max_turns=self._max_turns,
                timeout=self._timeout,
                allow_file_access=step.kind in FILE_TOUCHING_KINDS,
                cwd=self._project_root,
            )
        )

        if not response.success:
            return StepExecutionResult(
                success=False,
                error=(response.error or "LLM provider error")[:1900],
                summary=f"LLM zlyhal pri kroku {step.idx} ({step.kind.value}).",
            )

        return StepExecutionResult(
            success=True,
            summary=(response.text or "")[:3900],
            metadata={"model": self._model_id, "kind": step.kind.value},
        )

    async def _handle_verify(
        self,
        *,
        initiative_id: str,
        step: PlannedStep,
        prior_outputs: list[StepExecutionResult],
    ) -> StepExecutionResult:
        last = prior_outputs[-1] if prior_outputs else None
        if last is None:
            return StepExecutionResult(
                success=False,
                error="VERIFY krok bez predošlého výstupu.",
            )

        prompt = (
            VERIFIER_SYSTEM_PROMPT
            + "\n\n---\n\n"
            + f"PREDOŠLÝ KROK SUMMARY:\n{last.summary[:3000]}\n\n"
            + f"VERIFICATION TASK:\n{step.prompt[:2000]}\n\n"
            + "Vráť LEN JSON podľa schémy."
        )

        from agent.core.llm_provider import GenerateRequest

        response = await self._provider.generate(
            GenerateRequest(
                messages=[{"role": "user", "content": prompt}],
                model=self._model_id,
                max_turns=1,
                timeout=120,
                allow_file_access=False,
                cwd=self._project_root,
            )
        )

        if not response.success:
            return StepExecutionResult(
                success=False,
                error=(response.error or "")[:1900],
                summary="Verifier LLM call zlyhal.",
            )

        # Parse verifier output
        text = (response.text or "").strip()
        try:
            from agent.initiative.planner import _extract_json

            data = _extract_json(text)
            success = bool(data.get("success", False))
            summary = str(data.get("summary", ""))[:1900]
            issues = data.get("issues", [])
            issues_text = "\n".join(f"- {i}" for i in issues[:10])
            return StepExecutionResult(
                success=success,
                summary=summary + (f"\n\nIssues:\n{issues_text}" if issues else ""),
                next_step_hint=str(data.get("next_step_hint", ""))[:400],
                metadata={"verifier_raw": text[:500]},
            )
        except (ValueError, json.JSONDecodeError):
            # Fallback: pessimistic — ak verifier nevrátil platný JSON, fail
            return StepExecutionResult(
                success=False,
                error="Verifier nevrátil platný JSON.",
                summary=text[:1900],
            )

    async def _handle_schedule(
        self, *, initiative_id: str, step: PlannedStep
    ) -> StepExecutionResult:
        if not self._tasks:
            return StepExecutionResult(
                success=False,
                error="TaskManager nie je k dispozícii.",
            )

        cron = step.metadata.get("cron_expression") or "0 */6 * * *"
        target_module = step.metadata.get("target_module", "")
        try:
            from agent.tasks.manager import TaskType

            t = await self._tasks.create_task(
                name=f"initiative:{initiative_id}:{step.title[:60]}",
                description=step.prompt[:1000],
                task_type=TaskType.CRON,
                cron_expression=cron,
                tags=["initiative", initiative_id],
                metadata={
                    "initiative_id": initiative_id,
                    "step_idx": step.idx,
                    "target_module": target_module,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return StepExecutionResult(
                success=False,
                error=f"Schedule failed: {exc}"[:1900],
            )
        return StepExecutionResult(
            success=True,
            summary=f"Scheduled cron task `{t.id}` (cron: `{cron}`).",
            metadata={"cron_task_id": t.id, "cron_expression": cron},
        )

    async def _handle_notify(
        self,
        *,
        initiative_id: str,
        step: PlannedStep,
        owner_chat_id: int,
    ) -> StepExecutionResult:
        if not self._bot or not owner_chat_id:
            return StepExecutionResult(
                success=False,
                error="Telegram bot alebo chat_id nie sú dostupné.",
            )
        text = step.metadata.get("text") or step.prompt[:3500]
        try:
            await self._bot.send_message(owner_chat_id, text)
        except Exception as exc:  # noqa: BLE001
            return StepExecutionResult(
                success=False,
                error=f"Telegram send failed: {exc}"[:1900],
            )
        return StepExecutionResult(
            success=True,
            summary=f"Notifikácia poslaná na chat {owner_chat_id} ({len(text)} znakov).",
        )

    async def _handle_approval(
        self,
        *,
        initiative_id: str,
        initiative_title: str,
        step: PlannedStep,
        owner_chat_id: int,
    ) -> StepExecutionResult:
        # Krok zostane "pending" — driver detekuje approved/denied a re-spustí
        if not self._approvals:
            # Bez approval queue: požiadaj cez Telegram a čakaj na manuálne resume
            if self._bot and owner_chat_id:
                try:
                    await self._bot.send_message(
                        owner_chat_id,
                        (
                            f"🔐 *Iniciatíva `{initiative_title}` čaká na schválenie*\n\n"
                            f"Krok: {step.title}\nDetail: {step.prompt[:1500]}\n\n"
                            "Odpovedz `/initiative resume <id>` po schválení alebo "
                            "`/initiative cancel <id>`."
                        ),
                    )
                except Exception:  # noqa: BLE001
                    pass
            return StepExecutionResult(
                success=False,
                summary="Čaká na manuálne schválenie majiteľa.",
                metadata={"awaiting_approval": True},
            )
        # ApprovalQueue dostupná — vytvor request
        try:
            req_id = await self._approvals.propose(
                action_type="initiative_step",
                rationale=step.prompt[:1500],
                metadata={
                    "initiative_id": initiative_id,
                    "step_idx": step.idx,
                    "step_kind": step.kind.value,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return StepExecutionResult(
                success=False,
                error=f"Approval propose failed: {exc}"[:1900],
            )
        return StepExecutionResult(
            success=False,
            summary=f"Schvaľovacia požiadavka vytvorená: `{req_id}`.",
            metadata={"awaiting_approval": True, "approval_id": str(req_id)},
        )
