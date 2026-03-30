"""
Tests for v1.21.0 Phase 3 features: cost accuracy and unified policy boundary.
"""

from __future__ import annotations

import pytest

from agent.control.models import TraceRecordKind
from agent.control.policy import (
    RuntimeActionRequest,
    RuntimePolicyDecision,
    evaluate_runtime_action,
)


class TestTraceRecordKindCostAccuracy:

    def test_cost_accuracy_kind_exists(self):
        assert TraceRecordKind.COST_ACCURACY == "cost_accuracy"

    def test_all_trace_kinds_are_unique(self):
        values = [k.value for k in TraceRecordKind]
        assert len(values) == len(set(values))


class TestRuntimeActionRequest:

    def test_default_values(self):
        action = RuntimeActionRequest(action_type="review")
        assert action.action_type == "review"
        assert action.source == ""
        assert action.estimated_cost_usd == 0.0
        assert action.policy_overrides == {}

    def test_frozen(self):
        action = RuntimeActionRequest(action_type="build")
        with pytest.raises(AttributeError):
            action.action_type = "review"  # type: ignore[misc]

    def test_policy_overrides_default(self):
        action = RuntimeActionRequest(action_type="review")
        assert isinstance(action.policy_overrides, dict)


class TestEvaluateRuntimeAction:

    def test_review_action_allowed(self):
        action = RuntimeActionRequest(
            action_type="review",
            source="telegram",
            review_type="repo_audit",
        )
        decision = evaluate_runtime_action(action)
        assert decision.allowed is True
        assert any("review_execution" in p for p in decision.applied_policies)

    def test_build_action_allowed(self):
        action = RuntimeActionRequest(
            action_type="build",
            source="operator",
            build_type="implementation",
        )
        decision = evaluate_runtime_action(action)
        assert decision.allowed is True
        assert any("build_execution" in p for p in decision.applied_policies)

    def test_deliver_action_blocked_without_approval(self):
        action = RuntimeActionRequest(
            action_type="deliver",
            approval_status="pending",
        )
        decision = evaluate_runtime_action(action)
        assert decision.allowed is False
        assert any("delivery" in p for p in decision.blocking_policies)

    def test_deliver_action_allowed_with_approval(self):
        action = RuntimeActionRequest(
            action_type="deliver",
            approval_status="approved",
        )
        decision = evaluate_runtime_action(action)
        assert decision.allowed is True

    def test_gateway_send_blocked_without_auth(self):
        action = RuntimeActionRequest(
            action_type="gateway_send",
            target_url="https://obolos.tech/api/test",
            auth_provided=False,
            approval_status="",
        )
        decision = evaluate_runtime_action(action)
        assert any("gateway" in p for p in decision.applied_policies)

    def test_budget_policy_applied_when_cost_specified(self):
        action = RuntimeActionRequest(
            action_type="review",
            source="telegram",
            estimated_cost_usd=5.0,
        )
        decision = evaluate_runtime_action(action)
        assert any("budget" in p for p in decision.applied_policies)

    def test_budget_policy_not_applied_for_zero_cost(self):
        action = RuntimeActionRequest(
            action_type="review",
            source="telegram",
            estimated_cost_usd=0.0,
        )
        decision = evaluate_runtime_action(action)
        assert not any("budget" in p for p in decision.applied_policies)

    def test_decision_to_dict(self):
        decision = RuntimePolicyDecision(
            allowed=True,
            blocking_policies=[],
            warnings=["test warning"],
            applied_policies=["review_execution:repo_host_read_only"],
        )
        d = decision.to_dict()
        assert d["allowed"] is True
        assert d["warnings"] == ["test warning"]

    def test_unknown_action_type_returns_allowed(self):
        action = RuntimeActionRequest(action_type="unknown_type")
        decision = evaluate_runtime_action(action)
        assert decision.allowed is True
        assert len(decision.applied_policies) == 0
