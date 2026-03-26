"""
Tests for workspace persistence and recovery.
"""

from __future__ import annotations

import pytest

from agent.work.workspace import WorkspaceManager, WorkspaceStatus


class TestWorkspacePersistence:
    """Workspace state survives restart."""

    @pytest.fixture
    def manager(self, tmp_path):
        m = WorkspaceManager(root=str(tmp_path / "workspaces"))
        m.initialize()
        yield m
        m.close()

    def test_create_persists(self, manager):
        ws = manager.create("test-ws", project_id="proj1")
        assert ws.id
        assert ws.status == WorkspaceStatus.CREATED
        assert ws.name == "test-ws"

    def test_recovery_after_restart(self, tmp_path):
        root = str(tmp_path / "workspaces")
        db_path = str(tmp_path / "workspaces" / "workspaces.db")

        # Create + activate
        m1 = WorkspaceManager(root=root, db_path=db_path)
        m1.initialize()
        ws = m1.create("recover-me")
        ws_id = ws.id
        m1.activate(ws_id)
        m1.record_command(ws_id, "echo hello")
        m1.record_file(ws_id, "output.txt")
        m1.close()

        # New manager — should recover
        m2 = WorkspaceManager(root=root, db_path=db_path)
        m2.initialize()
        recovered = m2.get(ws_id)
        assert recovered is not None
        assert recovered.name == "recover-me"
        assert recovered.status == WorkspaceStatus.ACTIVE
        assert "echo hello" in recovered.commands_run
        assert "output.txt" in recovered.files_created
        m2.close()

    def test_audit_trail(self, manager):
        ws = manager.create("audited")
        manager.activate(ws.id)
        manager.record_command(ws.id, "make build")
        manager.record_command(ws.id, "make test")
        manager.complete(ws.id, output="all passed")

        trail = manager.get_audit_trail(ws.id)
        assert len(trail) >= 4  # created, activated, 2 commands, completed
        event_types = [e["event_type"] for e in trail]
        assert "lifecycle" in event_types
        assert "command" in event_types

    def test_lifecycle_transitions(self, manager):
        ws = manager.create("lifecycle-test")
        assert ws.status == WorkspaceStatus.CREATED

        manager.activate(ws.id)
        assert manager.get(ws.id).status == WorkspaceStatus.ACTIVE

        manager.complete(ws.id, output="done")
        assert manager.get(ws.id).status == WorkspaceStatus.COMPLETED

    def test_fail_records_error(self, manager):
        ws = manager.create("fail-test")
        manager.activate(ws.id)
        manager.fail(ws.id, error="segfault")

        failed = manager.get(ws.id)
        assert failed.status == WorkspaceStatus.FAILED
        assert failed.error == "segfault"

    def test_cleanup_only_after_complete_or_fail(self, manager):
        ws = manager.create("no-cleanup")
        manager.activate(ws.id)

        # Can't cleanup active workspace
        assert not manager.cleanup(ws.id)

        manager.complete(ws.id)
        assert manager.cleanup(ws.id)
        assert manager.get(ws.id).status == WorkspaceStatus.CLEANED

    def test_stats(self, manager):
        manager.create("ws1")
        ws2 = manager.create("ws2")
        manager.activate(ws2.id)

        stats = manager.get_stats()
        assert stats["total"] == 2
        assert stats["active"] == "ws2"
