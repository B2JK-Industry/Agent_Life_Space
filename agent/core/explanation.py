"""
Agent Life Space — Explanation Layer

"Why did I do this?" — every agent decision is explainable.

Combines information from:
    - Task classification (routing signals)
    - Policy decisions (allowed/blocked + why)
    - Learning context (past errors, skill confidence)
    - Memory context (what was recalled)

Owner can ask for explanation of any action.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class DecisionExplanation:
    """Explainable record of why the agent did something."""

    action_id: str = ""
    timestamp: float = field(default_factory=time.time)

    # What happened
    action_type: str = ""       # "message_response", "tool_call", "task_creation"
    action_summary: str = ""    # Human-readable summary

    # Why this routing
    routing_task_type: str = ""  # "simple", "programming", "analysis", etc.
    routing_score: int = 0
    routing_signals: dict[str, int] = field(default_factory=dict)
    model_used: str = ""

    # Policy context
    policy_decisions: list[dict[str, Any]] = field(default_factory=list)

    # Learning context
    learning_escalation: str = ""  # Was model escalated? Why?
    past_errors_used: list[str] = field(default_factory=list)
    skill_confidence: dict[str, float] = field(default_factory=dict)

    # Memory context
    memories_recalled: int = 0
    provenance_breakdown: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "timestamp": self.timestamp,
            "action_type": self.action_type,
            "action_summary": self.action_summary,
            "routing": {
                "task_type": self.routing_task_type,
                "score": self.routing_score,
                "signals": self.routing_signals,
                "model": self.model_used,
            },
            "policy": self.policy_decisions,
            "learning": {
                "escalation": self.learning_escalation,
                "past_errors_used": self.past_errors_used,
                "skill_confidence": self.skill_confidence,
            },
            "memory": {
                "recalled": self.memories_recalled,
                "provenance": self.provenance_breakdown,
            },
        }

    def explain(self) -> str:
        """Human-readable explanation."""
        parts = [f"Akcia: {self.action_summary}"]

        if self.routing_task_type:
            parts.append(
                f"Routing: typ={self.routing_task_type}, "
                f"score={self.routing_score}, model={self.model_used}"
            )
            if self.routing_signals:
                sig = ", ".join(f"{k}={v}" for k, v in self.routing_signals.items())
                parts.append(f"  Signály: {sig}")

        if self.policy_decisions:
            for pd in self.policy_decisions:
                status = "povolené" if pd.get("allowed") else "blokované"
                parts.append(f"Policy: {pd.get('tool', '?')} → {status}")
                if pd.get("reason"):
                    parts.append(f"  Dôvod: {pd['reason']}")

        if self.learning_escalation:
            parts.append(f"Eskalácia: {self.learning_escalation}")

        if self.past_errors_used:
            parts.append(f"Minulé chyby: {len(self.past_errors_used)} relevantných")

        if self.memories_recalled:
            parts.append(f"Pamäť: {self.memories_recalled} recalled")

        return "\n".join(parts)


class ExplanationLog:
    """Ring buffer of decision explanations."""

    def __init__(self, max_entries: int = 500) -> None:
        self._entries: list[DecisionExplanation] = []
        self._max = max_entries

    def record(self, explanation: DecisionExplanation) -> None:
        self._entries.append(explanation)
        if len(self._entries) > self._max:
            self._entries.pop(0)

    def get_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries[-limit:]]

    def get_last_explanation(self) -> str | None:
        """Get human-readable explanation of last action."""
        if not self._entries:
            return None
        return self._entries[-1].explain()

    def find_by_action_id(self, action_id: str) -> DecisionExplanation | None:
        for e in reversed(self._entries):
            if e.action_id == action_id:
                return e
        return None

    @property
    def total(self) -> int:
        return len(self._entries)
