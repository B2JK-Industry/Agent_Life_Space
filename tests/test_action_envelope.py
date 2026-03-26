"""
Tests for ActionEnvelope — 4-step tool execution pipeline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.action import ActionEnvelope, ActionLog, ActionPhase
from agent.core.tool_policy import ToolExecutionContext


class TestActionEnvelope:
    """ActionEnvelope captures full lifecycle of a tool call."""

    def test_default_phase_is_requested(self):
        action = ActionEnvelope(tool_name="check_health")
        assert action.phase == ActionPhase.REQUESTED
        assert action.id
        assert action.requested_at > 0

    def test_to_audit_record(self):
        action = ActionEnvelope(
            tool_name="run_code",
            is_owner=True,
            safe_mode=False,
        )
        record = action.to_audit_record()
        assert record["tool"] == "run_code"
        assert record["phase"] == "requested"
        assert record["is_owner"] is True
        assert "id" in record

    def test_phases_are_valid_strings(self):
        for phase in ActionPhase:
            assert isinstance(phase.value, str)


class TestActionLog:
    """ActionLog is a bounded ring buffer of actions."""

    def test_record_and_retrieve(self):
        log = ActionLog()
        action = ActionEnvelope(tool_name="check_health")
        action.phase = ActionPhase.COMPLETED
        log.record(action)

        assert log.total == 1
        recent = log.get_recent(10)
        assert len(recent) == 1
        assert recent[0]["tool"] == "check_health"

    def test_ring_buffer(self):
        log = ActionLog(max_entries=3)
        for i in range(5):
            a = ActionEnvelope(tool_name=f"tool_{i}")
            a.phase = ActionPhase.COMPLETED
            log.record(a)

        assert log.total == 3
        tools = [r["tool"] for r in log.get_recent(10)]
        assert tools == ["tool_2", "tool_3", "tool_4"]

    def test_blocked_filter(self):
        log = ActionLog()
        ok = ActionEnvelope(tool_name="check_health")
        ok.phase = ActionPhase.COMPLETED
        log.record(ok)

        blocked = ActionEnvelope(tool_name="run_code")
        blocked.phase = ActionPhase.BLOCKED
        log.record(blocked)

        assert log.total_blocked == 1
        assert len(log.get_blocked()) == 1
        assert log.get_blocked()[0]["tool"] == "run_code"

    def test_failed_filter(self):
        log = ActionLog()
        failed = ActionEnvelope(tool_name="web_fetch")
        failed.phase = ActionPhase.FAILED
        failed.error = "timeout"
        log.record(failed)

        assert log.total_failed == 1
        assert log.get_failed()[0]["error"] == "timeout"

    def test_stats(self):
        log = ActionLog()
        for phase in [ActionPhase.COMPLETED, ActionPhase.COMPLETED, ActionPhase.BLOCKED]:
            a = ActionEnvelope(tool_name="check_health")
            a.phase = phase
            log.record(a)

        stats = log.get_stats()
        assert stats["total"] == 3
        assert stats["blocked"] == 1
        assert stats["by_phase"]["completed"] == 2


class TestExecutorPipeline:
    """ToolExecutor uses 4-step pipeline with ActionEnvelope."""

    @pytest.fixture
    def executor(self):
        from agent.core.tool_executor import ToolExecutor

        agent = MagicMock()
        agent.memory = AsyncMock()
        agent.memory.store = AsyncMock(return_value="mem_123")
        agent.memory.query = AsyncMock(return_value=[])
        agent.tasks = AsyncMock()
        agent.tasks.create_task = AsyncMock(return_value=MagicMock(id="task_1", name="test"))
        agent.tasks.get_stats = MagicMock(return_value={"total_tasks": 0})
        agent.watchdog = MagicMock()
        agent.watchdog.get_system_health = MagicMock(return_value=MagicMock(
            cpu_percent=25.0, memory_percent=40.0, disk_percent=50.0,
            modules={"brain": "healthy"}, alerts=[],
        ))
        agent.get_status = MagicMock(return_value={"running": True})
        sandbox = AsyncMock()

        return ToolExecutor(agent=agent, sandbox=sandbox)

    @pytest.mark.asyncio
    async def test_successful_execution_records_all_phases(self, executor):
        await executor.execute("check_health", {})

        log = executor.action_log
        assert log.total == 1
        record = log.get_recent(1)[0]
        assert record["phase"] == "completed"
        assert record["policy_allowed"] is True
        assert record["duration_ms"] >= 0
        assert record["tool"] == "check_health"

    @pytest.mark.asyncio
    async def test_blocked_execution_records_block(self, executor):
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        await executor.execute("run_code", {"code": "print('hi')"}, context=ctx)

        log = executor.action_log
        assert log.total == 1
        record = log.get_recent(1)[0]
        assert record["phase"] == "blocked"
        assert record["policy_allowed"] is False
        assert record["safe_mode"] is True

    @pytest.mark.asyncio
    async def test_unknown_tool_records_blocked(self, executor):
        """Unknown tools are denied by policy (deny-by-default), recorded as blocked."""
        await executor.execute("nonexistent_tool", {})

        log = executor.action_log
        assert log.total == 1
        record = log.get_recent(1)[0]
        assert record["phase"] == "blocked"

    @pytest.mark.asyncio
    async def test_multiple_calls_build_action_log(self, executor):
        await executor.execute("check_health", {})
        await executor.execute("get_status", {})
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        await executor.execute("run_code", {"code": "x"}, context=ctx)

        log = executor.action_log
        assert log.total == 3
        stats = log.get_stats()
        assert stats["by_phase"]["completed"] == 2
        assert stats["by_phase"]["blocked"] == 1

    @pytest.mark.asyncio
    async def test_action_has_unique_id(self, executor):
        await executor.execute("check_health", {})
        await executor.execute("check_health", {})

        records = executor.action_log.get_recent(10)
        ids = [r["id"] for r in records]
        assert len(set(ids)) == 2  # Unique IDs
