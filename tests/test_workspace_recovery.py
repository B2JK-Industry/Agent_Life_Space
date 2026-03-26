"""
Tests for workspace recovery after process crash.
"""

from __future__ import annotations

from agent.work.workspace import WorkspaceManager, WorkspaceStatus


class TestWorkspaceRecovery:
    """Workspace state recovers after simulated crash."""

    def test_active_workspace_survives_restart(self, tmp_path):
        root = str(tmp_path / "ws")
        db = str(tmp_path / "ws" / "workspaces.db")

        # Session 1: create + activate
        m1 = WorkspaceManager(root=root, db_path=db)
        m1.initialize()
        ws = m1.create("crash-test")
        m1.activate(ws.id)
        m1.record_command(ws.id, "make build")
        ws_id = ws.id
        m1.close()  # Simulate crash

        # Session 2: recover
        m2 = WorkspaceManager(root=root, db_path=db)
        m2.initialize()
        recovered = m2.get(ws_id)

        assert recovered is not None
        assert recovered.status == WorkspaceStatus.ACTIVE
        assert recovered.name == "crash-test"
        assert "make build" in recovered.commands_run
        m2.close()

    def test_completed_workspace_survives(self, tmp_path):
        root = str(tmp_path / "ws")
        db = str(tmp_path / "ws" / "workspaces.db")

        m1 = WorkspaceManager(root=root, db_path=db)
        m1.initialize()
        ws = m1.create("done-test")
        m1.activate(ws.id)
        m1.complete(ws.id, output="all good")
        ws_id = ws.id
        m1.close()

        m2 = WorkspaceManager(root=root, db_path=db)
        m2.initialize()
        recovered = m2.get(ws_id)
        assert recovered.status == WorkspaceStatus.COMPLETED
        assert recovered.output == "all good"
        m2.close()

    def test_multiple_workspaces_recover(self, tmp_path):
        root = str(tmp_path / "ws")
        db = str(tmp_path / "ws" / "workspaces.db")

        m1 = WorkspaceManager(root=root, db_path=db)
        m1.initialize()
        ws1 = m1.create("ws1")
        ws2 = m1.create("ws2")
        m1.activate(ws1.id)
        m1.activate(ws2.id)
        m1.complete(ws1.id)
        m1.close()

        m2 = WorkspaceManager(root=root, db_path=db)
        m2.initialize()
        assert m2.get(ws1.id).status == WorkspaceStatus.COMPLETED
        assert m2.get(ws2.id).status == WorkspaceStatus.ACTIVE
        m2.close()

    def test_audit_trail_survives(self, tmp_path):
        root = str(tmp_path / "ws")
        db = str(tmp_path / "ws" / "workspaces.db")

        m1 = WorkspaceManager(root=root, db_path=db)
        m1.initialize()
        ws = m1.create("audit-test")
        m1.activate(ws.id)
        m1.record_command(ws.id, "cmd1")
        m1.record_command(ws.id, "cmd2")
        ws_id = ws.id
        m1.close()

        m2 = WorkspaceManager(root=root, db_path=db)
        m2.initialize()
        trail = m2.get_audit_trail(ws_id)
        assert len(trail) >= 3  # created + activated + 2 commands
        m2.close()
