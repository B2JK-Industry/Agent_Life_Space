"""
Policy regression tests + red-team scenarios.

These tests ensure security invariants hold under adversarial conditions.
If any of these fail, the security boundary has been breached.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.tool_policy import (
    TOOL_CAPABILITIES,
    DenialCode,
    ToolExecutionContext,
    ToolPolicy,
)


class TestPolicyRegression:
    """Policy decisions must never regress on security."""

    @pytest.fixture
    def policy(self):
        return ToolPolicy()

    # === Non-owner must NEVER execute high-risk tools ===

    @pytest.mark.parametrize("tool", ["run_code", "run_tests", "web_fetch", "create_task"])
    def test_non_owner_cannot_use_sensitive_tools(self, policy, tool):
        ctx = ToolExecutionContext(is_owner=False, safe_mode=False)
        decision = policy.evaluate(tool, ctx)
        assert not decision.allowed, f"Non-owner should NEVER use {tool}"

    @pytest.mark.parametrize("tool", ["run_code", "run_tests", "web_fetch", "create_task"])
    def test_safe_mode_blocks_all_sensitive_tools(self, policy, tool):
        ctx = ToolExecutionContext(is_owner=True, safe_mode=True)
        decision = policy.evaluate(tool, ctx)
        assert not decision.allowed, f"Safe mode should block {tool} even for owner"

    # === Read-only tools must ALWAYS be accessible ===

    @pytest.mark.parametrize("tool", [
        "query_memory", "list_tasks", "check_health", "get_status", "search_knowledge",
    ])
    def test_read_tools_always_allowed(self, policy, tool):
        for is_owner in (True, False):
            for safe_mode in (True, False):
                ctx = ToolExecutionContext(is_owner=is_owner, safe_mode=safe_mode)
                decision = policy.evaluate(tool, ctx)
                assert decision.allowed, (
                    f"Read-only tool {tool} should always be allowed "
                    f"(owner={is_owner}, safe_mode={safe_mode})"
                )

    # === store_memory is special — internal write but not sensitive ===

    def test_store_memory_allowed_in_safe_mode(self, policy):
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        decision = policy.evaluate("store_memory", ctx)
        assert decision.allowed, "store_memory should be allowed in safe mode"

    # === Every decision has required metadata ===

    def test_all_decisions_have_audit_label(self, policy):
        for tool_name in TOOL_CAPABILITIES:
            decision = policy.evaluate(tool_name, ToolExecutionContext())
            assert decision.audit_label, f"Tool {tool_name} decision missing audit_label"

    def test_all_decisions_have_timestamp(self, policy):
        for tool_name in TOOL_CAPABILITIES:
            decision = policy.evaluate(tool_name, ToolExecutionContext())
            assert decision.timestamp > 0, f"Tool {tool_name} decision missing timestamp"


class TestRedTeamScenarios:
    """Adversarial scenarios that test policy boundaries."""

    @pytest.fixture
    def policy(self):
        return ToolPolicy()

    def test_unknown_tool_in_safe_mode(self, policy):
        """Unknown tools must be blocked in safe mode."""
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        decision = policy.evaluate("admin_shell", ctx)
        assert not decision.allowed

    def test_unknown_tool_denied_by_default(self, policy):
        """Unknown tools denied by default — even for owner (deny-by-default)."""
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False)
        decision = policy.evaluate("custom_tool", ctx)
        assert not decision.allowed
        assert decision.denial_code == DenialCode.UNKNOWN_TOOL

    def test_privilege_escalation_attempt(self, policy):
        """Non-owner in group trying to run code — must be blocked."""
        ctx = ToolExecutionContext(
            is_owner=False,
            safe_mode=True,
            channel_type="telegram",
        )
        # Try all dangerous tools
        for tool in ["run_code", "run_tests", "web_fetch", "create_task"]:
            decision = policy.evaluate(tool, ctx)
            assert not decision.allowed, f"Privilege escalation: {tool} should be blocked"
            assert decision.reason, f"Must explain WHY {tool} is blocked"

    def test_channel_context_preserved(self, policy):
        """Policy must record channel context for audit — restricted channels block high-risk tools."""
        ctx = ToolExecutionContext(
            is_owner=True, safe_mode=False, channel_type="agent_api"
        )
        decision = policy.evaluate("run_code", ctx)
        # agent_api is restricted channel — run_code must be blocked
        assert not decision.allowed
        log = policy.audit_log.get_recent(1)
        assert log[0]["channel"] == "agent_api"

    def test_channel_context_private_allowed(self, policy):
        """Private channel (telegram) allows high-risk tools for owner."""
        ctx = ToolExecutionContext(
            is_owner=True, safe_mode=False, channel_type="telegram"
        )
        decision = policy.evaluate("run_code", ctx)
        assert decision.allowed
        log = policy.audit_log.get_recent(1)
        assert log[0]["channel"] == "telegram"

    def test_rapid_fire_all_tools_blocked(self, policy):
        """Non-owner rapid-firing all tools — nothing should pass sensitive gates."""
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        for tool_name in TOOL_CAPABILITIES:
            decision = policy.evaluate(tool_name, ctx)
            cap = TOOL_CAPABILITIES[tool_name]
            if cap.owner_only or cap.safe_mode_blocked:
                assert not decision.allowed, f"{tool_name} should be blocked"

    def test_audit_log_captures_all_attempts(self, policy):
        """Every evaluation must be logged — even allowed ones."""
        ctx = ToolExecutionContext(is_owner=True)
        for tool_name in TOOL_CAPABILITIES:
            policy.evaluate(tool_name, ctx)

        assert policy.audit_log.total_decisions == len(TOOL_CAPABILITIES)


class TestToolExecutorRedTeam:
    """Test ToolExecutor under adversarial conditions."""

    @pytest.fixture
    def executor(self):
        from agent.core.tool_executor import ToolExecutor

        agent = MagicMock()
        agent.memory = AsyncMock()
        agent.memory.store = AsyncMock(return_value="m1")
        agent.memory.query = AsyncMock(return_value=[])
        agent.tasks = AsyncMock()
        agent.tasks.get_stats = MagicMock(return_value={})
        agent.watchdog = MagicMock()
        agent.watchdog.get_system_health = MagicMock(return_value=MagicMock(
            cpu_percent=10, memory_percent=20, disk_percent=30,
            modules={}, alerts=[],
        ))
        agent.get_status = MagicMock(return_value={})
        sandbox = AsyncMock()
        return ToolExecutor(agent=agent, sandbox=sandbox)

    @pytest.mark.asyncio
    async def test_blocked_tool_never_executes_handler(self, executor):
        """Blocked tool must NOT call the handler function."""
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        result = await executor.execute("run_code", {"code": "import os; os.system('rm -rf /')"}, context=ctx)
        assert result["blocked"] is True
        # Verify sandbox was never called
        executor._sandbox.execute_code.assert_not_called()
        executor._sandbox.execute_python.assert_not_called()

    @pytest.mark.asyncio
    async def test_action_log_records_blocked_attempts(self, executor):
        """Blocked attempts must appear in action log."""
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        await executor.execute("run_code", {"code": "x"}, context=ctx)
        await executor.execute("web_fetch", {"url": "http://evil.com"}, context=ctx)

        log = executor.action_log
        blocked = log.get_blocked()
        assert len(blocked) == 2
        tools = {b["tool"] for b in blocked}
        assert tools == {"run_code", "web_fetch"}

    @pytest.mark.asyncio
    async def test_unknown_tool_does_not_crash(self, executor):
        """Unknown tool should return error, not crash."""
        result = await executor.execute("drop_database", {"confirm": True})
        assert "error" in result
        assert "Unknown tool" in result["error"]
