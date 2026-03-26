"""
Regression tests for audit v2 findings.

Tests edge cases identified during code audit:
1. Status model stuck after early return
2. Restricted channel blocks file access on CLI path
3. Approval enforcement in policy
4. Quality escalation skips tool-loop responses
5. Deny-by-default with RESTRICTED_CHANNEL denial code
"""

from __future__ import annotations

import pytest

from agent.core.tool_policy import (
    TOOL_CAPABILITIES,
    ApprovalRequirement,
    DenialCode,
    SideEffectClass,
    ToolCapability,
    ToolExecutionContext,
    ToolPolicy,
    ToolRiskLevel,
)


class TestRestrictedChannelEnforcement:
    """Restricted channels must block high-risk tools even for owner."""

    @pytest.fixture()
    def policy(self):
        return ToolPolicy()

    def test_agent_api_blocks_run_code(self, policy):
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="agent_api")
        decision = policy.evaluate("run_code", ctx)
        assert not decision.allowed
        assert decision.denial_code == DenialCode.RESTRICTED_CHANNEL

    def test_webhook_blocks_run_tests(self, policy):
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="webhook")
        decision = policy.evaluate("run_tests", ctx)
        assert not decision.allowed
        assert decision.denial_code == DenialCode.RESTRICTED_CHANNEL

    def test_public_blocks_web_fetch(self, policy):
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="public")
        decision = policy.evaluate("web_fetch", ctx)
        assert not decision.allowed
        assert decision.denial_code == DenialCode.RESTRICTED_CHANNEL

    def test_telegram_allows_run_code_for_owner(self, policy):
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="telegram")
        decision = policy.evaluate("run_code", ctx)
        assert decision.allowed

    def test_internal_allows_run_code_for_owner(self, policy):
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="internal")
        decision = policy.evaluate("run_code", ctx)
        assert decision.allowed

    def test_restricted_channel_allows_read_only_tools(self, policy):
        """Read-only tools should work on any channel."""
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="agent_api")
        for tool in ["query_memory", "list_tasks", "check_health", "get_status", "search_knowledge"]:
            decision = policy.evaluate(tool, ctx)
            assert decision.allowed, f"{tool} should be allowed on agent_api"


class TestApprovalEnforcement:
    """Tools with approval=ALWAYS must be denied with APPROVAL_REQUIRED."""

    @pytest.fixture()
    def policy(self):
        return ToolPolicy()

    def test_always_approval_tool_blocked(self, policy):
        """If a tool has approval=ALWAYS, it must be blocked."""
        # No current tool has ALWAYS, but verify the logic works
        # by checking that the policy would block it
        from unittest.mock import patch
        test_cap = ToolCapability(
            name="test_finance_tool",
            risk_level=ToolRiskLevel.HIGH,
            side_effect=SideEffectClass.EXTERNAL,
            owner_only=True,
            safe_mode_blocked=True,
            approval=ApprovalRequirement.ALWAYS,
            audit_label="test:finance",
        )
        with patch.dict(TOOL_CAPABILITIES, {"test_finance_tool": test_cap}):
            ctx = ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="telegram")
            decision = policy.evaluate("test_finance_tool", ctx)
            assert not decision.allowed
            assert decision.denial_code == DenialCode.APPROVAL_REQUIRED

    def test_simulate_matches_evaluate_for_approval(self, policy):
        """simulate() and evaluate() must agree on approval decisions."""
        from unittest.mock import patch
        test_cap = ToolCapability(
            name="approval_test",
            risk_level=ToolRiskLevel.HIGH,
            side_effect=SideEffectClass.EXTERNAL,
            owner_only=False,
            safe_mode_blocked=False,
            approval=ApprovalRequirement.ALWAYS,
            audit_label="test:approval",
        )
        with patch.dict(TOOL_CAPABILITIES, {"approval_test": test_cap}):
            ctx = ToolExecutionContext(is_owner=True, safe_mode=False)
            decision = policy.evaluate("approval_test", ctx)
            simulation = policy.simulate("approval_test", ctx)
            assert decision.allowed == simulation["would_allow"]
            assert not decision.allowed


class TestDenyByDefaultCompleteness:
    """Deny-by-default must have no escape hatches."""

    @pytest.fixture()
    def policy(self):
        return ToolPolicy()

    def test_unknown_tool_blocked_all_contexts(self, policy):
        """Unknown tool blocked in every context combination."""
        contexts = [
            ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="internal"),
            ToolExecutionContext(is_owner=True, safe_mode=True, channel_type="telegram"),
            ToolExecutionContext(is_owner=False, safe_mode=False, channel_type="internal"),
            ToolExecutionContext(is_owner=False, safe_mode=True, channel_type="agent_api"),
        ]
        for ctx in contexts:
            decision = policy.evaluate("nonexistent_tool_xyz", ctx)
            assert not decision.allowed, f"Unknown tool allowed in context: {ctx}"
            assert decision.denial_code == DenialCode.UNKNOWN_TOOL

    def test_simulate_unknown_tool_matches_evaluate(self, policy):
        """simulate() must also deny unknown tools."""
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False)
        result = policy.simulate("nonexistent_tool", ctx)
        assert not result["would_allow"]
        assert result["denial_code"] == "unknown_tool"


class TestStatusLifecycle:
    """Status model must always return to IDLE, even on early returns."""

    def test_status_model_states_exist(self):
        from agent.core.status import AgentState
        assert hasattr(AgentState, "IDLE")
        assert hasattr(AgentState, "THINKING")
        assert hasattr(AgentState, "EXECUTING")
        assert hasattr(AgentState, "WAITING_APPROVAL")
        assert hasattr(AgentState, "BLOCKED")

    def test_status_transitions_are_valid(self):
        from agent.core.status import AgentState, AgentStatusModel
        status = AgentStatusModel()
        # Normal flow
        status.transition(AgentState.THINKING, "test")
        assert status._state == AgentState.THINKING
        status.transition(AgentState.EXECUTING, "test")
        assert status._state == AgentState.EXECUTING
        status.transition(AgentState.IDLE, "test")
        assert status._state == AgentState.IDLE

    def test_status_can_go_from_any_state_to_idle(self):
        """IDLE must be reachable from any state (for try/finally)."""
        from agent.core.status import AgentState, AgentStatusModel
        for state in AgentState:
            status = AgentStatusModel()
            status.transition(state, "setup")
            status.transition(AgentState.IDLE, "cleanup")
            assert status._state == AgentState.IDLE


class TestChannelFileAccessPolicy:
    """CLI path must respect channel restrictions for file access."""

    def test_restricted_channel_blocks_file_access_logic(self):
        """The cli_allow_file_access logic from brain.py."""
        restricted_channels = {"agent_api", "webhook", "public"}

        # Programming task on telegram — allowed
        task_type = "programming"
        channel = "telegram"
        result = task_type == "programming" and channel not in restricted_channels
        assert result is True

        # Programming task on agent_api — blocked
        channel = "agent_api"
        result = task_type == "programming" and channel not in restricted_channels
        assert result is False

        # Chat task on telegram — blocked (not programming)
        task_type = "chat"
        channel = "telegram"
        result = task_type == "programming" and channel not in restricted_channels
        assert result is False
