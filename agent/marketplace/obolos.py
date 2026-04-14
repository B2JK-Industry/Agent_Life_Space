"""
Agent Life Space — Obolos.tech Marketplace Connector

First concrete connector. Normalizes the obolos.tech API
(marketplace catalog, API details) into the common
Opportunity/Bid models.

All HTTP calls go through the existing ExternalGatewayService
which handles auth (AGENT_OBOLOS_WALLET_ADDRESS), rate limiting,
retries, and 402 payment flows.

Phase 1 scope:
- Discovery (catalog → slugs → detail for each)
- Evaluation (deterministic feasibility)
- Bid preparation (draft only)
- Bid submission is NOT supported in Phase 1:
  seller_publish_v1 registers seller APIs, it does not submit
  bids to opportunities. A dedicated bid/apply capability would
  need to be added to the obolos.tech gateway routes first.
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
        self, gateway: Any, bid: Bid,
    ) -> dict[str, Any]:
        """NOT SUPPORTED in Phase 1.

        seller_publish_v1 registers seller APIs on the marketplace.
        It does not submit bids/applications to existing opportunities.
        A dedicated bid capability route would need to be added first.
        """
        return {
            "ok": False,
            "error": (
                "Bid submission is not yet supported for obolos.tech. "
                "The existing seller_publish_v1 capability registers seller APIs, "
                "not opportunity bids. A dedicated bid/apply route is needed."
            ),
        }

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

        price = api.get("price", 0)
        price_obj = api.get("pricing", {})
        if isinstance(price_obj, dict):
            price = price_obj.get("per_call", price_obj.get("price", price))

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
