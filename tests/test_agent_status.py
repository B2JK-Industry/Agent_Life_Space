"""
Tests for agent status model — state machine with audit trail.
"""

from __future__ import annotations

from agent.core.status import AgentState, AgentStatusModel


class TestAgentStatusModel:
    """Agent status tracks state with history."""

    def test_initial_state_is_idle(self):
        model = AgentStatusModel()
        assert model.state == AgentState.IDLE

    def test_transition(self):
        model = AgentStatusModel()
        model.transition(AgentState.THINKING, "processing message")
        assert model.state == AgentState.THINKING

    def test_transition_records_history(self):
        model = AgentStatusModel()
        model.transition(AgentState.THINKING, "msg 1")
        model.transition(AgentState.EXECUTING, "tool call")
        model.transition(AgentState.IDLE, "done")

        history = model.get_history()
        assert len(history) == 3
        assert history[0]["from"] == "idle"
        assert history[0]["to"] == "thinking"
        assert history[2]["to"] == "idle"

    def test_same_state_no_op(self):
        model = AgentStatusModel()
        model.transition(AgentState.IDLE, "still idle")
        assert len(model.get_history()) == 0

    def test_blocked_reason(self):
        model = AgentStatusModel()
        model.transition(AgentState.BLOCKED, "database unreachable")
        status = model.get_status()
        assert status["state"] == "blocked"
        assert status["blocked_reason"] == "database unreachable"

    def test_degraded_modules(self):
        model = AgentStatusModel()
        model.mark_degraded("memory", "SQLite locked")
        assert model.state == AgentState.DEGRADED
        assert "memory" in model.get_status()["degraded_modules"]

    def test_clear_degraded_recovers(self):
        model = AgentStatusModel()
        model.mark_degraded("memory")
        model.clear_degraded("memory")
        assert model.state == AgentState.IDLE
        assert model.get_status()["degraded_modules"] == []

    def test_multiple_degraded_modules(self):
        model = AgentStatusModel()
        model.mark_degraded("memory")
        model.mark_degraded("tasks")
        # Clearing one doesn't recover
        model.clear_degraded("memory")
        assert model.state == AgentState.DEGRADED
        # Clearing both recovers
        model.clear_degraded("tasks")
        assert model.state == AgentState.IDLE

    def test_status_has_duration(self):
        model = AgentStatusModel()
        status = model.get_status()
        assert "state_duration_s" in status
        assert status["state_duration_s"] >= 0

    def test_history_ring_buffer(self):
        model = AgentStatusModel(max_history=3)
        for i in range(5):
            state = AgentState.THINKING if i % 2 == 0 else AgentState.IDLE
            model.transition(state, f"step {i}")

        history = model.get_history()
        assert len(history) == 3

    def test_waiting_approval_state(self):
        model = AgentStatusModel()
        model.transition(AgentState.WAITING_APPROVAL, "finance proposal pending")
        assert model.state == AgentState.WAITING_APPROVAL
