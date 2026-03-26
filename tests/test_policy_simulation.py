"""
Tests for policy simulation and structured denial codes.
"""

from __future__ import annotations

from agent.core.tool_policy import (
    DenialCode,
    ToolExecutionContext,
    ToolPolicy,
)


class TestDenialCodes:
    """Blocked decisions have structured denial codes."""

    def test_safe_mode_denial_code(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=True, safe_mode=True)
        decision = policy.evaluate("run_code", ctx)
        assert not decision.allowed
        assert decision.denial_code == DenialCode.SAFE_MODE

    def test_owner_only_denial_code(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=False, safe_mode=False)
        decision = policy.evaluate("run_code", ctx)
        assert not decision.allowed
        assert decision.denial_code == DenialCode.OWNER_ONLY

    def test_unknown_tool_denial_code(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        decision = policy.evaluate("evil_tool", ctx)
        assert not decision.allowed
        assert decision.denial_code == DenialCode.UNKNOWN_TOOL

    def test_allowed_has_no_denial_code(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False)
        decision = policy.evaluate("check_health", ctx)
        assert decision.allowed
        assert decision.denial_code is None

    def test_denial_code_in_audit_log(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=True, safe_mode=True)
        policy.evaluate("run_code", ctx)

        log = policy.audit_log.get_recent(1)
        assert log[0]["denial_code"] == "safe_mode"

    def test_allowed_audit_has_null_denial_code(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=True)
        policy.evaluate("check_health", ctx)

        log = policy.audit_log.get_recent(1)
        assert log[0]["denial_code"] is None


class TestPolicySimulation:
    """simulate() shows what WOULD happen without logging."""

    def test_simulate_allowed(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False)
        result = policy.simulate("check_health", ctx)
        assert result["would_allow"] is True
        assert result["denial_code"] is None

    def test_simulate_blocked_safe_mode(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=True, safe_mode=True)
        result = policy.simulate("run_code", ctx)
        assert result["would_allow"] is False
        assert result["denial_code"] == "safe_mode"

    def test_simulate_blocked_owner_only(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=False, safe_mode=False)
        result = policy.simulate("web_fetch", ctx)
        assert result["would_allow"] is False
        assert result["denial_code"] == "owner_only"

    def test_simulate_unknown_tool(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(safe_mode=True)
        result = policy.simulate("does_not_exist", ctx)
        assert result["would_allow"] is False
        assert result["denial_code"] == "unknown_tool"

    def test_simulate_does_not_log(self):
        policy = ToolPolicy()
        policy.simulate("run_code", ToolExecutionContext())
        assert policy.audit_log.total_decisions == 0

    def test_simulate_all(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        results = policy.simulate_all(ctx)
        assert len(results) >= 10
        # All sensitive tools should be blocked
        blocked = [r for r in results if not r["would_allow"]]
        assert len(blocked) >= 4

    def test_simulate_all_owner(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False)
        results = policy.simulate_all(ctx)
        # Owner should have all tools allowed
        assert all(r["would_allow"] for r in results)

    def test_simulate_returns_metadata(self):
        policy = ToolPolicy()
        result = policy.simulate("run_code", ToolExecutionContext())
        assert "risk_level" in result
        assert "side_effect" in result
        assert "owner_only" in result
        assert "approval" in result
