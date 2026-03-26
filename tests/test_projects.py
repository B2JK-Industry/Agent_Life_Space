"""
Tests pre agent/projects/manager.py a agent/work/workspace.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.projects.manager import Project, ProjectManager, ProjectStatus
from agent.work.workspace import Workspace, WorkspaceManager, WorkspaceStatus

# --- Project ---


class TestProject:
    def test_defaults(self):
        p = Project(name="test")
        assert p.status == ProjectStatus.IDEA
        assert p.task_ids == []
        assert p.priority == 0.5

    def test_to_from_dict(self):
        p = Project(name="test", tags=["a", "b"], priority=0.8)
        d = p.to_dict()
        p2 = Project.from_dict(d)
        assert p2.name == "test"
        assert p2.tags == ["a", "b"]
        assert p2.priority == 0.8


class TestProjectManager:
    @pytest.mark.asyncio
    async def test_create_and_get(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        p = await pm.create(name="Test Project", tags=["test"])
        assert p.name == "Test Project"
        assert p.status == ProjectStatus.IDEA

        fetched = await pm.get(p.id)
        assert fetched is not None
        assert fetched.name == "Test Project"

        await pm.close()

    @pytest.mark.asyncio
    async def test_start_and_complete(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        p = await pm.create(name="P1")
        await pm.start(p.id)
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.ACTIVE
        assert p.started_at is not None

        await pm.complete(p.id, result="done")
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.COMPLETED
        assert p.result == "done"

        await pm.close()

    @pytest.mark.asyncio
    async def test_add_task(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        p = await pm.create(name="P1")
        await pm.add_task(p.id, "task_123")
        await pm.add_task(p.id, "task_456")
        await pm.add_task(p.id, "task_123")  # Duplicit — ignorovať

        p = await pm.get(p.id)
        assert p.task_ids == ["task_123", "task_456"]

        await pm.close()

    @pytest.mark.asyncio
    async def test_list_by_status(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        await pm.create(name="P1")
        p2 = await pm.create(name="P2")
        await pm.start(p2.id)

        ideas = await pm.list_projects(status=ProjectStatus.IDEA)
        active = await pm.list_projects(status=ProjectStatus.ACTIVE)
        assert len(ideas) == 1
        assert len(active) == 1

        await pm.close()

    @pytest.mark.asyncio
    async def test_abandon(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        p = await pm.create(name="P1")
        await pm.abandon(p.id, reason="too complex")
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.ABANDONED
        assert p.notes == "too complex"

        await pm.close()

    @pytest.mark.asyncio
    async def test_stats(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        await pm.create(name="P1")
        p2 = await pm.create(name="P2")
        await pm.start(p2.id)

        stats = await pm.get_stats()
        assert stats["total_projects"] == 2
        assert "P2" in stats["active"]

        await pm.close()


# --- Workspace ---


class TestWorkspace:
    def test_defaults(self):
        ws = Workspace(name="test")
        assert ws.status == WorkspaceStatus.CREATED
        assert ws.commands_run == []
        assert ws.files_created == []

    def test_to_from_dict(self):
        ws = Workspace(name="test", project_id="p1", task_id="t1")
        d = ws.to_dict()
        ws2 = Workspace.from_dict(d)
        assert ws2.name == "test"
        assert ws2.project_id == "p1"


class TestWorkspaceManager:
    def test_create(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        ws = wm.create(name="test-job")
        assert ws.status == WorkspaceStatus.CREATED
        assert Path(ws.path).exists()

    def test_activate_and_complete(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        ws = wm.create(name="job1")
        wm.activate(ws.id)
        assert wm.get(ws.id).status == WorkspaceStatus.ACTIVE

        wm.complete(ws.id, output="result")
        assert wm.get(ws.id).status == WorkspaceStatus.COMPLETED
        assert wm.get(ws.id).output == "result"

    def test_record_command_and_file(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        ws = wm.create(name="job1")
        wm.record_command(ws.id, "pytest tests/")
        wm.record_command(ws.id, "git status")
        wm.record_file(ws.id, "output.txt")

        ws = wm.get(ws.id)
        assert len(ws.commands_run) == 2
        assert ws.files_created == ["output.txt"]

    def test_cleanup(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        ws = wm.create(name="job1")
        ws_path = Path(ws.path)
        assert ws_path.exists()

        # Can't cleanup before complete
        assert wm.cleanup(ws.id) is False

        wm.complete(ws.id)
        assert wm.cleanup(ws.id) is True
        assert not ws_path.exists()
        assert wm.get(ws.id).status == WorkspaceStatus.CLEANED

    def test_get_active(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        assert wm.get_active() is None

        ws = wm.create(name="job1")
        wm.activate(ws.id)
        assert wm.get_active().name == "job1"

    def test_stats(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        wm.create(name="j1")
        ws2 = wm.create(name="j2")
        wm.activate(ws2.id)

        stats = wm.get_stats()
        assert stats["total"] == 2
        assert stats["active"] == "j2"
