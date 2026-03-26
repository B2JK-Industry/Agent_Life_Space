"""
Tests for workspace limits, TTL, and auto-cleanup.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent.work.workspace import WorkspaceManager, WorkspaceStatus


class TestWorkspaceLimits:
    """Max active workspace limit is enforced."""

    @pytest.fixture
    def manager(self, tmp_path):
        m = WorkspaceManager(root=str(tmp_path / "ws"), max_active=2)
        m.initialize()
        yield m
        m.close()

    def test_create_within_limit(self, manager):
        ws1 = manager.create("ws1")
        manager.activate(ws1.id)
        ws2 = manager.create("ws2")
        manager.activate(ws2.id)
        # 2 active = at limit, but create before activate is ok
        assert manager.get(ws1.id).status == WorkspaceStatus.ACTIVE
        assert manager.get(ws2.id).status == WorkspaceStatus.ACTIVE

    def test_create_over_limit_raises(self, manager):
        ws1 = manager.create("ws1")
        manager.activate(ws1.id)
        ws2 = manager.create("ws2")
        manager.activate(ws2.id)
        # 3rd workspace when 2 are active should raise
        with pytest.raises(RuntimeError, match="max 2"):
            manager.create("ws3")

    def test_completing_frees_slot(self, manager):
        ws1 = manager.create("ws1")
        manager.activate(ws1.id)
        ws2 = manager.create("ws2")
        manager.activate(ws2.id)

        # Complete one → frees slot
        manager.complete(ws1.id)
        ws3 = manager.create("ws3")
        assert ws3.id  # Should not raise

    def test_created_not_active_doesnt_count(self, manager):
        """CREATED workspaces don't count toward active limit."""
        manager.create("ws1")  # CREATED, not ACTIVE
        manager.create("ws2")  # CREATED, not ACTIVE
        ws3 = manager.create("ws3")  # Should succeed
        assert ws3.id


class TestWorkspaceTTL:
    """Expired workspaces are auto-cleaned."""

    @pytest.fixture
    def manager(self, tmp_path):
        m = WorkspaceManager(root=str(tmp_path / "ws"), ttl_hours=1)
        m.initialize()
        yield m
        m.close()

    def test_cleanup_expired(self, manager):
        ws = manager.create("old-ws")
        manager.activate(ws.id)
        manager.complete(ws.id)

        # Hack: set completed_at to 2 hours ago
        old_ws = manager.get(ws.id)
        old_ws.completed_at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        manager._persist(old_ws)

        cleaned = manager.cleanup_expired()
        assert cleaned == 1
        assert manager.get(ws.id).status == WorkspaceStatus.CLEANED

    def test_no_cleanup_within_ttl(self, manager):
        ws = manager.create("fresh-ws")
        manager.activate(ws.id)
        manager.complete(ws.id)
        # Just completed — should not be cleaned
        cleaned = manager.cleanup_expired()
        assert cleaned == 0

    def test_active_not_cleaned(self, manager):
        ws = manager.create("active-ws")
        manager.activate(ws.id)
        cleaned = manager.cleanup_expired()
        assert cleaned == 0

    def test_failed_gets_cleaned(self, manager):
        ws = manager.create("failed-ws")
        manager.activate(ws.id)
        manager.fail(ws.id, error="boom")

        # Hack: set completed_at to past TTL
        failed_ws = manager.get(ws.id)
        failed_ws.completed_at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        manager._persist(failed_ws)

        cleaned = manager.cleanup_expired()
        assert cleaned == 1
