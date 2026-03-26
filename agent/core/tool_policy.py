"""
Agent Life Space — Tool Policy

Deterministická vrstva medzi LLM tool requestom a samotným vykonaním.
LLM navrhuje, policy rozhodne, executor vykoná len povolené akcie.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolExecutionContext:
    """Request-scoped context used for tool authorization."""

    is_owner: bool = True
    safe_mode: bool = False
    channel_type: str = "internal"


@dataclass(frozen=True)
class ToolPolicyDecision:
    """Result of policy evaluation for a tool call."""

    allowed: bool
    risk_level: ToolRiskLevel
    reason: str = ""


_TOOL_RISK_LEVELS: dict[str, ToolRiskLevel] = {
    "store_memory": ToolRiskLevel.LOW,
    "query_memory": ToolRiskLevel.LOW,
    "list_tasks": ToolRiskLevel.LOW,
    "check_health": ToolRiskLevel.LOW,
    "get_status": ToolRiskLevel.LOW,
    "search_knowledge": ToolRiskLevel.LOW,
    "create_task": ToolRiskLevel.MEDIUM,
    "web_fetch": ToolRiskLevel.MEDIUM,
    "run_code": ToolRiskLevel.HIGH,
    "run_tests": ToolRiskLevel.HIGH,
}

_SAFE_MODE_BLOCKED_TOOLS = frozenset({
    "create_task",
    "run_code",
    "run_tests",
    "web_fetch",
})

_OWNER_ONLY_TOOLS = frozenset({
    "create_task",
    "run_code",
    "run_tests",
    "web_fetch",
})


class ToolPolicy:
    """
    Deterministic authorization rules for tools.

    Safety goals:
      - non-owner contexts cannot trigger high-impact side effects
      - safe mode blocks tools with code execution or external network access
      - read-only introspection remains available
    """

    def evaluate(
        self,
        tool_name: str,
        context: ToolExecutionContext | None = None,
    ) -> ToolPolicyDecision:
        ctx = context or ToolExecutionContext()
        risk_level = _TOOL_RISK_LEVELS.get(tool_name, ToolRiskLevel.MEDIUM)

        if ctx.safe_mode and tool_name in _SAFE_MODE_BLOCKED_TOOLS:
            return ToolPolicyDecision(
                allowed=False,
                risk_level=risk_level,
                reason=(
                    f"Tool '{tool_name}' is blocked in safe mode. "
                    "Requires owner-approved private context."
                ),
            )

        if not ctx.is_owner and tool_name in _OWNER_ONLY_TOOLS:
            return ToolPolicyDecision(
                allowed=False,
                risk_level=risk_level,
                reason=(
                    f"Tool '{tool_name}' is owner-only because it can trigger "
                    "external actions or code execution."
                ),
            )

        return ToolPolicyDecision(allowed=True, risk_level=risk_level)
