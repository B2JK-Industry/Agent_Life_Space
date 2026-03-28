"""
Agent Life Space — Structured Denial Payloads

Small shared helpers for operator-visible deny-by-default and block reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExecutionDenial:
    """Structured, machine-readable block/denial payload."""

    code: str
    summary: str
    detail: str = ""
    scope: str = ""
    policy_id: str = ""
    environment_profile_id: str = ""
    suggested_action: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        if self.detail:
            return f"{self.summary}: {self.detail}"
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "summary": self.summary,
            "detail": self.detail,
            "scope": self.scope,
            "policy_id": self.policy_id,
            "environment_profile_id": self.environment_profile_id,
            "suggested_action": self.suggested_action,
            "metadata": dict(self.metadata),
        }


def make_denial(
    *,
    code: str,
    summary: str,
    detail: str = "",
    scope: str = "",
    policy_id: str = "",
    environment_profile_id: str = "",
    suggested_action: str = "",
    metadata: dict[str, Any] | None = None,
) -> ExecutionDenial:
    """Build a structured denial payload with a stable schema."""

    return ExecutionDenial(
        code=code,
        summary=summary,
        detail=detail,
        scope=scope,
        policy_id=policy_id,
        environment_profile_id=environment_profile_id,
        suggested_action=suggested_action,
        metadata=metadata or {},
    )
