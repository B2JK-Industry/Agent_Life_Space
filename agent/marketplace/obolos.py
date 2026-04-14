"""
Agent Life Space — Obolos.tech Marketplace Connector

First concrete connector. Normalizes the obolos.tech API
(marketplace catalog, API details, seller publish) into the
common Opportunity/Bid models.

All HTTP calls go through the existing ExternalGatewayService
which handles auth (AGENT_OBOLOS_WALLET_ADDRESS), rate limiting,
retries, and 402 payment flows.
"""

from __future__ import annotations

from typing import Any

import structlog

from agent.marketplace.models import (
    Bid,
    BidStatus,
    Evaluation,
    FeasibilityVerdict,
    Opportunity,
    OpportunityStatus,
)

logger = structlog.get_logger(__name__)

# Skills the ALS agent can realistically offer
_ALS_CAPABILITIES = frozenset({
    "code-review", "code-generation", "python", "api", "data-analysis",
    "text-generation", "summarization", "testing", "linting",
    "documentation", "web-scraping", "monitoring",
})


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
        """Fetch marketplace catalog via gateway capability route."""
        result = await gateway.call_api_via_capability(
            capability_id="marketplace_catalog_v1",
            provider_id="obolos.tech",
            resource="",
            method="GET",
            params={"category": category} if category else {},
        )
        if not result.get("ok"):
            logger.warning(
                "obolos_catalog_fetch_failed",
                error=result.get("error", "unknown"),
            )
            return []

        normalized = result.get("normalized_response", {})
        apis = normalized.get("apis", [])

        opportunities: list[Opportunity] = []
        for api in apis[:limit]:
            opp = self._normalize_api_to_opportunity(api)
            if opp:
                opportunities.append(opp)

        logger.info("obolos_catalog_fetched", count=len(opportunities))
        return opportunities

    async def fetch_opportunity_detail(
        self, gateway: Any, platform_id: str,
    ) -> Opportunity | None:
        """Fetch single API detail via marketplace_api_call capability."""
        result = await gateway.call_api_via_capability(
            capability_id="marketplace_api_call_v1",
            provider_id="obolos.tech",
            resource=platform_id,
            method="GET",
        )
        if not result.get("ok"):
            return None

        normalized = result.get("normalized_response", {})
        return self._normalize_api_to_opportunity(normalized, platform_id=platform_id)

    def evaluate_opportunity(
        self, opportunity: Opportunity, agent_capabilities: list[str],
    ) -> Evaluation:
        """Deterministic feasibility check."""
        caps = set(agent_capabilities) | _ALS_CAPABILITIES

        required = set(opportunity.skills_required)
        matched = required & caps
        missing = required - caps

        # Heuristic: if most required skills match, it's feasible
        if not required:
            # No explicit requirements — check category
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
            # Bid at 80% of max to be competitive
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
        self, gateway: Any, bid: Bid,
    ) -> dict[str, Any]:
        """Submit bid via seller_publish capability."""
        result = await gateway.call_api_via_capability(
            capability_id="seller_publish_v1",
            provider_id="obolos.tech",
            resource="",
            method="POST",
            payload={
                "name": bid.title,
                "description": bid.proposal_text,
                "price": bid.price_usd,
                "opportunity_id": bid.opportunity_id,
            },
        )
        if result.get("ok"):
            bid.status = BidStatus.SUBMITTED
            logger.info("obolos_bid_submitted", bid_id=bid.id, opportunity=bid.opportunity_id)
        else:
            logger.warning("obolos_bid_submit_failed", error=result.get("error", ""))

        return result

    # ─── Internal normalization ───

    def _normalize_api_to_opportunity(
        self, api: dict[str, Any], platform_id: str = "",
    ) -> Opportunity | None:
        """Normalize a single obolos.tech API listing to Opportunity."""
        slug = api.get("slug") or api.get("id") or platform_id
        if not slug:
            return None

        title = api.get("name") or api.get("title") or slug
        description = api.get("description", "")

        # Extract pricing
        price = api.get("price", 0)
        price_obj = api.get("pricing", {})
        if isinstance(price_obj, dict):
            price = price_obj.get("per_call", price_obj.get("price", price))

        # Extract tags/skills
        tags = api.get("tags", []) or api.get("categories", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        return Opportunity(
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
