"""Tests for TaskManager.refresh_from_db and InitiativeEngine.pause cancels cron."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from typing import Any

import pytest

from agent.tasks.manager import TaskManager, TaskStatus, TaskType


@pytest.mark.asyncio
async def test_refresh_picks_up_external_task():
    """Task added by another process via direct SQL must be visible after refresh."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "tasks.db")
        tm = TaskManager(db_path=path)
        await tm.initialize()
        assert len(tm._tasks) == 0

        # External writer (simulujem direct SQL write — žiadny TaskManager API)
        c = sqlite3.connect(path)
        external_task = {
            "id": "external_x1",
            "name": "external task",
            "description": "added externally",
            "status": "queued",
            "task_type": "one_time",
            "priority": 0.5,
            "importance": 0.5,
            "urgency": 0.5,
            "effort": 0.5,
            "tags": [],
            "dependencies": [],
            "created_at": "2026-04-25T06:00:00+00:00",
            "scheduled_at": None,
            "started_at": None,
            "completed_at": None,
            "deadline": None,
            "result": None,
            "error": None,
            "requires_llm": False,
            "requires_approval": False,
            "metadata": {},
            "cron_expression": None,
            "recurrence_count": 0,
        }
        c.execute(
            "INSERT INTO tasks (id, data) VALUES (?, ?)",
            ("external_x1", json.dumps(external_task)),
        )
        c.commit()
        c.close()

        # Daemon's in-memory cache nevie nič
        assert tm.get_task("external_x1") is None

        # Refresh
        loaded = await tm.refresh_from_db()
        assert loaded == 1
        # Now visible
        t = tm.get_task("external_x1")
        assert t is not None
        assert t.name == "external task"

        await tm.close()


@pytest.mark.asyncio
async def test_refresh_drops_externally_deleted():
    """Task removed from DB by another process disappears after refresh."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "tasks.db")
        tm = TaskManager(db_path=path)
        await tm.initialize()
        t = await tm.create_task(name="will be deleted")
        assert tm.get_task(t.id) is not None

        # External delete
        c = sqlite3.connect(path)
        c.execute("DELETE FROM tasks WHERE id = ?", (t.id,))
        c.commit()
        c.close()

        await tm.refresh_from_db()
        assert tm.get_task(t.id) is None
        await tm.close()


@pytest.mark.asyncio
async def test_initiative_pause_cancels_cron_tasks():
    """engine.pause() must cancel cron tasks linked to that initiative."""
    from datetime import UTC, datetime
    from pathlib import Path

    from agent.initiative.engine import InitiativeEngine
    from agent.initiative.executor import StepExecutor
    from agent.initiative.planner import InitiativePlanner
    from agent.projects.manager import ProjectManager

    class FakeProvider:
        async def generate(self, req: Any) -> Any:
            class R: success = True; text = ""; error = ""
            return R()

    with tempfile.TemporaryDirectory() as tmp:
        pm = ProjectManager(db_path=os.path.join(tmp, "p.db"))
        tm = TaskManager(db_path=os.path.join(tmp, "t.db"))
        await pm.initialize()
        await tm.initialize()

        # Vytvor projekt + 2 cron tasks attached k iniciatíve
        project = await pm.create(name="test init", tags=["initiative"])
        cron1 = await tm.create_task(
            name="scrape every 6h",
            task_type=TaskType.CRON,
            cron_expression="0 */6 * * *",
            metadata={"initiative_id": project.id},
        )
        cron2 = await tm.create_task(
            name="daily report",
            task_type=TaskType.CRON,
            cron_expression="0 8 * * *",
            metadata={"initiative_id": project.id},
        )
        # Plus jeden cron NEPATRIACI iniciatíve
        unrelated = await tm.create_task(
            name="unrelated cron",
            task_type=TaskType.CRON,
            cron_expression="0 0 * * *",
            metadata={"initiative_id": "OTHER"},
        )

        eng = InitiativeEngine(
            planner=InitiativePlanner(
                provider=FakeProvider(), agent_name="t", owner_name="t",
                project_root=tmp, data_root=tmp,
            ),
            executor=StepExecutor(
                provider=FakeProvider(), agent_name="t",
                project_root=tmp, data_root=tmp, task_manager=tm,
            ),
            project_manager=pm, task_manager=tm, data_root=tmp,
        )

        # Pause
        ok = await eng.pause(project.id)
        assert ok is True

        # Cron tasks attached k iniciatíve cancelled
        assert tm.get_task(cron1.id).status == TaskStatus.CANCELLED
        assert tm.get_task(cron2.id).status == TaskStatus.CANCELLED
        # Unrelated cron NIE
        assert tm.get_task(unrelated.id).status != TaskStatus.CANCELLED

        await pm.close()
        await tm.close()
