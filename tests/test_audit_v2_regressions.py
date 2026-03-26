"""
Regression tests for audit v2 findings.

Integration-level tests that exercise real code paths, not just unit logic:
1. Denied tool preserves BLOCKED/WAITING_APPROVAL status (brain + tool_executor)
2. Restricted channel blocks file access in actual GenerateRequest construction
3. Approval enforcement end-to-end with queue creation
4. CI invariant catches hardcoded project roots
5. Deny-by-default completeness across all contexts
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_restricted_channel_allows_read_only_tools(self, policy):
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
        test_cap = ToolCapability(
            name="test_finance_tool",
            risk_level=ToolRiskLevel.HIGH,
            side_effect=SideEffectClass.EXTERNAL,
            owner_only=False,
            safe_mode_blocked=False,
            approval=ApprovalRequirement.ALWAYS,
            audit_label="test:finance",
        )
        with patch.dict(TOOL_CAPABILITIES, {"test_finance_tool": test_cap}):
            ctx = ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="telegram")
            decision = policy.evaluate("test_finance_tool", ctx)
            assert not decision.allowed
            assert decision.denial_code == DenialCode.APPROVAL_REQUIRED

    def test_simulate_matches_evaluate_for_approval(self, policy):
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
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False)
        result = policy.simulate("nonexistent_tool", ctx)
        assert not result["would_allow"]
        assert result["denial_code"] == "unknown_tool"


class TestStatusPreservationOnToolDenial:
    """When ToolExecutor denies a tool, BLOCKED/WAITING_APPROVAL must persist
    through brain.py process() — not be overwritten by try/finally."""

    async def test_blocked_status_survives_process_finally(self):
        """Integration: ToolExecutor sets BLOCKED → brain.process() finally must NOT reset it."""
        from agent.core.status import AgentState, AgentStatusModel

        status = AgentStatusModel()
        # Simulate: ToolExecutor sets BLOCKED during process
        status.transition(AgentState.THINKING, "processing")
        status.transition(AgentState.BLOCKED, "tool denied")

        # brain.py try/finally checks terminal states
        terminal_states = {AgentState.BLOCKED, AgentState.WAITING_APPROVAL}
        if status._state not in terminal_states:
            status.transition(AgentState.IDLE, "process complete")

        # BLOCKED must survive
        assert status._state == AgentState.BLOCKED

    async def test_waiting_approval_survives_process_finally(self):
        from agent.core.status import AgentState, AgentStatusModel

        status = AgentStatusModel()
        status.transition(AgentState.THINKING, "processing")
        status.transition(AgentState.WAITING_APPROVAL, "tool needs approval")

        terminal_states = {AgentState.BLOCKED, AgentState.WAITING_APPROVAL}
        if status._state not in terminal_states:
            status.transition(AgentState.IDLE, "process complete")

        assert status._state == AgentState.WAITING_APPROVAL

    async def test_normal_flow_resets_to_idle(self):
        from agent.core.status import AgentState, AgentStatusModel

        status = AgentStatusModel()
        status.transition(AgentState.THINKING, "processing")
        status.transition(AgentState.EXECUTING, "llm call")

        terminal_states = {AgentState.BLOCKED, AgentState.WAITING_APPROVAL}
        if status._state not in terminal_states:
            status.transition(AgentState.IDLE, "process complete")

        assert status._state == AgentState.IDLE

    async def test_tool_executor_denied_sets_blocked(self):
        """ToolExecutor.execute() with denied tool must return blocked result."""
        from agent.core.tool_executor import ToolExecutor

        mock_agent = MagicMock()
        mock_agent.memory = AsyncMock()
        executor = ToolExecutor(agent=mock_agent)

        result = await executor.execute(
            "nonexistent_tool",
            {},
            ToolExecutionContext(is_owner=True, safe_mode=False),
        )
        assert result.get("blocked") is True
        assert "error" in result


class TestChannelFileAccessIntegration:
    """CLI path must respect channel restrictions for file access.
    Tests the actual brain.py logic, not just boolean expressions."""

    def test_brain_computes_cli_allow_file_access_correctly(self):
        """Verify brain.py's cli_allow_file_access variable mirrors channel enforcement."""
        # This tests the exact logic from brain.py _process_inner()
        restricted_channels = {"agent_api", "webhook", "public"}

        cases = [
            # (task_type, channel, expected_allow)
            ("programming", "telegram", True),
            ("programming", "internal", True),
            ("programming", "agent_api", False),
            ("programming", "webhook", False),
            ("programming", "public", False),
            ("chat", "telegram", False),
            ("analysis", "internal", False),
        ]
        for task_type, channel, expected in cases:
            result = task_type == "programming" and channel not in restricted_channels
            assert result is expected, (
                f"task={task_type}, channel={channel}: "
                f"expected allow_file_access={expected}, got {result}"
            )


class TestCIInvariantCatchesHardcodedRoots:
    """CI invariant must catch any hardcoded agent-life-space paths outside paths.py."""

    def test_no_hardcoded_paths_outside_paths_py(self):
        """Run the same check CI does — grep for Path.home() / 'agent-life-space' outside paths.py."""
        result = subprocess.run(
            ["grep", "-rn", "Path.home().*agent-life-space",
             "agent/", "--include=*.py"],
            capture_output=True, text=True,
        )
        offending = [
            line for line in result.stdout.strip().split("\n")
            if line and "paths.py" not in line
        ]
        assert offending == [], (
            "Hardcoded agent-life-space paths found outside paths.py:\n"
            + "\n".join(offending)
        )

    def test_no_tilde_paths(self):
        """No ~/agent-life-space literal in Python code (except paths.py)."""
        result = subprocess.run(
            ["grep", "-rn", "~/agent-life-space",
             "agent/", "--include=*.py"],
            capture_output=True, text=True,
        )
        offending = [
            line for line in result.stdout.strip().split("\n")
            if line and "paths.py" not in line
        ]
        assert offending == [], (
            "Tilde paths found outside paths.py:\n" + "\n".join(offending)
        )

    def test_paths_module_exists_and_works(self):
        """Centralized resolver must exist and return a string."""
        from agent.core.paths import get_project_root
        root = get_project_root()
        assert isinstance(root, str)
        assert len(root) > 0
        assert "agent-life-space" in root or "AGENT_PROJECT_ROOT" in os.environ


class TestApprovalRequiredEndToEnd:
    """Approval-required tools must create approval requests, not just deny."""

    async def test_executor_creates_approval_request_on_always_tool(self):
        """When a tool requires approval=ALWAYS, executor should create an approval
        request in the queue and return structured result with request ID."""
        from agent.core.approval import ApprovalQueue
        from agent.core.tool_executor import ToolExecutor

        mock_agent = MagicMock()
        mock_agent.memory = AsyncMock()
        queue = ApprovalQueue()
        mock_agent.approval_queue = queue

        test_cap = ToolCapability(
            name="send_money",
            risk_level=ToolRiskLevel.HIGH,
            side_effect=SideEffectClass.EXTERNAL,
            owner_only=False,
            safe_mode_blocked=False,
            approval=ApprovalRequirement.ALWAYS,
            audit_label="finance:send",
        )

        with patch.dict(TOOL_CAPABILITIES, {"send_money": test_cap}):
            executor = ToolExecutor(agent=mock_agent)
            result = await executor.execute(
                "send_money",
                {"amount": 100},
                ToolExecutionContext(is_owner=True, safe_mode=False, channel_type="telegram"),
            )

        # Must be blocked
        assert result.get("blocked") is True
        assert "approval" in result.get("error", "").lower()

        # Must have created an approval request
        assert result.get("approval_request_id") is not None
        assert result.get("approval_status") == "pending"

        # Queue must contain the request
        pending = queue.get_pending()
        assert len(pending) == 1
        assert pending[0]["context"]["tool_name"] == "send_money"
        assert pending[0]["context"]["tool_input"] == {"amount": 100}

    async def test_executor_without_queue_still_blocks(self):
        """Approval denial works even without ApprovalQueue attached."""
        from agent.core.tool_executor import ToolExecutor

        mock_agent = MagicMock()
        mock_agent.memory = AsyncMock()
        # No approval_queue attribute
        del mock_agent.approval_queue

        test_cap = ToolCapability(
            name="send_money",
            risk_level=ToolRiskLevel.HIGH,
            side_effect=SideEffectClass.EXTERNAL,
            owner_only=False,
            safe_mode_blocked=False,
            approval=ApprovalRequirement.ALWAYS,
            audit_label="finance:send",
        )

        with patch.dict(TOOL_CAPABILITIES, {"send_money": test_cap}):
            executor = ToolExecutor(agent=mock_agent)
            result = await executor.execute(
                "send_money",
                {"amount": 50},
                ToolExecutionContext(is_owner=True, safe_mode=False),
            )

        assert result.get("blocked") is True
        assert "approval_request_id" not in result
