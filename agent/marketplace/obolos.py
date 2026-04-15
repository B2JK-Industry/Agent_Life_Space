"""
Agent Life Space — Obolos.tech Marketplace Connector

First concrete connector. Normalizes the obolos.tech API
(marketplace catalog, API details) into the common
Opportunity/Bid models.

All HTTP calls go through the existing ExternalGatewayService
which handles auth (AGENT_OBOLOS_WALLET_ADDRESS), rate limiting,
retries, and 402 payment flows.

Scope:
- Discovery (catalog → slugs → detail for each)
- Evaluation (deterministic feasibility)
- Bid preparation (draft)
- Bid submission via marketplace_api_call_v1 POST to opportunity slug
  (seller_publish_v1 is for registering your own APIs, NOT for bidding)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from agent.marketplace.models import (
    Bid,
    BidStatus,
    Evaluation,
    FeasibilityVerdict,
    Opportunity,
    OpportunityStatus,
    stable_marketplace_id,
)

logger = structlog.get_logger(__name__)

# Skills the ALS agent can realistically offer
_ALS_CAPABILITIES = frozenset({
    "code-review", "code-generation", "python", "api", "data-analysis",
    "text-generation", "summarization", "testing", "linting",
    "documentation", "web-scraping", "monitoring",
})


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class ObolosConnector:
    """Connector for obolos.tech x402 marketplace."""

    @property
    def platform_id(self) -> str:
        return "obolos.tech"

    @property
    def display_name(self) -> str:
        return "Obolos.tech"

    async def fetch_opportunities(
        self, gateway: Any, *, category: str = "", limit: int = 20,
    ) -> list[Opportunity]:
        """Fetch marketplace catalog, then detail for each slug.

        Two-step flow:
        1. marketplace_catalog_v1 → normalized_response.slugs (list of IDs)
        2. For each slug: marketplace_api_call_v1 GET → response_json (full listing)
        """
        catalog = await gateway.call_api_via_capability(
            capability_id="marketplace_catalog_v1",
            provider_id="obolos.tech",
            resource="",
            method="GET",
            query_params={"category": category} if category else None,
        )
        if not catalog.get("ok"):
            logger.warning(
                "obolos_catalog_fetch_failed",
                error=catalog.get("error", "unknown"),
            )
            return []

        normalized = catalog.get("normalized_response", {})
        slugs = normalized.get("slugs", [])
        if not slugs:
            logger.info("obolos_catalog_empty")
            return []

        opportunities: list[Opportunity] = []
        for slug in slugs[:limit]:
            opp = await self.fetch_opportunity_detail(gateway, slug)
            if opp:
                opportunities.append(opp)

        logger.info("obolos_catalog_fetched", count=len(opportunities))
        return opportunities

    async def fetch_opportunity_detail(
        self, gateway: Any, platform_id: str,
    ) -> Opportunity | None:
        """Fetch single API detail via marketplace_api_call capability.

        Uses response_json (raw provider payload) because the gateway
        normalizer for this route only returns top_level_keys, not the
        full listing object.
        """
        result = await gateway.call_api_via_capability(
            capability_id="marketplace_api_call_v1",
            provider_id="obolos.tech",
            resource=platform_id,
            method="GET",
        )
        if not result.get("ok"):
            return None

        # Use raw response_json — normalized_response only has top_level_keys
        raw = result.get("response_json", {})
        return self._normalize_api_to_opportunity(raw, platform_id=platform_id)

    def evaluate_opportunity(
        self, opportunity: Opportunity, agent_capabilities: list[str],
    ) -> Evaluation:
        """Deterministic feasibility check."""
        caps = set(agent_capabilities) | _ALS_CAPABILITIES

        required = set(opportunity.skills_required)
        matched = required & caps
        missing = required - caps

        if not required:
            verdict = FeasibilityVerdict.PARTIAL
            confidence = 0.5
            reasoning = "No explicit skill requirements listed; manual review recommended."
        elif len(missing) == 0:
            verdict = FeasibilityVerdict.FEASIBLE
            confidence = min(0.9, 0.6 + 0.1 * len(matched))
            reasoning = f"All {len(required)} required skills matched."
        elif len(matched) >= len(required) * 0.6:
            verdict = FeasibilityVerdict.PARTIAL
            confidence = len(matched) / max(len(required), 1)
            reasoning = f"Matched {len(matched)}/{len(required)} skills. Missing: {', '.join(sorted(missing))}."
        else:
            verdict = FeasibilityVerdict.INFEASIBLE
            confidence = len(matched) / max(len(required), 1)
            reasoning = f"Only {len(matched)}/{len(required)} skills matched. Missing: {', '.join(sorted(missing))}."

        return Evaluation(
            opportunity_id=opportunity.id,
            verdict=verdict,
            confidence=round(confidence, 2),
            matched_skills=sorted(matched),
            missing_skills=sorted(missing),
            reasoning=reasoning,
        )

    def prepare_bid(
        self, opportunity: Opportunity, evaluation: Evaluation,
    ) -> Bid:
        """Draft a bid for a feasible opportunity."""
        price = opportunity.budget_min or 0.0
        if opportunity.budget_max > 0:
            price = round(opportunity.budget_max * 0.8, 2)

        proposal = (
            f"I can deliver this using my automated pipeline:\n"
            f"- Matched skills: {', '.join(evaluation.matched_skills) or 'general'}\n"
            f"- Execution: sandboxed build + verification + code review\n"
            f"- Confidence: {evaluation.confidence:.0%}"
        )

        return Bid(
            opportunity_id=opportunity.id,
            platform=self.platform_id,
            title=f"Bid: {opportunity.title[:80]}",
            proposal_text=proposal,
            price_usd=price,
            status=BidStatus.DRAFT,
        )

    async def submit_bid(
        self, gateway: Any, bid: Bid, opportunity: Opportunity | None = None,
    ) -> dict[str, Any]:
        """Submit bid via marketplace_api_call_v1 POST to opportunity slug.

        Uses the generic marketplace API call capability which POSTs to
        the opportunity's slug endpoint. This is the correct route —
        seller_publish_v1 is for registering YOUR OWN APIs, not for
        applying to existing opportunities.

        Requires: opportunity.platform_id (the slug to POST to).
        """
        if not opportunity or not opportunity.platform_id:
            return {
                "ok": False,
                "error": "Cannot submit: opportunity platform_id (slug) is required.",
            }

        result = await gateway.call_api_via_capability(
            capability_id="marketplace_api_call_v1",
            provider_id="obolos.tech",
            resource=opportunity.platform_id,
            method="POST",
            json_payload={
                "action": "bid",
                "title": bid.title,
                "description": bid.proposal_text,
                "price": bid.price_usd,
                "delivery_days": bid.delivery_days,
            },
        )
        if result.get("ok"):
            bid.status = BidStatus.SUBMITTED
            bid.submitted_at = datetime.now(UTC).isoformat()
            logger.info("obolos_bid_submitted", bid_id=bid.id, slug=opportunity.platform_id)
        else:
            logger.warning(
                "obolos_bid_submit_failed",
                bid_id=bid.id,
                error=result.get("error", ""),
            )

        return result

    # ─── Listings (documented provider-side work) ───

    async def list_listings(
        self, gateway: Any, *, limit: int = 20,
    ) -> list[Opportunity]:
        """GET /api/listings — browse available work listings."""
        result = await gateway.call_api_via_capability(
            capability_id="listings_list_v1",
            provider_id="obolos.tech",
            resource="",
            method="GET",
        )
        if not result.get("ok"):
            logger.warning("obolos_listings_fetch_failed", error=result.get("error", ""))
            return []

        normalized = result.get("normalized_response", {})
        raw_listings = normalized.get("listings", [])

        opportunities: list[Opportunity] = []
        for item in raw_listings[:limit]:
            opp = self._normalize_listing_to_opportunity(item)
            if opp:
                opportunities.append(opp)
        logger.info("obolos_listings_fetched", count=len(opportunities))
        return opportunities

    async def get_listing(
        self, gateway: Any, listing_id: str,
    ) -> Opportunity | None:
        """GET /api/listings/{id} — full listing detail."""
        result = await gateway.call_api_via_capability(
            capability_id="listings_detail_v1",
            provider_id="obolos.tech",
            resource=listing_id,
            method="GET",
        )
        if not result.get("ok"):
            return None
        normalized = result.get("normalized_response", {})
        listing_data = normalized.get("listing", {})
        return self._normalize_listing_to_opportunity(listing_data, listing_id=listing_id)

    async def submit_listing_bid(
        self, gateway: Any, listing_id: str, bid: Bid,
    ) -> dict[str, Any]:
        """POST /api/listings/{id}/bid — submit bid to a work listing."""
        result = await gateway.call_api_via_capability(
            capability_id="listings_bid_v1",
            provider_id="obolos.tech",
            resource=listing_id,
            method="POST",
            json_payload={
                "price": bid.price_usd,
                "delivery_time": bid.delivery_days,
                "message": bid.proposal_text,
            },
        )
        if result.get("ok"):
            bid.status = BidStatus.SUBMITTED
            bid.submitted_at = datetime.now(UTC).isoformat()
            normalized = result.get("normalized_response", {})
            bid.metadata["platform_bid_id"] = normalized.get("bid_id", "")
            logger.info("obolos_listing_bid_submitted", listing_id=listing_id)
        else:
            logger.warning("obolos_listing_bid_failed", error=result.get("error", ""))
        return result

    # ─── Jobs (ERC-8183 ACP) ───

    async def list_jobs(
        self, gateway: Any, *, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """GET /api/jobs — list accepted work."""
        result = await gateway.call_api_via_capability(
            capability_id="jobs_list_v1",
            provider_id="obolos.tech",
            resource="",
            method="GET",
        )
        if not result.get("ok"):
            logger.warning("obolos_jobs_fetch_failed", error=result.get("error", ""))
            return []
        normalized = result.get("normalized_response", {})
        return normalized.get("jobs", [])[:limit]

    async def get_job(
        self, gateway: Any, job_id: str,
    ) -> dict[str, Any] | None:
        """GET /api/jobs/{id} — job detail."""
        result = await gateway.call_api_via_capability(
            capability_id="jobs_detail_v1",
            provider_id="obolos.tech",
            resource=job_id,
            method="GET",
        )
        if not result.get("ok"):
            return None
        return result.get("normalized_response", {}).get("job", {})

    async def submit_job_work(
        self, gateway: Any, job_id: str, *,
        summary: str = "", proof: str = "", artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """POST /api/jobs/{id}/submit — submit completed work."""
        payload: dict[str, Any] = {}
        if summary:
            payload["result"] = summary
        if proof:
            payload["proof"] = proof
        if artifact_ids:
            payload["artifact_ids"] = artifact_ids

        result = await gateway.call_api_via_capability(
            capability_id="jobs_submit_v1",
            provider_id="obolos.tech",
            resource=job_id,
            method="POST",
            json_payload=payload,
        )
        if result.get("ok"):
            logger.info("obolos_job_work_submitted", job_id=job_id)
        else:
            logger.warning("obolos_job_submit_failed", job_id=job_id, error=result.get("error", ""))
        return result

    async def complete_job(
        self, gateway: Any, job_id: str, *, notes: str = "",
    ) -> dict[str, Any]:
        """POST /api/jobs/{id}/complete — mark job as completed."""
        result = await gateway.call_api_via_capability(
            capability_id="jobs_complete_v1",
            provider_id="obolos.tech",
            resource=job_id,
            method="POST",
            json_payload={"notes": notes} if notes else None,
        )
        if result.get("ok"):
            logger.info("obolos_job_completed", job_id=job_id)
        return result

    async def reject_job(
        self, gateway: Any, job_id: str, *, reason: str = "",
    ) -> dict[str, Any]:
        """POST /api/jobs/{id}/reject — reject/decline a job."""
        result = await gateway.call_api_via_capability(
            capability_id="jobs_reject_v1",
            provider_id="obolos.tech",
            resource=job_id,
            method="POST",
            json_payload={"reason": reason} if reason else None,
        )
        if result.get("ok"):
            logger.info("obolos_job_rejected", job_id=job_id, reason=reason[:50])
        return result

    async def get_reputation(
        self, gateway: Any, agent_id: str,
    ) -> dict[str, Any] | None:
        """GET /api/anp/reputation/{agentId} — trust/reputation check."""
        result = await gateway.call_api_via_capability(
            capability_id="anp_reputation_v1",
            provider_id="obolos.tech",
            resource=agent_id,
            method="GET",
        )
        if not result.get("ok"):
            return None
        return result.get("normalized_response", {})

    # ─── Internal normalization ───

    def _normalize_listing_to_opportunity(
        self, listing: dict[str, Any], listing_id: str = "",
    ) -> Opportunity | None:
        """Normalize a work listing to Opportunity model."""
        lid = str(listing.get("id", listing.get("_id", listing_id)))
        if not lid:
            return None
        title = listing.get("title") or listing.get("name") or f"Listing {lid[:8]}"
        description = listing.get("description", "")
        budget_min = _to_float(listing.get("min_budget"))
        budget_max = _to_float(listing.get("max_budget"))
        budget = listing.get("budget", listing.get("price", 0))
        if isinstance(budget, dict):
            budget = budget.get("amount", budget.get("max", 0))
        budget_fallback = _to_float(budget)
        if not budget_min:
            budget_min = budget_fallback
        if not budget_max:
            budget_max = budget_fallback or budget_min
        tags = listing.get("skills", listing.get("tags", []))
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        return Opportunity(
            id=stable_marketplace_id("obolos.tech", lid),
            platform="obolos.tech",
            platform_id=str(lid),
            title=str(title)[:200],
            description=str(description)[:500],
            url=f"https://obolos.tech/api/listings/{lid}",
            category="listing",
            budget_min=budget_min,
            budget_max=budget_max,
            currency=str(listing.get("currency", "USD")),
            skills_required=tags[:10] if isinstance(tags, list) else [],
            deadline=str(listing.get("deadline", "")),
            status=OpportunityStatus.DISCOVERED,
            raw_data=listing,
        )

    def _normalize_api_to_opportunity(
        self, api: dict[str, Any], platform_id: str = "",
    ) -> Opportunity | None:
        """Normalize a single obolos.tech API listing to Opportunity."""
        slug = api.get("slug") or api.get("id") or platform_id
        if not slug:
            return None

        title = api.get("name") or api.get("title") or slug
        description = api.get("description", "")

        price = api.get("price", 0)
        price_obj = api.get("pricing", {})
        if isinstance(price_obj, dict):
            price = price_obj.get("per_call", price_obj.get("price", price))

        tags = api.get("tags", []) or api.get("categories", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        return Opportunity(
            id=stable_marketplace_id("obolos.tech", str(slug)),
            platform="obolos.tech",
            platform_id=slug,
            title=str(title),
            description=str(description)[:500],
            url=f"https://obolos.tech/api/{slug}",
            category=tags[0] if tags else "",
            budget_min=float(price) if price else 0.0,
            budget_max=float(price) if price else 0.0,
            currency="credits",
            skills_required=tags,
            status=OpportunityStatus.DISCOVERED,
            raw_data=api,
        )
