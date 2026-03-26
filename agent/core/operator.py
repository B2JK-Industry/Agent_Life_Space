"""
Agent Life Space — Operator Controls

Runtime capability toggle for the owner.
Disable/enable capabilities without code deploy.

Use cases:
    - Temporarily disable web_fetch during incident
    - Disable run_code while debugging sandbox
    - Lock down all external tools during maintenance

Controls are:
    - In-memory (reset on restart by design)
    - Auditable (every toggle is logged)
    - Owner-only (no agent self-modification)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class CapabilityOverride:
    """A runtime override for a tool capability."""

    tool_name: str
    enabled: bool
    reason: str
    set_by: str
    set_at: float = field(default_factory=time.time)


class OperatorControls:
    """
    Runtime capability toggles for the owner.

    Overrides take priority over static policy.
    Resets on restart (intentional — prevents stale lockdowns).
    """

    def __init__(self) -> None:
        self._overrides: dict[str, CapabilityOverride] = {}
        self._history: list[dict[str, Any]] = []
        self._max_history = 200

    def disable(self, tool_name: str, reason: str = "", by: str = "owner") -> None:
        """Disable a tool at runtime."""
        override = CapabilityOverride(
            tool_name=tool_name,
            enabled=False,
            reason=reason,
            set_by=by,
        )
        self._overrides[tool_name] = override
        self._record("disabled", tool_name, reason, by)
        logger.info("operator_tool_disabled", tool=tool_name, reason=reason, by=by)

    def enable(self, tool_name: str, reason: str = "", by: str = "owner") -> None:
        """Re-enable a previously disabled tool."""
        if tool_name in self._overrides:
            del self._overrides[tool_name]
        self._record("enabled", tool_name, reason, by)
        logger.info("operator_tool_enabled", tool=tool_name, reason=reason, by=by)

    def is_disabled(self, tool_name: str) -> bool:
        """Check if a tool has been disabled by operator."""
        override = self._overrides.get(tool_name)
        return override is not None and not override.enabled

    def get_disabled_reason(self, tool_name: str) -> str:
        """Get the reason why a tool was disabled."""
        override = self._overrides.get(tool_name)
        if override and not override.enabled:
            return override.reason
        return ""

    def lockdown(self, reason: str = "maintenance", by: str = "owner") -> None:
        """Disable ALL external-facing tools."""
        from agent.core.tool_policy import TOOL_CAPABILITIES, SideEffectClass

        for name, cap in TOOL_CAPABILITIES.items():
            if cap.side_effect in (SideEffectClass.EXTERNAL, SideEffectClass.DESTRUCTIVE):
                self.disable(name, reason=reason, by=by)

    def unlock(self, reason: str = "maintenance complete", by: str = "owner") -> None:
        """Re-enable all disabled tools."""
        for tool_name in list(self._overrides.keys()):
            self.enable(tool_name, reason=reason, by=by)

    def get_status(self) -> dict[str, Any]:
        """Current override status."""
        disabled = {
            name: {"reason": o.reason, "by": o.set_by, "since": o.set_at}
            for name, o in self._overrides.items()
            if not o.enabled
        }
        return {
            "disabled_tools": disabled,
            "total_disabled": len(disabled),
            "in_lockdown": len(disabled) >= 3,
        }

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._history[-limit:]

    def _record(self, action: str, tool: str, reason: str, by: str) -> None:
        entry = {
            "action": action,
            "tool": tool,
            "reason": reason,
            "by": by,
            "timestamp": time.time(),
        }
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history.pop(0)
