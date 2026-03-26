"""
Tests for tool governance — capability manifest, policy decisions, audit trail.
"""

from __future__ import annotations

import pytest

from agent.core.tool_policy import (
    TOOL_CAPABILITIES,
    ApprovalRequirement,
    SideEffectClass,
    ToolExecutionContext,
    ToolPolicy,
    ToolRiskLevel,
)


class TestCapabilityManifest:
    """All tools have complete capability manifest entries."""

    def test_all_expected_tools_in_manifest(self):
        expected = [
            "store_memory", "query_memory", "create_task", "list_tasks",
            "run_code", "run_tests", "web_fetch", "check_health",
            "get_status", "search_knowledge",
        ]
        for name in expected:
            assert name in TOOL_CAPABILITIES, f"Tool '{name}' missing from manifest"

    def test_manifest_has_all_required_fields(self):
        for name, cap in TOOL_CAPABILITIES.items():
            assert isinstance(cap.risk_level, ToolRiskLevel), f"{name} missing risk_level"
            assert isinstance(cap.side_effect, SideEffectClass), f"{name} missing side_effect"
            assert isinstance(cap.owner_only, bool), f"{name} missing owner_only"
            assert isinstance(cap.safe_mode_blocked, bool), f"{name} missing safe_mode_blocked"
            assert isinstance(cap.approval, ApprovalRequirement), f"{name} missing approval"
            assert cap.audit_label, f"{name} missing audit_label"

    def test_read_only_tools_have_no_side_effects(self):
        read_only = ["query_memory", "list_tasks", "check_health", "get_status", "search_knowledge"]
        for name in read_only:
            assert TOOL_CAPABILITIES[name].side_effect == SideEffectClass.NONE

    def test_execution_tools_are_high_risk(self):
        assert TOOL_CAPABILITIES["run_code"].risk_level == ToolRiskLevel.HIGH
        assert TOOL_CAPABILITIES["run_tests"].risk_level == ToolRiskLevel.HIGH

    def test_execution_tools_have_external_side_effects(self):
        assert TOOL_CAPABILITIES["run_code"].side_effect == SideEffectClass.EXTERNAL
        assert TOOL_CAPABILITIES["run_tests"].side_effect == SideEffectClass.EXTERNAL


class TestPolicyDecisions:
    """Policy correctly authorizes/blocks tool calls based on context."""

    @pytest.fixture
    def policy(self):
        return ToolPolicy()

    def test_owner_can_use_all_tools(self, policy):
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False)
        for name in TOOL_CAPABILITIES:
            decision = policy.evaluate(name, ctx)
            assert decision.allowed, f"Owner should be able to use {name}"

    def test_safe_mode_blocks_sensitive_tools(self, policy):
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        blocked = ["run_code", "run_tests", "web_fetch", "create_task"]
        for name in blocked:
            decision = policy.evaluate(name, ctx)
            assert not decision.allowed, f"{name} should be blocked in safe mode"
            assert decision.reason  # Must explain why

    def test_safe_mode_allows_read_tools(self, policy):
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        allowed = ["query_memory", "list_tasks", "check_health", "get_status", "search_knowledge"]
        for name in allowed:
            decision = policy.evaluate(name, ctx)
            assert decision.allowed, f"{name} should be allowed in safe mode"

    def test_non_owner_blocked_from_owner_only(self, policy):
        ctx = ToolExecutionContext(is_owner=False, safe_mode=False)
        for name, cap in TOOL_CAPABILITIES.items():
            if cap.owner_only:
                decision = policy.evaluate(name, ctx)
                assert not decision.allowed, f"Non-owner should not use {name}"

    def test_unknown_tool_blocked_in_safe_mode(self, policy):
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)
        decision = policy.evaluate("totally_unknown_tool", ctx)
        assert not decision.allowed

    def test_unknown_tool_denied_for_owner(self, policy):
        """Deny-by-default: unknown tools blocked even for owner."""
        ctx = ToolExecutionContext(is_owner=True, safe_mode=False)
        decision = policy.evaluate("totally_unknown_tool", ctx)
        assert not decision.allowed
        assert decision.risk_level == ToolRiskLevel.HIGH

    def test_decision_includes_metadata(self, policy):
        decision = policy.evaluate("run_code", ToolExecutionContext())
        assert decision.risk_level == ToolRiskLevel.HIGH
        assert decision.side_effect == SideEffectClass.EXTERNAL
        assert decision.audit_label == "sandbox:execute"
        assert decision.timestamp > 0


class TestAuditTrail:
    """Policy audit log records all decisions."""

    def test_audit_records_decisions(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=True)

        policy.evaluate("check_health", ctx)
        policy.evaluate("run_code", ctx)

        assert policy.audit_log.total_decisions == 2
        recent = policy.audit_log.get_recent(10)
        assert len(recent) == 2
        assert recent[0]["tool"] == "check_health"
        assert recent[1]["tool"] == "run_code"

    def test_audit_tracks_blocked(self):
        policy = ToolPolicy()
        ctx = ToolExecutionContext(is_owner=False, safe_mode=True)

        policy.evaluate("run_code", ctx)
        policy.evaluate("check_health", ctx)

        assert policy.audit_log.total_blocked == 1
        blocked = policy.audit_log.get_blocked()
        assert len(blocked) == 1
        assert blocked[0]["tool"] == "run_code"

    def test_audit_ring_buffer(self):
        from agent.core.tool_policy import PolicyAuditLog
        log = PolicyAuditLog(max_entries=3)
        ctx = ToolExecutionContext()
        policy = ToolPolicy()
        policy._audit = log

        for _i in range(5):
            policy.evaluate("check_health", ctx)

        assert log.total_decisions == 3  # Oldest 2 evicted

    def test_manifest_export(self):
        policy = ToolPolicy()
        manifest = policy.get_manifest()
        assert len(manifest) == len(TOOL_CAPABILITIES)
        for entry in manifest:
            assert "name" in entry
            assert "risk_level" in entry
            assert "side_effect" in entry
            assert "audit_label" in entry
