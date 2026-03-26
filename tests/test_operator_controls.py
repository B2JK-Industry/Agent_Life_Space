"""
Tests for operator controls — runtime capability toggles.
"""

from __future__ import annotations

from agent.core.operator import OperatorControls


class TestOperatorControls:
    """Owner can disable/enable tools at runtime."""

    def test_disable_tool(self):
        controls = OperatorControls()
        controls.disable("run_code", reason="sandbox broken")
        assert controls.is_disabled("run_code")
        assert controls.get_disabled_reason("run_code") == "sandbox broken"

    def test_enable_tool(self):
        controls = OperatorControls()
        controls.disable("run_code")
        controls.enable("run_code", reason="fixed")
        assert not controls.is_disabled("run_code")

    def test_not_disabled_by_default(self):
        controls = OperatorControls()
        assert not controls.is_disabled("run_code")
        assert controls.get_disabled_reason("run_code") == ""

    def test_lockdown(self):
        controls = OperatorControls()
        controls.lockdown(reason="incident")
        # External tools should be disabled
        assert controls.is_disabled("run_code")
        assert controls.is_disabled("run_tests")
        assert controls.is_disabled("web_fetch")
        # Internal-only tools should still work
        assert not controls.is_disabled("store_memory")
        assert not controls.is_disabled("query_memory")

    def test_unlock(self):
        controls = OperatorControls()
        controls.lockdown()
        controls.unlock(reason="all clear")
        assert not controls.is_disabled("run_code")
        assert not controls.is_disabled("web_fetch")

    def test_status(self):
        controls = OperatorControls()
        controls.disable("run_code", reason="test")
        status = controls.get_status()
        assert status["total_disabled"] == 1
        assert "run_code" in status["disabled_tools"]

    def test_history(self):
        controls = OperatorControls()
        controls.disable("run_code", reason="broken")
        controls.enable("run_code", reason="fixed")
        history = controls.get_history()
        assert len(history) == 2
        assert history[0]["action"] == "disabled"
        assert history[1]["action"] == "enabled"

    def test_lockdown_status_shows_in_lockdown(self):
        controls = OperatorControls()
        controls.lockdown()
        status = controls.get_status()
        assert status["in_lockdown"]

    def test_multiple_disables(self):
        controls = OperatorControls()
        controls.disable("run_code", reason="reason 1")
        controls.disable("web_fetch", reason="reason 2")
        assert controls.is_disabled("run_code")
        assert controls.is_disabled("web_fetch")
        assert not controls.is_disabled("check_health")
