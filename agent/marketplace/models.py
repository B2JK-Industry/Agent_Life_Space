"""
Agent Life Space — Marketplace Domain Models

Platform-agnostic models for the marketplace earning engine.
Connectors normalize platform-specific data into these models.

Hierarchy:
    Opportunity (external listing/job/gig)
      → Evaluation (can ALS handle it?)
        → Bid (prepared response/proposal)
          → Engagement (tracked project + execution)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# ─────────────────────────────────────────────
# Opportunity — normalized external listing
# ─────────────────────────────────────────────


class OpportunityStatus(str, Enum):
    DISCOVERED = "discovered"      # Just fetched from platform
    EVALUATED = "evaluated"        # Feasibility assessed
    BID_READY = "bid_ready"        # Bid/response prepared
    SUBMITTED = "submitted"        # Bid sent to platform
    ENGAGED = "engaged"            # Won / accepted, work started
    COMPLETED = "completed"        # Delivered
    SKIPPED = "skipped"            # Not feasible / not worth it
    LOST = "lost"                  # Bid rejected


@dataclass
class Opportunity:
    """Normalized external marketplace opportunity."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    platform: str = ""                # e.g. "obolos.tech"
    platform_id: str = ""             # ID on the source platform
    title: str = ""
    description: str = ""
    url: str = ""
    category: str = ""                # e.g. "api", "code-review", "data"
    budget_min: float = 0.0
    budget_max: float = 0.0
    currency: str = "USD"
    skills_required: list[str] = field(default_factory=list)
    deadline: str = ""
    status: OpportunityStatus = OpportunityStatus.DISCOVERED
    raw_data: dict[str, Any] = field(default_factory=dict)
    discovered_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.platform,
            "platform_id": self.platform_id,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "category": self.category,
            "budget_min": self.budget_min,
            "budget_max": self.budget_max,
            "currency": self.currency,
            "skills_required": self.skills_required,
            "deadline": self.deadline,
            "status": self.status.value,
            "raw_data": self.raw_data,
            "discovered_at": self.discovered_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Opportunity:
        return cls(
            id=d.get("id", uuid.uuid4().hex[:12]),
            platform=d.get("platform", ""),
            platform_id=d.get("platform_id", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
            url=d.get("url", ""),
            category=d.get("category", ""),
            budget_min=d.get("budget_min", 0.0),
            budget_max=d.get("budget_max", 0.0),
            currency=d.get("currency", "USD"),
            skills_required=d.get("skills_required", []),
            deadline=d.get("deadline", ""),
            status=OpportunityStatus(d.get("status", "discovered")),
            raw_data=d.get("raw_data", {}),
            discovered_at=d.get("discovered_at", ""),
            updated_at=d.get("updated_at", ""),
        )


# ─────────────────────────────────────────────
# Evaluation — feasibility assessment
# ─────────────────────────────────────────────


class FeasibilityVerdict(str, Enum):
    FEASIBLE = "feasible"
    PARTIAL = "partial"             # Some capabilities missing
    INFEASIBLE = "infeasible"


@dataclass
class Evaluation:
    """Can ALS actually do this opportunity?"""

    opportunity_id: str = ""
    verdict: FeasibilityVerdict = FeasibilityVerdict.INFEASIBLE
    confidence: float = 0.0         # 0.0-1.0
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    estimated_hours: float = 0.0
    estimated_cost_usd: float = 0.0
    reasoning: str = ""
    evaluated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "matched_skills": self.matched_skills,
            "missing_skills": self.missing_skills,
            "estimated_hours": self.estimated_hours,
            "estimated_cost_usd": self.estimated_cost_usd,
            "reasoning": self.reasoning,
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Evaluation:
        return cls(
            opportunity_id=d.get("opportunity_id", ""),
            verdict=FeasibilityVerdict(d.get("verdict", "infeasible")),
            confidence=d.get("confidence", 0.0),
            matched_skills=d.get("matched_skills", []),
            missing_skills=d.get("missing_skills", []),
            estimated_hours=d.get("estimated_hours", 0.0),
            estimated_cost_usd=d.get("estimated_cost_usd", 0.0),
            reasoning=d.get("reasoning", ""),
            evaluated_at=d.get("evaluated_at", ""),
        )


# ─────────────────────────────────────────────
# Bid — prepared response/proposal
# ─────────────────────────────────────────────


class BidStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass
class Bid:
    """Prepared proposal for an opportunity."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    opportunity_id: str = ""
    platform: str = ""
    title: str = ""
    proposal_text: str = ""
    price_usd: float = 0.0
    delivery_days: int = 0
    status: BidStatus = BidStatus.DRAFT
    project_id: str = ""            # Linked ALS project, if created
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    submitted_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "opportunity_id": self.opportunity_id,
            "platform": self.platform,
            "title": self.title,
            "proposal_text": self.proposal_text,
            "price_usd": self.price_usd,
            "delivery_days": self.delivery_days,
            "status": self.status.value,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "submitted_at": self.submitted_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Bid:
        return cls(
            id=d.get("id", uuid.uuid4().hex[:12]),
            opportunity_id=d.get("opportunity_id", ""),
            platform=d.get("platform", ""),
            title=d.get("title", ""),
            proposal_text=d.get("proposal_text", ""),
            price_usd=d.get("price_usd", 0.0),
            delivery_days=d.get("delivery_days", 0),
            status=BidStatus(d.get("status", "draft")),
            project_id=d.get("project_id", ""),
            created_at=d.get("created_at", ""),
            submitted_at=d.get("submitted_at", ""),
            metadata=d.get("metadata", {}),
        )
