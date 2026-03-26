"""
Agent Life Space — Approval Queue

Centrálne miesto pre risk-sensitive akcie čakajúce na schválenie.

Flow:
    1. Agent navrhne akciu → propose()
    2. Akcia čaká v queue → PENDING
    3. Owner schváli/zamietne → approve() / deny()
    4. Agent vykoná → mark_executed()

Každá akcia má:
    - unikátne ID
    - typ (finance, tool, external)
    - risk level
    - dôvod prečo vyžaduje approval
    - timestamp + TTL
    - rozhodnutie (approved/denied/expired)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    PARTIALLY_APPROVED = "partially_approved"  # Multi-step: some approvers approved
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    EXECUTED = "executed"


class ApprovalCategory(str, Enum):
    FINANCE = "finance"       # Sending money, budget proposals
    TOOL = "tool"             # High-risk tool execution
    EXTERNAL = "external"     # External API calls, network writes
    HOST = "host"             # Host filesystem access


@dataclass
class ApprovalRequest:
    """A single action awaiting owner approval."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    category: ApprovalCategory = ApprovalCategory.TOOL
    description: str = ""
    risk_level: str = "medium"
    reason: str = ""  # Why this needs approval
    proposed_by: str = "agent"  # Who/what proposed this
    context: dict[str, Any] = field(default_factory=dict)  # Extra metadata

    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: float = field(default_factory=time.time)
    decided_at: float = 0.0
    decided_by: str = ""
    denial_reason: str = ""
    ttl_seconds: int = 3600  # Expires after 1 hour by default
    executed_at: float = 0.0

    # Multi-step approval
    required_approvals: int = 1  # How many approvals needed
    approvals_received: list[str] = field(default_factory=list)  # Who approved so far

    @property
    def is_expired(self) -> bool:
        if self.status not in (ApprovalStatus.PENDING, ApprovalStatus.PARTIALLY_APPROVED):
            return False
        return time.time() - self.created_at >= self.ttl_seconds

    @property
    def is_fully_approved(self) -> bool:
        return len(self.approvals_received) >= self.required_approvals

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category.value,
            "description": self.description,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "proposed_by": self.proposed_by,
            "status": self.status.value,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "denial_reason": self.denial_reason,
            "ttl_seconds": self.ttl_seconds,
            "required_approvals": self.required_approvals,
            "approvals_received": list(self.approvals_received),
        }


class ApprovalQueue:
    """
    Structured approval queue. All risk-sensitive actions go here.

    Owner can:
        - list pending approvals
        - approve/deny individual items
        - see history of decisions
    """

    def __init__(self, max_history: int = 500) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalRequest] = []
        self._max_history = max_history

    def propose(
        self,
        category: ApprovalCategory,
        description: str,
        risk_level: str = "medium",
        reason: str = "",
        proposed_by: str = "agent",
        context: dict[str, Any] | None = None,
        ttl_seconds: int = 3600,
        required_approvals: int = 1,
    ) -> ApprovalRequest:
        """Submit an action for approval. Returns the request."""
        req = ApprovalRequest(
            category=category,
            description=description,
            risk_level=risk_level,
            reason=reason,
            proposed_by=proposed_by,
            context=context or {},
            ttl_seconds=ttl_seconds,
            required_approvals=max(1, required_approvals),
        )
        self._pending[req.id] = req
        logger.info("approval_proposed",
                     id=req.id, category=category.value,
                     description=description[:100])
        return req

    def approve(self, request_id: str, decided_by: str = "owner") -> ApprovalRequest | None:
        """
        Approve a pending request.
        For multi-step: records approval, only fully approves when all needed.
        """
        req = self._pending.get(request_id)
        if not req:
            return None
        if req.is_expired:
            self._pending.pop(request_id)
            req.status = ApprovalStatus.EXPIRED
            self._archive(req)
            return req

        # Record this approval
        if decided_by not in req.approvals_received:
            req.approvals_received.append(decided_by)

        if req.is_fully_approved:
            # All required approvals received
            self._pending.pop(request_id)
            req.status = ApprovalStatus.APPROVED
            req.decided_at = time.time()
            req.decided_by = decided_by
            self._archive(req)
            logger.info("approval_granted", id=request_id, by=decided_by,
                        approvals=len(req.approvals_received))
        else:
            # Partially approved — still pending
            req.status = ApprovalStatus.PARTIALLY_APPROVED
            logger.info("approval_partial", id=request_id, by=decided_by,
                        received=len(req.approvals_received),
                        required=req.required_approvals)

        return req

    def deny(
        self, request_id: str, reason: str = "", decided_by: str = "owner"
    ) -> ApprovalRequest | None:
        """Deny a pending request."""
        req = self._pending.pop(request_id, None)
        if not req:
            return None

        req.status = ApprovalStatus.DENIED
        req.decided_at = time.time()
        req.decided_by = decided_by
        req.denial_reason = reason
        self._archive(req)
        logger.info("approval_denied", id=request_id, reason=reason[:100])
        return req

    def mark_executed(self, request_id: str) -> bool:
        """Mark an approved request as executed."""
        for req in self._history:
            if req.id == request_id and req.status == ApprovalStatus.APPROVED:
                req.status = ApprovalStatus.EXECUTED
                req.executed_at = time.time()
                return True
        return False

    def expire_stale(self) -> int:
        """Expire pending requests past their TTL."""
        expired = 0
        for req_id in list(self._pending.keys()):
            req = self._pending[req_id]
            if req.is_expired:
                req.status = ApprovalStatus.EXPIRED
                self._pending.pop(req_id)
                self._archive(req)
                expired += 1
        if expired:
            logger.info("approvals_expired", count=expired)
        return expired

    def get_pending(self) -> list[dict[str, Any]]:
        """List all pending approvals."""
        self.expire_stale()
        return [r.to_dict() for r in self._pending.values()]

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """List decided approvals."""
        return [r.to_dict() for r in self._history[-limit:]]

    def get_by_category(
        self, category: ApprovalCategory
    ) -> list[dict[str, Any]]:
        """Pending approvals filtered by category."""
        self.expire_stale()
        return [
            r.to_dict() for r in self._pending.values()
            if r.category == category
        ]

    def get_stats(self) -> dict[str, Any]:
        self.expire_stale()
        by_status: dict[str, int] = {}
        for req in self._history:
            by_status[req.status.value] = by_status.get(req.status.value, 0) + 1
        return {
            "pending": len(self._pending),
            "history_total": len(self._history),
            "by_status": by_status,
        }

    def _archive(self, req: ApprovalRequest) -> None:
        self._history.append(req)
        if len(self._history) > self._max_history:
            self._history.pop(0)
