"""
Agent Life Space — Agent Status Model

Explicit agent state machine for operator visibility.

States:
    IDLE         — čaká na vstup, nič nerobí
    THINKING     — spracúva správu, volá LLM
    EXECUTING    — vykonáva tool/akciu
    WAITING_APPROVAL — čaká na owner approval
    BLOCKED      — niečo nefunguje (dependency, error)
    DEGRADED     — čiastočne funkčný (niektoré moduly down)
    MAINTENANCE  — plánovaná údržba

Owner vždy vie v akom stave agent je.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    DEGRADED = "degraded"
    MAINTENANCE = "maintenance"


@dataclass
class StateTransition:
    """Record of a state change."""

    from_state: AgentState
    to_state: AgentState
    reason: str
    timestamp: float = field(default_factory=time.time)


class AgentStatusModel:
    """
    Tracks agent state with audit trail.

    Owner can always see:
    - current state
    - how long in current state
    - reason for last transition
    - recent state history
    """

    def __init__(self, max_history: int = 200) -> None:
        self._state = AgentState.IDLE
        self._state_since = time.time()
        self._state_reason = "initialized"
        self._history: list[StateTransition] = []
        self._max_history = max_history
        self._blocked_reason = ""
        self._degraded_modules: list[str] = []

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def state_duration_seconds(self) -> float:
        return time.time() - self._state_since

    def transition(self, new_state: AgentState, reason: str = "") -> None:
        """Change state with audit trail."""
        if new_state == self._state:
            return  # No-op for same state

        transition = StateTransition(
            from_state=self._state,
            to_state=new_state,
            reason=reason,
        )
        self._history.append(transition)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        old = self._state
        self._state = new_state
        self._state_since = time.time()
        self._state_reason = reason

        if new_state == AgentState.BLOCKED:
            self._blocked_reason = reason
        elif new_state != AgentState.DEGRADED:
            self._blocked_reason = ""

        logger.info("agent_state_change",
                     from_state=old.value,
                     to_state=new_state.value,
                     reason=reason[:100])

    def mark_degraded(self, module: str, reason: str = "") -> None:
        """Mark a module as degraded."""
        if module not in self._degraded_modules:
            self._degraded_modules.append(module)
        if self._state != AgentState.DEGRADED:
            self.transition(AgentState.DEGRADED, reason or f"Module {module} degraded")

    def clear_degraded(self, module: str) -> None:
        """Mark a module as recovered."""
        if module in self._degraded_modules:
            self._degraded_modules.remove(module)
        if not self._degraded_modules and self._state == AgentState.DEGRADED:
            self.transition(AgentState.IDLE, f"Module {module} recovered")

    def get_status(self) -> dict[str, Any]:
        """Full status for owner inspection."""
        return {
            "state": self._state.value,
            "state_since": self._state_since,
            "state_duration_s": round(self.state_duration_seconds, 1),
            "reason": self._state_reason,
            "blocked_reason": self._blocked_reason,
            "degraded_modules": list(self._degraded_modules),
        }

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "from": t.from_state.value,
                "to": t.to_state.value,
                "reason": t.reason,
                "timestamp": t.timestamp,
            }
            for t in self._history[-limit:]
        ]
