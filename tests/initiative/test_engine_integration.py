"""Integration test for InitiativeEngine — mocked LLM provider, real DBs.

Overuje end-to-end:
    - planner vyrobí plán z mock LLM odpovede
    - engine vytvorí Project + Tasks
    - tick() postupne spracuje kroky a označí ich completed
    - prior_outputs sa odovzdávajú do ďalších krokov
    - finalizácia označí Project ako COMPLETED
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from agent.initiative.engine import InitiativeEngine
from agent.initiative.executor import StepExecutor
from agent.initiative.planner import InitiativePlanner
from agent.initiative.schemas import StepKind
from agent.projects.manager import ProjectManager, ProjectStatus
from agent.tasks.manager import TaskManager


class FakeResponse:
    def __init__(self, success: bool, text: str = "", error: str = "") -> None:
        self.success = success
        self.text = text
        self.error = error


class FakeProvider:
    """Deterministický mock LLM — vracia naskryptované odpovede podľa poradia."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[Any] = []

    async def generate(self, request: Any) -> FakeResponse:
        self.calls.append(request)
        if not self._responses:
            return FakeResponse(False, error="no more scripted responses")
        return self._responses.pop(0)


def _plan_json() -> str:
    return json.dumps(
        {
            "goal_summary": "Test plán pre integration test enginu",
            "pattern": {
                "pattern_id": "scraper",
                "confidence": 0.85,
                "rationale": "test pattern",
            },
            "success_criteria": ["všetky kroky dokončené"],
            "estimated_total_minutes": 10,
            "is_long_running": False,
            "risk_notes": [],
            "steps": [
                {
                    "idx": 0,
                    "kind": StepKind.ANALYZE.value,
                    "title": "analyze step",
                    "prompt": "analyzuj cieľ a navrhni postup" + " ." * 5,
                    "depends_on_idx": [],
                    "estimated_minutes": 3,
                },
                {
                    "idx": 1,
                    "kind": StepKind.DESIGN.value,
                    "title": "design step",
                    "prompt": "navrhni schému dát" + " ." * 5,
                    "depends_on_idx": [0],
                    "estimated_minutes": 3,
                },
                {
                    "idx": 2,
                    "kind": StepKind.NOTIFY.value,
                    "title": "notify step",
                    "prompt": "Notifikácia: hotovo!",
                    "depends_on_idx": [1],
                    "estimated_minutes": 1,
                    "metadata": {"text": "test notif"},
                },
            ],
        }
    )


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> None:
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_engine_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        # Manuálne nakonfiguruj DBs v temp dir
        proj_db = os.path.join(tmp, "projects.db")
        task_db = os.path.join(tmp, "tasks.db")

        pm = ProjectManager(db_path=proj_db)
        tm = TaskManager(db_path=task_db)
        await pm.initialize()
        await tm.initialize()

        # 1. planner call → vráti plán
        # 2. analyze step → success
        # 3. design step → success
        # (notify step nepoužíva LLM)
        # 4. dream mode (post-finalize) → success
        responses = [
            FakeResponse(True, text=_plan_json()),
            FakeResponse(True, text="analyzed: navrhujem prístup X"),
            FakeResponse(True, text="design: schéma má polia A,B,C"),
            FakeResponse(True, text="lessons learned text"),
        ]
        provider = FakeProvider(responses)
        bot = FakeBot()

        planner = InitiativePlanner(
            provider=provider,
            agent_name="test-agent",
            owner_name="tester",
            project_root=tmp,
            data_root=tmp,
        )
        executor = StepExecutor(
            provider=provider,
            agent_name="test-agent",
            project_root=tmp,
            data_root=tmp,
            telegram_bot=bot,
            task_manager=tm,
        )
        engine = InitiativeEngine(
            planner=planner,
            executor=executor,
            project_manager=pm,
            task_manager=tm,
            data_root=tmp,
        )

        # Vytvor iniciatívu
        info = await engine.start_initiative(
            "urob mi scraper na test", chat_id=12345
        )
        assert info["steps_total"] == 3
        assert info["pattern"] == "scraper"

        # Driver tick — postupne 3 kroky
        for _ in range(5):
            ran = await engine.tick()
            if ran == 0:
                break

        # Verify projekt je COMPLETED
        proj = await pm.get(info["initiative_id"])
        assert proj is not None
        assert proj.status == ProjectStatus.COMPLETED, (
            f"expected COMPLETED, got {proj.status.value}"
        )

        # Verify všetky tasks sú completed
        for tid in proj.task_ids:
            t = tm.get_task(tid)
            assert t is not None
            assert t.status.value == "completed", (
                f"task {tid} status: {t.status.value}"
            )

        # Verify notifikácia poslaná (notify step + dream mode)
        assert len(bot.sent) >= 1
        assert any("test notif" in msg for _, msg in bot.sent), bot.sent

        # Verify plan.json a meta.json existujú
        idir = Path(tmp) / "initiatives_data" / info["initiative_id"]
        assert (idir / "plan.json").exists()
        assert (idir / "meta.json").exists()
        assert (idir / "steps").exists()
        step_files = list((idir / "steps").glob("*.json"))
        assert len(step_files) == 3

        await pm.close()
        await tm.close()


@pytest.mark.asyncio
async def test_engine_step_failure_paused_after_max_attempts():
    """Krok ktorý zlyhá MAX_STEP_ATTEMPTS-krát → projekt PAUSED, alert."""
    with tempfile.TemporaryDirectory() as tmp:
        pm = ProjectManager(db_path=os.path.join(tmp, "p.db"))
        tm = TaskManager(db_path=os.path.join(tmp, "t.db"))
        await pm.initialize()
        await tm.initialize()

        # Plán s 1 krokom
        plan = {
            "goal_summary": "single failing step",
            "pattern": {"pattern_id": "scraper", "confidence": 0.5, "rationale": ""},
            "success_criteria": ["x"],
            "estimated_total_minutes": 1,
            "is_long_running": False,
            "risk_notes": [],
            "steps": [
                {
                    "idx": 0,
                    "kind": StepKind.ANALYZE.value,
                    "title": "will fail",
                    "prompt": "this will fail repeatedly" + " ." * 5,
                    "depends_on_idx": [],
                }
            ],
        }
        responses = [
            FakeResponse(True, text=json.dumps(plan)),
            FakeResponse(False, error="LLM error 1"),
            FakeResponse(False, error="LLM error 2"),
            FakeResponse(False, error="LLM error 3"),
        ]
        provider = FakeProvider(responses)
        bot = FakeBot()

        planner = InitiativePlanner(
            provider=provider, agent_name="t", owner_name="t",
            project_root=tmp, data_root=tmp,
        )
        executor = StepExecutor(
            provider=provider, agent_name="t",
            project_root=tmp, data_root=tmp,
            telegram_bot=bot, task_manager=tm,
        )
        engine = InitiativeEngine(
            planner=planner, executor=executor,
            project_manager=pm, task_manager=tm, data_root=tmp,
        )

        info = await engine.start_initiative("fail goal", chat_id=42)
        for _ in range(5):
            await engine.tick()

        proj = await pm.get(info["initiative_id"])
        assert proj is not None
        assert proj.status == ProjectStatus.PAUSED, proj.status.value
        # Alert na Telegram
        assert any("PAUZOVANÁ" in m for _, m in bot.sent)

        await pm.close()
        await tm.close()
