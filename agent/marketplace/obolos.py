"""
Agent Life Space — Obolos.tech Marketplace Connector

CLI-first connector: uses the `obolos` CLI binary when available,
falls back to REST API via ExternalGatewayService when CLI is missing.

CLI advantages (per skill.md v4.0.0):
- Stable --json contract
- Handles x402/USDC payments automatically
- EIP-712 signing for ANP
- Same surface as MCP server

REST fallback advantages:
- Works without npm/node
- Uses existing gateway auth/rate-limit/audit infrastructure
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
from agent.marketplace.obolos_cli import (
    cli_anp_bid,
    cli_anp_list,
    cli_available,
    cli_job_complete,
    cli_job_info,
    cli_job_list,
    cli_job_reject,
    cli_job_submit,
    cli_listing_bid,
    cli_listing_create,
    cli_listing_info,
    cli_listing_list,
    cli_reputation_check,
    cli_search,
)

logger = structlog.get_logger(__name__)

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


def _use_cli() -> bool:
    """Decide whether to use CLI (preferred) or REST fallback."""
    return cli_available()


class ObolosConnector:
    """Connector for obolos.tech x402 marketplace.

    Strategy: CLI-first, REST gateway fallback.
    Each method tries CLI, catches failure, falls back to gateway.
    """

    @property
    def platform_id(self) -> str:
        return "obolos.tech"

    @property
    def display_name(self) -> str:
        return "Obolos.tech"

    @property
    def transport(self) -> str:
        return "cli" if _use_cli() else "gateway"

    # ─── Create listing (offer services) ───

    async def create_listing(
        self,
        *,
        title: str,
        description: str = "",
        max_budget: float = 0,
        deadline: str = "7d",
    ) -> dict[str, Any]:
        """Create a work listing on obolos.tech via CLI."""
        if not _use_cli():
            return {"ok": False, "error": "Obolos CLI not available. Install with: npm install -g @obolos_tech/cli"}
        result = await cli_listing_create(
            title=title, description=description,
            max_budget=max_budget, deadline=deadline,
        )
        if result["ok"]:
            logger.info("obolos_listing_created", title=title[:50], id=result["data"].get("id", ""))
        return result

    # ─── API Marketplace (discovery) ───

    async def fetch_opportunities(
        self, gateway: Any, *, category: str = "", limit: int = 20,
    ) -> list[Opportunity]:
        """Search API marketplace. CLI: obolos search; fallback: gateway catalog."""
        if _use_cli():
            result = await cli_search(category)
            if result["ok"]:
                apis = result["data"].get("apis", [])
                return [
                    opp for api in apis[:limit]
                    if (opp := self._normalize_api_to_opportunity(api)) is not None
                ]
            logger.warning("obolos_cli_search_failed", error=result.get("error", ""))

        # REST fallback
        catalog = await gateway.call_api_via_capability(
            capability_id="marketplace_catalog_v1",
            provider_id="obolos.tech",
            resource="",
            method="GET",
            query_params={"category": category} if category else None,
        )
        if not catalog.get("ok"):
            return []
        slugs = catalog.get("normalized_response", {}).get("slugs", [])
        opportunities: list[Opportunity] = []
        for slug in slugs[:limit]:
            opp = await self.fetch_opportunity_detail(gateway, slug)
            if opp:
                opportunities.append(opp)
        return opportunities

    async def fetch_opportunity_detail(
        self, gateway: Any, platform_id: str,
    ) -> Opportunity | None:
        """Fetch single API detail. CLI not applicable here; uses gateway."""
        result = await gateway.call_api_via_capability(
            capability_id="marketplace_api_call_v1",
            provider_id="obolos.tech",
            resource=platform_id,
            method="GET",
        )
        if not result.get("ok"):
            return None
        raw = result.get("response_json", {})
        return self._normalize_api_to_opportunity(raw, platform_id=platform_id)

    # ─── ANP Listings (Agent Negotiation Protocol — wider pool) ───

    async def list_anp_listings(
        self, gateway: Any, *, limit: int = 20,
    ) -> list[Opportunity]:
        """Browse ANP listings. CLI: obolos anp list; no REST fallback."""
        if not _use_cli():
            return []
        result = await cli_anp_list()
        if not result["ok"]:
            logger.warning("obolos_cli_anp_list_failed", error=result.get("error", ""))
            return []
        raw_listings = result["data"].get("listings", [])
        opps: list[Opportunity] = []
        for item in raw_listings[:limit]:
            if str(item.get("status", "")).lower() != "open":
                continue
            opp = self._normalize_anp_to_opportunity(item)
            if opp:
                opps.append(opp)
        return opps

    async def submit_anp_bid(
        self, listing_cid: str, *, price: float, message: str = "",
    ) -> dict[str, Any]:
        """Submit ANP bid via CLI. Returns CLI result dict."""
        if not _use_cli():
            return {"ok": False, "error": "Obolos CLI not available for ANP bids"}
        return await cli_anp_bid(listing_cid, price=price, message=message)

    def _normalize_anp_to_opportunity(self, item: dict[str, Any]) -> Opportunity | None:
        """Normalize an ANP listing to Opportunity model."""
        cid = str(item.get("cid", ""))
        if not cid:
            return None
        title = str(item.get("title", f"ANP {cid[:12]}"))[:200]
        description = str(item.get("description", ""))[:500]
        min_price = _to_float(item.get("min_price", 0))
        max_price = _to_float(item.get("max_price", 0))
        return Opportunity(
            id=stable_marketplace_id("obolos.tech", cid, kind="anp"),
            platform="obolos.tech",
            platform_id=cid,
            title=title,
            description=description,
            url=f"https://obolos.tech/anp/{cid[:16]}",
            category="listing",
            budget_min=min_price,
            budget_max=max_price or min_price,
            currency="USDC",
            skills_required=[],
            status=OpportunityStatus.DISCOVERED,
            raw_data=item,
        )

    # ─── Listings (provider-side work) ───

    async def list_listings(
        self, gateway: Any, *, limit: int = 20,
    ) -> list[Opportunity]:
        """Browse work listings. CLI: obolos listing list; fallback: gateway."""
        if _use_cli():
            result = await cli_listing_list(status="open")
            if result["ok"]:
                raw_listings = result["data"].get("listings", [])
                return [
                    opp for item in raw_listings[:limit]
                    if (opp := self._normalize_listing_to_opportunity(item)) is not None
                ]
            logger.warning("obolos_cli_listing_list_failed", error=result.get("error", ""))

        # REST fallback
        result = await gateway.call_api_via_capability(
            capability_id="listings_list_v1",
            provider_id="obolos.tech",
            resource="",
            method="GET",
        )
        if not result.get("ok"):
            return []
        raw_listings = result.get("normalized_response", {}).get("listings", [])
        return [
            opp for item in raw_listings[:limit]
            if (opp := self._normalize_listing_to_opportunity(item)) is not None
        ]

    async def get_listing(
        self, gateway: Any, listing_id: str,
    ) -> Opportunity | None:
        """Listing detail. CLI: obolos listing info; fallback: gateway."""
        if _use_cli():
            result = await cli_listing_info(listing_id)
            if result["ok"]:
                return self._normalize_listing_to_opportunity(
                    result["data"], listing_id=listing_id,
                )
            logger.warning("obolos_cli_listing_info_failed", error=result.get("error", ""))

        # REST fallback
        result = await gateway.call_api_via_capability(
            capability_id="listings_detail_v1",
            provider_id="obolos.tech",
            resource=listing_id,
            method="GET",
        )
        if not result.get("ok"):
            return None
        return self._normalize_listing_to_opportunity(
            result.get("normalized_response", {}).get("listing", {}),
            listing_id=listing_id,
        )

    async def submit_listing_bid(
        self, gateway: Any, listing_id: str, bid: Bid,
    ) -> dict[str, Any]:
        """Submit bid on listing. CLI: obolos listing bid; fallback: gateway REST."""
        if _use_cli():
            result = await cli_listing_bid(
                listing_id,
                price=bid.price_usd,
                delivery_hours=bid.delivery_days * 24 if bid.delivery_days else 0,
                message=bid.proposal_text,
            )
            if result["ok"]:
                bid.status = BidStatus.SUBMITTED
                bid.submitted_at = datetime.now(UTC).isoformat()
                bid.metadata["platform_bid_id"] = result["data"].get("id", "")
                bid.metadata["transport"] = "cli"
                logger.info("obolos_listing_bid_submitted_cli", listing_id=listing_id)
                return {"ok": True, "normalized_response": result["data"], "transport": "cli"}
            logger.warning("obolos_cli_listing_bid_failed", error=result.get("error", ""))
            # Fall through to REST

        # REST fallback — Obolos requires string values
        payload = {
            "price": str(bid.price_usd),
            "delivery_time": str(bid.delivery_days),
            "message": bid.proposal_text,
        }
        result = await gateway.call_api_via_capability(
            capability_id="listings_bid_v1",
            provider_id="obolos.tech",
            resource=listing_id,
            method="POST",
            json_payload=payload,
        )
        if result.get("ok"):
            bid.status = BidStatus.SUBMITTED
            bid.submitted_at = datetime.now(UTC).isoformat()
            normalized = result.get("normalized_response", {})
            bid.metadata["platform_bid_id"] = normalized.get("bid_id", "")
            bid.metadata["transport"] = "gateway"
            logger.info("obolos_listing_bid_submitted_gateway", listing_id=listing_id)
        else:
            logger.warning("obolos_listing_bid_failed", error=result.get("error", ""))
        return result

    # ─── Jobs (ERC-8183 ACP) ───

    async def list_jobs(
        self, gateway: Any, *, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List jobs. CLI: obolos job list; fallback: gateway."""
        if _use_cli():
            result = await cli_job_list()
            if result["ok"]:
                return result["data"].get("jobs", [])[:limit]
            logger.warning("obolos_cli_job_list_failed", error=result.get("error", ""))

        result = await gateway.call_api_via_capability(
            capability_id="jobs_list_v1",
            provider_id="obolos.tech",
            resource="",
            method="GET",
        )
        if not result.get("ok"):
            return []
        return result.get("normalized_response", {}).get("jobs", [])[:limit]

    async def get_job(
        self, gateway: Any, job_id: str,
    ) -> dict[str, Any] | None:
        """Job detail. CLI: obolos job info; fallback: gateway."""
        if _use_cli():
            result = await cli_job_info(job_id)
            if result["ok"]:
                return result["data"]
            logger.warning("obolos_cli_job_info_failed", error=result.get("error", ""))

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
        """Submit deliverable. CLI: obolos job submit; fallback: gateway."""
        deliverable = summary or "Work completed."

        if _use_cli():
            result = await cli_job_submit(job_id, deliverable=deliverable)
            if result["ok"]:
                logger.info("obolos_job_work_submitted_cli", job_id=job_id)
                return {"ok": True, "normalized_response": result["data"], "transport": "cli"}
            logger.warning("obolos_cli_job_submit_failed", error=result.get("error", ""))

        # REST fallback
        payload: dict[str, Any] = {"deliverable": deliverable}
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
            logger.info("obolos_job_work_submitted_gateway", job_id=job_id)
        else:
            logger.warning("obolos_job_submit_failed", job_id=job_id, error=result.get("error", ""))
        return result

    async def complete_job(
        self, gateway: Any, job_id: str, *, notes: str = "",
    ) -> dict[str, Any]:
        """Mark completed. CLI: obolos job complete; fallback: gateway."""
        if _use_cli():
            result = await cli_job_complete(job_id, reason=notes)
            if result["ok"]:
                logger.info("obolos_job_completed_cli", job_id=job_id)
                return {"ok": True, "normalized_response": result["data"], "transport": "cli"}
            logger.warning("obolos_cli_job_complete_failed", error=result.get("error", ""))

        result = await gateway.call_api_via_capability(
            capability_id="jobs_complete_v1",
            provider_id="obolos.tech",
            resource=job_id,
            method="POST",
            json_payload={"notes": notes} if notes else None,
        )
        if result.get("ok"):
            logger.info("obolos_job_completed_gateway", job_id=job_id)
        return result

    async def reject_job(
        self, gateway: Any, job_id: str, *, reason: str = "",
    ) -> dict[str, Any]:
        """Reject job. CLI: obolos job reject; fallback: gateway."""
        if _use_cli():
            result = await cli_job_reject(job_id, reason=reason)
            if result["ok"]:
                logger.info("obolos_job_rejected_cli", job_id=job_id)
                return {"ok": True, "normalized_response": result["data"], "transport": "cli"}
            logger.warning("obolos_cli_job_reject_failed", error=result.get("error", ""))

        result = await gateway.call_api_via_capability(
            capability_id="jobs_reject_v1",
            provider_id="obolos.tech",
            resource=job_id,
            method="POST",
            json_payload={"reason": reason} if reason else None,
        )
        if result.get("ok"):
            logger.info("obolos_job_rejected_gateway", job_id=job_id)
        return result

    async def get_reputation(
        self, gateway: Any, agent_id: str,
    ) -> dict[str, Any] | None:
        """Reputation check. CLI: obolos reputation check; fallback: gateway."""
        if _use_cli():
            result = await cli_reputation_check(agent_id)
            if result["ok"]:
                return result["data"]
            logger.warning("obolos_cli_reputation_failed", error=result.get("error", ""))

        result = await gateway.call_api_via_capability(
            capability_id="anp_reputation_v1",
            provider_id="obolos.tech",
            resource=agent_id,
            method="GET",
        )
        if not result.get("ok"):
            return None
        return result.get("normalized_response", {})

    # ─── Evaluation (local, no network) ───

    def evaluate_opportunity(
        self, opportunity: Opportunity, agent_capabilities: list[str],
    ) -> Evaluation:
        """Deterministic feasibility check — no CLI/network needed."""
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
        """Draft a bid — local only."""
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

    # Legacy generic submit (not used for listings)
    async def submit_bid(
        self, gateway: Any, bid: Bid, opportunity: Opportunity | None = None,
    ) -> dict[str, Any]:
        """Generic submit — only for non-listing opportunities (API marketplace)."""
        if not opportunity or not opportunity.platform_id:
            return {"ok": False, "error": "Cannot submit: opportunity platform_id is required."}
        return {"ok": False, "error": "Generic API marketplace bidding is not supported. Use listings."}

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
        """Normalize a single obolos.tech API marketplace entry."""
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
