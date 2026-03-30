"""
Agent Life Space — Action Envelope

Jednotný obal pre každú akciu agenta. Zachytáva celý lifecycle:
    1. REQUEST  — LLM navrhne tool call
    2. POLICY   — policy engine rozhodne
    3. EXECUTE  — handler vykoná akciu
    4. RESULT   — výsledok zaznamenaný

Každý krok je explicitný, auditovateľný, serializovateľný.
Žiadna akcia sa nevykoná bez prechodu cez všetky 4 kroky.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ActionPhase(str, Enum):
    """Current phase of action lifecycle."""

    REQUESTED = "requested"
    POLICY_CHECKED = "policy_checked"
    EXECUTING = "executing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class ActionEnvelope:
    """
    Mutable lifecycle record of a single agent action.
    Created at request time, updated through each phase.
    """

    # Identity
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)

    # Context
    is_owner: bool = True
    safe_mode: bool = False
    channel_type: str = "internal"
    request_source: str = "llm"  # llm, internal, api

    # Phase tracking
    phase: ActionPhase = ActionPhase.REQUESTED
    requested_at: float = field(default_factory=time.time)

    # Policy decision
    policy_allowed: bool | None = None
    policy_reason: str = ""
    policy_risk_level: str = ""
    policy_side_effect: str = ""
    policy_audit_label: str = ""
    policy_decided_at: float = 0.0

    # Execution result
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    executed_at: float = 0.0
    completed_at: float = 0.0
    duration_ms: int = 0

    def to_audit_record(self) -> dict[str, Any]:
        """Serialize for audit log / inspection."""
        return {
            "id": self.id,
            "tool": self.tool_name,
            "phase": self.phase.value,
            "is_owner": self.is_owner,
            "safe_mode": self.safe_mode,
            "channel": self.channel_type,
            "source": self.request_source,
            "policy_allowed": self.policy_allowed,
            "policy_reason": self.policy_reason,
            "policy_risk": self.policy_risk_level,
            "policy_side_effect": self.policy_side_effect,
            "policy_label": self.policy_audit_label,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "requested_at": self.requested_at,
            "completed_at": self.completed_at,
        }


class ActionLog:
    """
    Append-only action log. Ring buffer for memory safety.
    Every action the agent takes is recorded here.
    """

    def __init__(self, max_entries: int = 2000) -> None:
        self._entries: list[ActionEnvelope] = []
        self._max = max_entries

    def record(self, action: ActionEnvelope) -> None:
        self._entries.append(action)
        if len(self._entries) > self._max:
            self._entries.pop(0)

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return [a.to_audit_record() for a in self._entries[-limit:]]

    def get_by_phase(self, phase: ActionPhase, limit: int = 50) -> list[dict[str, Any]]:
        return [
            a.to_audit_record()
            for a in self._entries
            if a.phase == phase
        ][-limit:]

    def get_blocked(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.get_by_phase(ActionPhase.BLOCKED, limit)

    def get_failed(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.get_by_phase(ActionPhase.FAILED, limit)

    @property
    def total(self) -> int:
        return len(self._entries)

    @property
    def total_blocked(self) -> int:
        return sum(1 for a in self._entries if a.phase == ActionPhase.BLOCKED)

    @property
    def total_failed(self) -> int:
        return sum(1 for a in self._entries if a.phase == ActionPhase.FAILED)

    def get_stats(self) -> dict[str, Any]:
        by_phase: dict[str, int] = {}
        by_tool: dict[str, int] = {}
        for a in self._entries:
            by_phase[a.phase.value] = by_phase.get(a.phase.value, 0) + 1
            by_tool[a.tool_name] = by_tool.get(a.tool_name, 0) + 1
        return {
            "total": self.total,
            "by_phase": by_phase,
            "by_tool": by_tool,
            "blocked": self.total_blocked,
            "failed": self.total_failed,
        }
