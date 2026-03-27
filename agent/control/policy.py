"""
Agent Life Space — Control-Plane Policies

Deterministic policy profiles for build review gates and delivery decisions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewGatePolicy:
    """Deterministic blocking profile for post-build review."""

    id: str
    label: str
    description: str
    max_critical: int
    max_high: int
    block_fail_verdict: bool = True
    advisory_only: bool = False


@dataclass(frozen=True)
class DeliveryDecisionPolicy:
    """Deterministic delivery policy profile."""

    id: str
    label: str
    approval_required: bool = True
    allow_external_send: bool = False
    gateway_required: bool = False


_REVIEW_GATE_POLICIES: dict[str, ReviewGatePolicy] = {
    "critical_findings": ReviewGatePolicy(
        id="critical_findings",
        label="Critical findings block",
        description="Block completion only when critical findings or fail verdicts appear.",
        max_critical=0,
        max_high=999,
        block_fail_verdict=True,
    ),
    "high_or_critical": ReviewGatePolicy(
        id="high_or_critical",
        label="High or critical findings block",
        description="Block completion when critical or high findings appear.",
        max_critical=0,
        max_high=0,
        block_fail_verdict=True,
    ),
    "advisory": ReviewGatePolicy(
        id="advisory",
        label="Advisory only",
        description="Never block completion on review findings; capture them for delivery.",
        max_critical=999,
        max_high=999,
        block_fail_verdict=False,
        advisory_only=True,
    ),
}

_DELIVERY_POLICIES: dict[str, DeliveryDecisionPolicy] = {
    "approval_required": DeliveryDecisionPolicy(
        id="approval_required",
        label="Approval required",
        approval_required=True,
        allow_external_send=False,
        gateway_required=False,
    ),
    "gateway_only": DeliveryDecisionPolicy(
        id="gateway_only",
        label="Gateway only",
        approval_required=True,
        allow_external_send=False,
        gateway_required=True,
    ),
}


def get_review_gate_policy(policy_id: str = "critical_findings") -> ReviewGatePolicy:
    """Resolve a configured review gate policy."""
    return _REVIEW_GATE_POLICIES.get(policy_id, _REVIEW_GATE_POLICIES["critical_findings"])


def list_review_gate_policies() -> list[ReviewGatePolicy]:
    """Return known post-build review gate policies."""
    return list(_REVIEW_GATE_POLICIES.values())


def get_delivery_policy(policy_id: str = "approval_required") -> DeliveryDecisionPolicy:
    """Resolve a configured delivery decision policy."""
    return _DELIVERY_POLICIES.get(policy_id, _DELIVERY_POLICIES["approval_required"])


def list_delivery_policies() -> list[DeliveryDecisionPolicy]:
    """Return known delivery decision policies."""
    return list(_DELIVERY_POLICIES.values())
