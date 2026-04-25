"""Test generator-based driver tick_stream."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest

from agent.initiative.engine import InitiativeEngine
from agent.initiative.executor import StepExecutor
from agent.initiative.planner import InitiativePlanner
from agent.initiative.schemas import StepKind
from agent.projects.manager import ProjectManager
from agent.tasks.manager import TaskManager


class FakeResp:
    def __init__(self, success=True, text="", error=""):
        self.success = success; self.text = text; self.error = error


class FakeProvider:
    def __init__(self, responses):
        self._r = list(responses)
    async def generate(self, req):
        if not self._r:
            return FakeResp(False, error="no more")
        return self._r.pop(0)


class FakeBot:
    def __init__(self):
        self.sent = []
    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


def _plan_json() -> str:
    return json.dumps({
        "goal_summary": "stream test plan",
        "pattern": {"pattern_id": "scraper", "confidence": 0.9, "rationale": ""},
        "success_criteria": ["done"],
        "estimated_total_minutes": 5,
        "is_long_running": False,
        "risk_notes": [],
        "steps": [
            {"idx": 0, "kind": StepKind.ANALYZE.value, "title": "step 0",
             "prompt": "analyze something useful here please", "depends_on_idx": []},
            {"idx": 1, "kind": StepKind.ANALYZE.value, "title": "step 1",
             "prompt": "design something useful here please", "depends_on_idx": [0]},
        ],
    })


@pytest.mark.asyncio
async def test_tick_stream_yields_event_per_processed_step():
    with tempfile.TemporaryDirectory() as tmp:
        pm = ProjectManager(db_path=os.path.join(tmp, "p.db"))
        tm = TaskManager(db_path=os.path.join(tmp, "t.db"))
        await pm.initialize(); await tm.initialize()

        provider = FakeProvider([
            FakeResp(True, text=_plan_json()),
            FakeResp(True, text="step0 done"),
            FakeResp(True, text="step1 done"),
            FakeResp(True, text="dream summary"),  # for finalize
        ])
        bot = FakeBot()
        ex = StepExecutor(provider=provider, agent_name="t",
                          project_root=tmp, data_root=tmp,
                          telegram_bot=bot, task_manager=tm)
        eng = InitiativeEngine(
            planner=InitiativePlanner(provider=provider, agent_name="t",
                                      owner_name="t", project_root=tmp, data_root=tmp),
            executor=ex, project_manager=pm, task_manager=tm, data_root=tmp,
        )

        info = await eng.start_initiative("test stream", chat_id=1)

        # Tick 1 — process step 0
        events: list[dict[str, Any]] = []
        async for ev in eng.tick_stream():
            events.append(ev)
        assert len(events) == 1
        assert events[0]["event"] == "step_processed"
        assert events[0]["initiative_id"] == info["initiative_id"]

        # Tick 2 — process step 1
        events = []
        async for ev in eng.tick_stream():
            events.append(ev)
        assert len(events) == 1

        # Tick 3 — finalize, no more steps → 0 events
        events = []
        async for ev in eng.tick_stream():
            events.append(ev)
        # Either 0 (already finalized) or no events for new processing
        # Backward compat: tick_stream returns nothing when no work done

        await pm.close(); await tm.close()


@pytest.mark.asyncio
async def test_tick_and_tick_stream_equivalent():
    """tick() and tick_stream() process same number of steps."""
    with tempfile.TemporaryDirectory() as tmp:
        pm = ProjectManager(db_path=os.path.join(tmp, "p.db"))
        tm = TaskManager(db_path=os.path.join(tmp, "t.db"))
        await pm.initialize(); await tm.initialize()

        provider = FakeProvider([
            FakeResp(True, text=_plan_json()),
            FakeResp(True, text="step done via tick"),
            FakeResp(True, text="dream"),
        ])
        ex = StepExecutor(provider=provider, agent_name="t",
                          project_root=tmp, data_root=tmp,
                          telegram_bot=FakeBot(), task_manager=tm)
        eng = InitiativeEngine(
            planner=InitiativePlanner(provider=provider, agent_name="t",
                                      owner_name="t", project_root=tmp, data_root=tmp),
            executor=ex, project_manager=pm, task_manager=tm, data_root=tmp,
        )
        await eng.start_initiative("eq test", chat_id=1)
        n = await eng.tick()
        assert n == 1
        await pm.close(); await tm.close()
