"""
Agent Life Space — Tool Policy

Deterministická vrstva medzi LLM tool requestom a samotným vykonaním.
LLM navrhuje, policy rozhodne, executor vykoná len povolené akcie.

Capability manifest: každý tool má risk class, side-effect class,
owner/safe-mode policy, approval requirement, a audit label.
Všetky policy decisions sú logované pre audit trail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SideEffectClass(str, Enum):
    """What kind of side effects does this tool produce?"""

    NONE = "none"          # Read-only, no state change
    INTERNAL = "internal"  # Changes agent's own state (memory, tasks)
    EXTERNAL = "external"  # Reaches outside (network, file system)
    DESTRUCTIVE = "destructive"  # Can destroy data or cost money


class ApprovalRequirement(str, Enum):
    """When does this tool need explicit approval?"""

    NEVER = "never"       # Always allowed (for owner)
    SAFE_MODE = "safe_mode"  # Needs approval only in safe mode
    ALWAYS = "always"     # Always needs approval (e.g., finance)


@dataclass(frozen=True)
class ToolCapability:
    """Capability manifest entry for a single tool."""

    name: str
    risk_level: ToolRiskLevel
    side_effect: SideEffectClass
    owner_only: bool
    safe_mode_blocked: bool
    approval: ApprovalRequirement
    audit_label: str  # Human-readable label for audit log


# Central capability registry — single source of truth for all tool policies
TOOL_CAPABILITIES: dict[str, ToolCapability] = {
    "store_memory": ToolCapability(
        name="store_memory",
        risk_level=ToolRiskLevel.LOW,
        side_effect=SideEffectClass.INTERNAL,
        owner_only=False,
        safe_mode_blocked=False,
        approval=ApprovalRequirement.NEVER,
        audit_label="memory:write",
    ),
    "query_memory": ToolCapability(
        name="query_memory",
        risk_level=ToolRiskLevel.LOW,
        side_effect=SideEffectClass.NONE,
        owner_only=False,
        safe_mode_blocked=False,
        approval=ApprovalRequirement.NEVER,
        audit_label="memory:read",
    ),
    "list_tasks": ToolCapability(
        name="list_tasks",
        risk_level=ToolRiskLevel.LOW,
        side_effect=SideEffectClass.NONE,
        owner_only=False,
        safe_mode_blocked=False,
        approval=ApprovalRequirement.NEVER,
        audit_label="tasks:read",
    ),
    "check_health": ToolCapability(
        name="check_health",
        risk_level=ToolRiskLevel.LOW,
        side_effect=SideEffectClass.NONE,
        owner_only=False,
        safe_mode_blocked=False,
        approval=ApprovalRequirement.NEVER,
        audit_label="system:health",
    ),
    "get_status": ToolCapability(
        name="get_status",
        risk_level=ToolRiskLevel.LOW,
        side_effect=SideEffectClass.NONE,
        owner_only=False,
        safe_mode_blocked=False,
        approval=ApprovalRequirement.NEVER,
        audit_label="system:status",
    ),
    "search_knowledge": ToolCapability(
        name="search_knowledge",
        risk_level=ToolRiskLevel.LOW,
        side_effect=SideEffectClass.NONE,
        owner_only=False,
        safe_mode_blocked=False,
        approval=ApprovalRequirement.NEVER,
        audit_label="knowledge:read",
    ),
    "create_task": ToolCapability(
        name="create_task",
        risk_level=ToolRiskLevel.MEDIUM,
        side_effect=SideEffectClass.INTERNAL,
        owner_only=True,
        safe_mode_blocked=True,
        approval=ApprovalRequirement.SAFE_MODE,
        audit_label="tasks:create",
    ),
    "web_fetch": ToolCapability(
        name="web_fetch",
        risk_level=ToolRiskLevel.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL,
        owner_only=True,
        safe_mode_blocked=True,
        approval=ApprovalRequirement.SAFE_MODE,
        audit_label="network:fetch",
    ),
    "run_code": ToolCapability(
        name="run_code",
        risk_level=ToolRiskLevel.HIGH,
        side_effect=SideEffectClass.EXTERNAL,
        owner_only=True,
        safe_mode_blocked=True,
        approval=ApprovalRequirement.SAFE_MODE,
        audit_label="sandbox:execute",
    ),
    "run_tests": ToolCapability(
        name="run_tests",
        risk_level=ToolRiskLevel.HIGH,
        side_effect=SideEffectClass.EXTERNAL,
        owner_only=True,
        safe_mode_blocked=True,
        approval=ApprovalRequirement.SAFE_MODE,
        audit_label="sandbox:test",
    ),
}


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
    side_effect: SideEffectClass = SideEffectClass.NONE
    audit_label: str = ""
    reason: str = ""
    timestamp: float = 0.0


@dataclass
class PolicyAuditLog:
    """In-memory audit log of policy decisions. Bounded ring buffer."""

    max_entries: int = 1000
    _entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, decision: ToolPolicyDecision, tool_name: str,
               context: ToolExecutionContext) -> None:
        entry = {
            "tool": tool_name,
            "allowed": decision.allowed,
            "risk_level": decision.risk_level.value,
            "side_effect": decision.side_effect.value,
            "audit_label": decision.audit_label,
            "reason": decision.reason,
            "is_owner": context.is_owner,
            "safe_mode": context.safe_mode,
            "channel": context.channel_type,
            "timestamp": decision.timestamp,
        }
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries.pop(0)

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._entries[-limit:]

    def get_blocked(self, limit: int = 50) -> list[dict[str, Any]]:
        return [e for e in self._entries if not e["allowed"]][-limit:]

    @property
    def total_decisions(self) -> int:
        return len(self._entries)

    @property
    def total_blocked(self) -> int:
        return sum(1 for e in self._entries if not e["allowed"])


class ToolPolicy:
    """
    Deterministic authorization rules for tools.
    Uses capability manifest for decisions, logs everything.
    """

    def __init__(self) -> None:
        self._audit = PolicyAuditLog()

    @property
    def audit_log(self) -> PolicyAuditLog:
        return self._audit

    def evaluate(
        self,
        tool_name: str,
        context: ToolExecutionContext | None = None,
    ) -> ToolPolicyDecision:
        ctx = context or ToolExecutionContext()
        cap = TOOL_CAPABILITIES.get(tool_name)
        ts = time.time()

        # Unknown tool — default to MEDIUM risk, block in safe mode
        if cap is None:
            decision = ToolPolicyDecision(
                allowed=not ctx.safe_mode,
                risk_level=ToolRiskLevel.MEDIUM,
                side_effect=SideEffectClass.EXTERNAL,
                audit_label=f"unknown:{tool_name}",
                reason=f"Unknown tool '{tool_name}' — blocked in safe mode."
                if ctx.safe_mode else "",
                timestamp=ts,
            )
            self._audit.record(decision, tool_name, ctx)
            self._log_decision(decision, tool_name, ctx)
            return decision

        # Safe mode check
        if ctx.safe_mode and cap.safe_mode_blocked:
            decision = ToolPolicyDecision(
                allowed=False,
                risk_level=cap.risk_level,
                side_effect=cap.side_effect,
                audit_label=cap.audit_label,
                reason=(
                    f"Tool '{tool_name}' is blocked in safe mode. "
                    "Requires owner-approved private context."
                ),
                timestamp=ts,
            )
            self._audit.record(decision, tool_name, ctx)
            self._log_decision(decision, tool_name, ctx)
            return decision

        # Owner-only check
        if not ctx.is_owner and cap.owner_only:
            decision = ToolPolicyDecision(
                allowed=False,
                risk_level=cap.risk_level,
                side_effect=cap.side_effect,
                audit_label=cap.audit_label,
                reason=(
                    f"Tool '{tool_name}' is owner-only because it can trigger "
                    "external actions or code execution."
                ),
                timestamp=ts,
            )
            self._audit.record(decision, tool_name, ctx)
            self._log_decision(decision, tool_name, ctx)
            return decision

        # Allowed
        decision = ToolPolicyDecision(
            allowed=True,
            risk_level=cap.risk_level,
            side_effect=cap.side_effect,
            audit_label=cap.audit_label,
            timestamp=ts,
        )
        self._audit.record(decision, tool_name, ctx)
        self._log_decision(decision, tool_name, ctx)
        return decision

    @staticmethod
    def _log_decision(
        decision: ToolPolicyDecision, tool_name: str, ctx: ToolExecutionContext
    ) -> None:
        if decision.allowed:
            logger.debug(
                "policy_allowed",
                tool=tool_name,
                audit_label=decision.audit_label,
                risk=decision.risk_level.value,
            )
        else:
            logger.warning(
                "policy_blocked",
                tool=tool_name,
                audit_label=decision.audit_label,
                risk=decision.risk_level.value,
                reason=decision.reason,
                is_owner=ctx.is_owner,
                safe_mode=ctx.safe_mode,
            )

    def get_manifest(self) -> list[dict[str, Any]]:
        """Return full capability manifest for inspection."""
        return [
            {
                "name": cap.name,
                "risk_level": cap.risk_level.value,
                "side_effect": cap.side_effect.value,
                "owner_only": cap.owner_only,
                "safe_mode_blocked": cap.safe_mode_blocked,
                "approval": cap.approval.value,
                "audit_label": cap.audit_label,
            }
            for cap in TOOL_CAPABILITIES.values()
        ]
