"""
Agent Life Space — Marketplace Service

Orchestrates the earning workflow:
    discover → evaluate → bid → engage → deliver

Delegates platform-specific work to connectors.
Reuses existing ALS infrastructure:
    - ExternalGatewayService for HTTP calls
    - ProjectManager for tracking engagements
    - ApprovalQueue for bid submission gating
    - FinanceTracker for revenue tracking
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiosqlite
import orjson
import structlog

from agent.marketplace.connectors import ConnectorRegistry
from agent.marketplace.models import (
    Bid,
    BidStatus,
    Evaluation,
    FeasibilityVerdict,
    JobOutcome,
    JobOutcomeStatus,
    Opportunity,
    OpportunityStatus,
)

logger = structlog.get_logger(__name__)


class MarketplaceService:
    """Orchestrates marketplace earning workflows."""

    def __init__(
        self,
        gateway: Any = None,
        projects: Any = None,
        approval_queue: Any = None,
        db_path: str = "",
    ) -> None:
        self._gateway = gateway
        self._projects = projects
        self._approval_queue = approval_queue
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._registry = ConnectorRegistry()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        if self._db_path:
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS opportunities (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS bids (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS job_outcomes (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    external_job_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # Indexes for hot-path lookups (auto-scout, dedup, terminal-state checks)
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_opp_platform ON opportunities(platform)",
            )
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status)",
            )
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_bids_opp ON bids(opportunity_id)",
            )
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_bids_status ON bids(status)",
            )
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcomes_job ON job_outcomes(external_job_id)",
            )
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcomes_status ON job_outcomes(status)",
            )
            await self._db.commit()
        self._initialized = True
        logger.info("marketplace_service_initialized", platforms=self._registry.list_platforms())

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def registry(self) -> ConnectorRegistry:
        return self._registry

    # ─── Discovery ───

    async def discover(
        self, *, platform: str = "", category: str = "", limit: int = 20,
    ) -> list[Opportunity]:
        """Fetch opportunities from one or all platforms.

        For Obolos-like connectors, discovery includes both generic marketplace
        opportunities and work listings so operators do not get a false-empty
        result when only the listings surface is available.
        """
        all_opps: dict[str, Opportunity] = {}

        connectors = (
            [self._registry.get(platform)] if platform
            else self._registry.all()
        )

        for connector in connectors:
            if connector is None:
                continue
            try:
                opps = await connector.fetch_opportunities(
                    self._gateway, category=category, limit=limit,
                )
                for opp in opps:
                    await self._persist_opportunity(opp)
                    all_opps[opp.id] = opp
            except Exception:
                logger.warning("marketplace_discover_error", platform=getattr(connector, "platform_id", "?"))
            if hasattr(connector, "list_listings"):
                try:
                    listings = await connector.list_listings(self._gateway, limit=limit)
                    for opp in listings:
                        await self._persist_opportunity(opp)
                        all_opps[opp.id] = opp
                except Exception:
                    logger.warning(
                        "marketplace_listings_discover_error",
                        platform=getattr(connector, "platform_id", "?"),
                    )

        discovered = list(all_opps.values())[:limit]
        logger.info("marketplace_discover", total=len(discovered))
        return discovered

    async def get_opportunity(self, opportunity_id: str) -> Opportunity | None:
        """Get a persisted opportunity by exact ID or unique short prefix."""
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT data FROM opportunities WHERE id = ?", (opportunity_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return Opportunity.from_dict(orjson.loads(row[0]))
        matches: list[Opportunity] = []
        async with self._db.execute(
            "SELECT data FROM opportunities ORDER BY rowid DESC",
        ) as cur:
            rows = await cur.fetchall()
        for raw_row in rows:
            opp = Opportunity.from_dict(orjson.loads(raw_row[0]))
            if (
                opp.id.startswith(opportunity_id)
                or opp.platform_id == opportunity_id
                or opp.platform_id.startswith(opportunity_id)
            ):
                matches.append(opp)
        if len(matches) == 1:
            return matches[0]
        return None

    async def list_opportunities(
        self, *, status: OpportunityStatus | None = None, limit: int = 20,
    ) -> list[Opportunity]:
        """List persisted opportunities."""
        if not self._db:
            return []
        query = "SELECT data FROM opportunities"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status.value)
        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        async with self._db.execute(query, params) as cur:
            rows = await cur.fetchall()
        return [Opportunity.from_dict(orjson.loads(r[0])) for r in rows]

    # ─── Evaluation ───

    def evaluate(
        self, opportunity: Opportunity, agent_capabilities: list[str] | None = None,
    ) -> Evaluation:
        """Evaluate feasibility of an opportunity."""
        connector = self._registry.get(opportunity.platform)
        if not connector:
            return Evaluation(
                opportunity_id=opportunity.id,
                verdict=FeasibilityVerdict.INFEASIBLE,
                reasoning=f"No connector for platform: {opportunity.platform}",
            )
        return connector.evaluate_opportunity(opportunity, agent_capabilities or [])

    # ─── Client mode: hire others by creating listings ───

    async def create_listing(
        self,
        *,
        platform: str = "obolos.tech",
        title: str,
        description: str = "",
        max_budget: float = 0.0,
        deadline: str = "7d",
        approval_id: str = "",
    ) -> dict[str, Any]:
        """Create a paid work listing — John acts as client, hires others.

        Spending money requires owner approval (FINANCE category).
        Two-phase flow:
        1. Without approval_id → propose approval, return pending status
        2. With approval_id → check status, execute if approved

        Same-wallet constraint: John cannot bid on his own listings — Obolos
        rejects same wallet for client and worker. This method does NOT prevent
        creation, but the bidding side will refuse self-bidding.
        """
        connector = self._registry.get(platform)
        if not connector or not hasattr(connector, "create_listing"):
            return {"ok": False, "error": f"No create-listing support for {platform}"}

        if not title.strip():
            return {"ok": False, "error": "Listing title is required."}
        if max_budget <= 0:
            return {"ok": False, "error": "Listing must have a positive max_budget."}

        # ── Phase 2: approval ID provided → check status, execute if approved ──
        if approval_id and self._approval_queue:
            req = self._approval_queue.get_request(approval_id)
            if req is None:
                return {"ok": False, "error": f"Approval {approval_id} not found."}
            status = req.get("status", "")
            if status in ("denied",):
                return {"ok": False, "error": f"Approval denied: {req.get('denial_reason', 'no reason')}"}
            if status in ("expired",):
                return {"ok": False, "error": "Approval expired. Re-propose."}
            if status not in ("approved", "executed"):
                return {
                    "ok": False,
                    "pending_approval": True,
                    "approval_id": approval_id,
                    "message": f"Approval still pending. Use `/queue approve {approval_id}`",
                }
            # Approved → fall through to execute

        # ── Phase 1: no approval ID → propose approval, return pending ──
        elif self._approval_queue:
            from agent.core.approval import ApprovalCategory
            proposal = self._approval_queue.propose(
                category=ApprovalCategory.FINANCE,
                description=f"Create paid Obolos listing: {title[:60]} (budget ${max_budget:.2f})",
                risk_level="high",
                reason=f"John as client: hire others to do work. Spends up to ${max_budget:.2f} USDC.",
                proposed_by="marketplace_service.create_listing",
                context={
                    "platform": platform,
                    "title": title,
                    "description": description[:200],
                    "max_budget": max_budget,
                    "deadline": deadline,
                },
            )
            return {
                "ok": False,
                "pending_approval": True,
                "approval_id": proposal.id,
                "message": (
                    f"Listing creation requires owner approval (commits up to "
                    f"${max_budget:.2f} USDC). Use `/queue approve {proposal.id}` "
                    f"then re-run `/marketplace create-listing` with --approval-id={proposal.id}"
                ),
            }

        # ── Execute: approved or no approval queue ──
        result = await connector.create_listing(
            title=title, description=description,
            max_budget=max_budget, deadline=deadline,
        )
        if result.get("ok") and approval_id and self._approval_queue:
            self._approval_queue.mark_executed(approval_id)
        if result.get("ok"):
            logger.info("marketplace_listing_created", platform=platform, title=title[:50],
                        budget=max_budget)
        return result

    # ─── Bidding ───

    def prepare_bid(self, opportunity: Opportunity, evaluation: Evaluation) -> Bid:
        """Prepare a bid draft for a feasible opportunity."""
        connector = self._registry.get(opportunity.platform)
        if not connector:
            return Bid(
                opportunity_id=opportunity.id,
                platform=opportunity.platform,
                title=f"No connector for {opportunity.platform}",
                status=BidStatus.DRAFT,
            )
        bid = connector.prepare_bid(opportunity, evaluation)
        return bid

    def _resolve_my_wallets(self) -> set[str]:
        """Resolve all of John's identities/addresses on Obolos (lowercase set).

        John has multiple representations of the same identity:
        - ANP id (e.g. "als-john-b2jk") used in x-wallet-address auth header
        - EVM address (e.g. "0x...") used as listing client_address on-chain

        Both come from the vault. Either one matching a listing's creator
        means "this is my listing → cannot bid on it (same-wallet rule)".

        The vault lookup must be a synchronous callable. Async lookups (e.g.
        AsyncMock in tests) are intentionally ignored to avoid unawaited
        coroutines — production gateway always exposes a sync `_secret_lookup`.
        """
        import inspect
        import os
        cached = getattr(self, "_my_wallets_cached", None)
        if cached is not None:
            return cached

        ids: set[str] = set()

        # Source 1: env override (rarely set in production)
        env_val = (os.environ.get("AGENT_OBOLOS_WALLET_ADDRESS", "") or "").strip()
        if env_val:
            ids.add(env_val.lower())

        # Source 2/3: vault — pull all known representations of the identity
        if self._gateway is not None:
            lookup = getattr(self._gateway, "_secret_lookup", None)
            # Accept only sync callables. AsyncMock and async helpers would
            # return coroutines that never get awaited from this sync method.
            if callable(lookup) and not inspect.iscoroutinefunction(lookup):
                for key in (
                    "obolos.tech.wallet_address",   # ANP id (auth header)
                    "obolos.tech.client_address",   # explicit EVM, if set
                    "ETH_ADDRESS",                  # John's vault EVM
                ):
                    try:
                        value = lookup(key)
                    except Exception:
                        continue
                    # Defense-in-depth: if the lookup unexpectedly returned a
                    # coroutine (e.g. dynamic mock), close it instead of leaking.
                    if inspect.iscoroutine(value):
                        value.close()
                        continue
                    text = str(value or "").strip()
                    if text:
                        ids.add(text.lower())

        self._my_wallets_cached = ids
        return ids

    # Backwards compat — single-wallet API kept for any external callers/tests.
    def _resolve_my_wallet(self) -> str:
        wallets = self._resolve_my_wallets()
        return next(iter(wallets), "") if wallets else ""

    def get_listing_bid_eligibility(self, opportunity: Opportunity) -> tuple[bool, str]:
        """Return whether a listing is currently biddable on the platform."""
        if not opportunity.is_listing:
            return False, (
                "This opportunity is an API marketplace item, not a work listing. "
                "Provider bids are only supported for work listings. "
                "Use `/marketplace listings` to find biddable work."
            )
        market_status = str(opportunity.raw_data.get("status", "")).strip().lower()
        if market_status and market_status != "open":
            return False, f"Listing is not open for bids (platform status: {market_status})."

        # Same-wallet self-bidding check — match against any of John's known
        # identities (ANP id + EVM address, both stored in vault).
        my_wallets = self._resolve_my_wallets()
        if my_wallets:
            for key in ("creator_wallet", "creator_address", "client_wallet",
                        "client_address", "owner_wallet", "owner_address"):
                creator = str(opportunity.raw_data.get(key, "") or "").strip().lower()
                if creator and creator in my_wallets:
                    return False, "Cannot bid on your own listing (same wallet)."
        return True, ""

    async def submit_bid(self, bid: Bid) -> dict[str, Any]:
        """Submit bid to platform. Approval-gated, persisted, audited.

        State machine:
        - DRAFT + approval_queue → propose approval, bid → READY
        - READY + approval pending → return "still pending"
        - READY + approval granted → execute gateway call → SUBMITTED
        - READY + approval denied/expired → FAILED
        - DRAFT + no approval_queue → execute directly
        - SUBMITTED → reject (already done)
        """
        connector = self._registry.get(bid.platform)
        if not connector:
            return {"ok": False, "error": f"No connector for {bid.platform}"}

        if bid.status == BidStatus.SUBMITTED:
            return {"ok": False, "error": "Bid already submitted."}

        opportunity = await self.get_opportunity(bid.opportunity_id)
        if not opportunity:
            return {"ok": False, "error": f"Opportunity {bid.opportunity_id} not found."}

        # ── READY: check approval outcome before acting ──
        if bid.status == BidStatus.READY and self._approval_queue:
            approval_id = bid.metadata.get("approval_id", "")
            if not approval_id:
                # Lost approval linkage — re-propose
                bid.status = BidStatus.DRAFT
            else:
                req = self._approval_queue.get_request(approval_id)
                if req is None:
                    # Approval record gone — re-propose
                    bid.status = BidStatus.DRAFT
                else:
                    status = req.get("status", "")
                    if status in ("approved", "executed"):
                        # Approved → fall through to execution below
                        pass
                    elif status in ("denied",):
                        bid.status = BidStatus.FAILED
                        await self._persist_bid(bid)
                        return {
                            "ok": False,
                            "error": f"Approval denied: {req.get('denial_reason', 'no reason given')}",
                        }
                    elif status in ("expired",):
                        bid.status = BidStatus.FAILED
                        await self._persist_bid(bid)
                        return {"ok": False, "error": "Approval expired. Re-draft and resubmit."}
                    else:
                        # Still pending
                        return {
                            "ok": False,
                            "pending_approval": True,
                            "approval_id": approval_id,
                            "message": f"Approval still pending (`{approval_id}`). "
                                       f"Use `/queue approve {approval_id}` to approve.",
                        }

        # ── DRAFT: propose new approval if queue exists ──
        if bid.status == BidStatus.DRAFT and self._approval_queue:
            from agent.core.approval import ApprovalCategory
            proposal = self._approval_queue.propose(
                category=ApprovalCategory.EXTERNAL,
                description=f"Submit bid: {bid.title} (${bid.price_usd:.2f})",
                risk_level="medium",
                reason=f"Marketplace bid on {bid.platform} for {opportunity.title[:50]}",
                proposed_by="marketplace_service",
                context={"bid_id": bid.id, "opportunity_id": bid.opportunity_id},
            )
            bid.status = BidStatus.READY
            bid.metadata["approval_id"] = proposal.id
            await self._persist_bid(bid)
            return {
                "ok": False,
                "pending_approval": True,
                "approval_id": proposal.id,
                "message": f"Bid requires approval. Use `/queue approve {proposal.id}`",
            }

        # ── Execute: only work listings are biddable ──
        biddable, reason = self.get_listing_bid_eligibility(opportunity)
        if not biddable:
            return {"ok": False, "error": reason}
        if hasattr(connector, "submit_listing_bid"):
            result = await connector.submit_listing_bid(
                self._gateway, opportunity.platform_id, bid,
            )
        else:
            result = await connector.submit_bid(self._gateway, bid, opportunity)
        if result.get("ok"):
            bid.status = BidStatus.SUBMITTED
            opportunity.status = OpportunityStatus.SUBMITTED
            await self._persist_opportunity(opportunity)
            # Mark approval as executed if applicable
            approval_id = bid.metadata.get("approval_id", "")
            if approval_id and self._approval_queue:
                self._approval_queue.mark_executed(approval_id)
        else:
            bid.status = BidStatus.FAILED
        await self._persist_bid(bid)
        return result

    # ─── Bid queries ───

    async def get_bid(self, bid_id: str) -> Bid | None:
        """Get a persisted bid by exact ID or unique short prefix."""
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT data FROM bids WHERE id = ?", (bid_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return Bid.from_dict(orjson.loads(row[0]))
        matches: list[Bid] = []
        async with self._db.execute(
            "SELECT data FROM bids ORDER BY rowid DESC",
        ) as cur:
            rows = await cur.fetchall()
        for raw_row in rows:
            bid = Bid.from_dict(orjson.loads(raw_row[0]))
            if bid.id.startswith(bid_id):
                matches.append(bid)
        if len(matches) == 1:
            return matches[0]
        return None

    async def list_bids(self, *, limit: int = 20) -> list[Bid]:
        """List persisted bid drafts, most recent first."""
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT data FROM bids ORDER BY rowid DESC LIMIT ?", (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [Bid.from_dict(orjson.loads(r[0])) for r in rows]

    # ─── Tracking — convert opportunity to tracked ALS project ───

    async def track(self, opportunity: Opportunity, bid: Bid | None = None) -> dict[str, Any]:
        """Create an ALS project to track work on this opportunity.

        This does NOT imply platform-side acceptance or winning a bid.
        It means the operator decided this opportunity is worth tracking
        as a local project for planning and execution.
        """
        if not self._projects:
            return {"ok": False, "error": "ProjectManager not available"}

        desc_parts = [
            f"Marketplace opportunity from {opportunity.platform}.",
            f"Platform ID: {opportunity.platform_id}",
        ]
        if opportunity.budget_max:
            desc_parts.append(f"Budget: {opportunity.budget_max} {opportunity.currency}")
        if opportunity.description:
            desc_parts.append(f"Description: {opportunity.description[:200]}")
        if bid:
            desc_parts.append(f"Bid draft: ${bid.price_usd:.2f}")

        project = await self._projects.create(
            name=f"[{opportunity.platform}] {opportunity.title[:60]}",
            description="\n".join(desc_parts),
            tags=["marketplace", opportunity.platform],
        )
        await self._projects.start(project.id)

        if bid:
            bid.project_id = project.id
            await self._persist_bid(bid)

        opportunity.status = OpportunityStatus.TRACKING
        await self._persist_opportunity(opportunity)

        logger.info(
            "marketplace_tracking",
            project_id=project.id,
            opportunity=opportunity.platform_id,
            platform=opportunity.platform,
        )
        return {"ok": True, "project_id": project.id, "project_name": project.name}

    # Legacy alias
    async def engage(self, opportunity: Opportunity, bid: Bid) -> dict[str, Any]:
        """Legacy alias for track()."""
        return await self.track(opportunity, bid)

    # ─── Listings / Jobs (delegated to connector) ───

    async def list_listings(self, *, platform: str = "", limit: int = 20) -> list[Opportunity]:
        """Browse work listings from platform connectors."""
        all_listings: list[Opportunity] = []
        connectors = [self._registry.get(platform)] if platform else self._registry.all()
        for connector in connectors:
            if connector is None or not hasattr(connector, "list_listings"):
                continue
            try:
                listings = await connector.list_listings(self._gateway, limit=limit)
                for opp in listings:
                    await self._persist_opportunity(opp)
                all_listings.extend(listings)
            except Exception:
                logger.warning("marketplace_listings_error", platform=getattr(connector, "platform_id", "?"))
        return all_listings

    async def list_jobs(self, *, platform: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """List accepted jobs from platform connectors."""
        all_jobs: list[dict[str, Any]] = []
        connectors = [self._registry.get(platform)] if platform else self._registry.all()
        for connector in connectors:
            if connector is None or not hasattr(connector, "list_jobs"):
                continue
            try:
                jobs = await connector.list_jobs(self._gateway, limit=limit)
                all_jobs.extend(jobs)
            except Exception:
                logger.warning("marketplace_jobs_error", platform=getattr(connector, "platform_id", "?"))
        return all_jobs

    async def get_job_detail(self, platform: str, job_id: str) -> dict[str, Any] | None:
        """Get job detail from a specific platform."""
        connector = self._registry.get(platform)
        if not connector or not hasattr(connector, "get_job"):
            return None
        return await connector.get_job(self._gateway, job_id)

    # ─── Job linkage ───

    async def link_job(self, bid_id: str, external_job_id: str) -> dict[str, Any]:
        """Link an external job ID to a bid (after platform acceptance)."""
        bid = await self.get_bid(bid_id)
        if not bid:
            return {"ok": False, "error": f"Bid {bid_id} not found."}
        if bid.external_job_id and bid.external_job_id != external_job_id:
            return {"ok": False, "error": f"Bid already linked to job {bid.external_job_id}."}
        bid.external_job_id = external_job_id
        if bid.status == BidStatus.SUBMITTED:
            bid.status = BidStatus.ACCEPTED
        await self._persist_bid(bid)
        # Also update opportunity status
        opp = await self.get_opportunity(bid.opportunity_id)
        if opp:
            opp.status = OpportunityStatus.ENGAGED
            await self._persist_opportunity(opp)
        logger.info("marketplace_job_linked", bid_id=bid_id, job_id=external_job_id)
        return {"ok": True, "bid_id": bid_id, "external_job_id": external_job_id}

    async def _find_linkage_for_job(self, job_id: str) -> dict[str, str]:
        """Find bid/opportunity/project linked to an external job ID."""
        bids = await self.list_bids(limit=100)
        for bid in bids:
            if bid.external_job_id == job_id:
                return {
                    "bid_id": bid.id,
                    "opportunity_id": bid.opportunity_id,
                    "project_id": bid.project_id,
                }
        return {}

    _TERMINAL_STATUSES = frozenset({"completed", "rejected", "cancelled"})

    async def _get_terminal_outcome(self, job_id: str) -> JobOutcome | None:
        """Return a terminal outcome for this job, or None."""
        for o in await self.list_outcomes(limit=200):
            if o.external_job_id == job_id and o.status.value in self._TERMINAL_STATUSES:
                return o
        return None

    # ─── Job lifecycle ───

    async def submit_job_work(
        self, platform: str, job_id: str, *,
        summary: str = "", proof: str = "", artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Submit completed work to a job and record as lifecycle event."""
        connector = self._registry.get(platform)
        if not connector or not hasattr(connector, "submit_job_work"):
            return {"ok": False, "error": f"No job-submit support for {platform}"}
        # Block if job already has a terminal outcome
        terminal = await self._get_terminal_outcome(job_id)
        if terminal:
            return {"ok": False, "error": f"Job {job_id} already finalized ({terminal.status.value})."}
        # Block duplicate submit — already submitted locally
        existing_submit = next(
            (o for o in await self.list_outcomes(limit=200)
             if o.external_job_id == job_id and o.status == JobOutcomeStatus.SUBMITTED),
            None,
        )
        if existing_submit:
            return {
                "ok": False,
                "error": "Work already submitted for this job. Awaiting platform response.",
                "existing_outcome_id": existing_submit.id,
            }
        result = await connector.submit_job_work(
            self._gateway, job_id, summary=summary, proof=proof, artifact_ids=artifact_ids,
        )
        if result.get("ok"):
            linkage = await self._find_linkage_for_job(job_id)
            outcome = JobOutcome(
                platform=platform,
                external_job_id=job_id,
                opportunity_id=linkage.get("opportunity_id", ""),
                bid_id=linkage.get("bid_id", ""),
                project_id=linkage.get("project_id", ""),
                status=JobOutcomeStatus.SUBMITTED,
                completed_at=datetime.now(UTC).isoformat(),
                platform_response=result.get("normalized_response", {}),
                notes=summary,
            )
            await self._persist_outcome(outcome)
            result["outcome_id"] = outcome.id
        return result

    async def complete_job(
        self, platform: str, job_id: str, *, notes: str = "",
    ) -> dict[str, Any]:
        """Mark job as completed and record outcome with auto-resolved linkage."""
        connector = self._registry.get(platform)
        if not connector or not hasattr(connector, "complete_job"):
            return {"ok": False, "error": f"No job-complete support for {platform}"}
        terminal = await self._get_terminal_outcome(job_id)
        if terminal:
            return {"ok": False, "error": f"Job {job_id} already finalized ({terminal.status.value})."}
        result = await connector.complete_job(self._gateway, job_id, notes=notes)
        if result.get("ok"):
            linkage = await self._find_linkage_for_job(job_id)
            normalized = result.get("normalized_response", {})
            outcome = JobOutcome(
                platform=platform,
                external_job_id=job_id,
                opportunity_id=linkage.get("opportunity_id", ""),
                bid_id=linkage.get("bid_id", ""),
                project_id=linkage.get("project_id", ""),
                status=JobOutcomeStatus.COMPLETED,
                revenue_amount=normalized.get("revenue"),
                revenue_currency=normalized.get("currency", ""),
                completed_at=datetime.now(UTC).isoformat(),
                platform_response=normalized,
                notes=notes,
            )
            await self._persist_outcome(outcome)
            result["outcome_id"] = outcome.id
            result["linkage"] = linkage
        return result

    async def reject_job(
        self, platform: str, job_id: str, *, reason: str = "",
    ) -> dict[str, Any]:
        """Reject/decline a job and record outcome with auto-resolved linkage."""
        connector = self._registry.get(platform)
        if not connector or not hasattr(connector, "reject_job"):
            return {"ok": False, "error": f"No job-reject support for {platform}"}
        terminal = await self._get_terminal_outcome(job_id)
        if terminal:
            return {"ok": False, "error": f"Job {job_id} already finalized ({terminal.status.value})."}
        result = await connector.reject_job(self._gateway, job_id, reason=reason)
        if result.get("ok"):
            linkage = await self._find_linkage_for_job(job_id)
            outcome = JobOutcome(
                platform=platform,
                external_job_id=job_id,
                opportunity_id=linkage.get("opportunity_id", ""),
                bid_id=linkage.get("bid_id", ""),
                project_id=linkage.get("project_id", ""),
                status=JobOutcomeStatus.REJECTED,
                completed_at=datetime.now(UTC).isoformat(),
                notes=reason,
            )
            await self._persist_outcome(outcome)
            result["outcome_id"] = outcome.id
        return result

    async def get_reputation(self, platform: str, agent_id: str) -> dict[str, Any] | None:
        """Get reputation/trust data for an agent."""
        connector = self._registry.get(platform)
        if not connector or not hasattr(connector, "get_reputation"):
            return None
        return await connector.get_reputation(self._gateway, agent_id)

    # ─── Outcome queries ───

    async def list_outcomes(self, *, limit: int = 20) -> list[JobOutcome]:
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT data FROM job_outcomes ORDER BY rowid DESC LIMIT ?", (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [JobOutcome.from_dict(orjson.loads(r[0])) for r in rows]

    # ─── Stats ───

    async def get_stats(self) -> dict[str, Any]:
        """Marketplace stats from persisted state."""
        if not self._db:
            return {"platforms": self._registry.list_platforms(), "opportunities": 0, "bids": 0}

        opp_count = 0
        bid_count = 0
        async with self._db.execute("SELECT COUNT(*) FROM opportunities") as cur:
            row = await cur.fetchone()
            opp_count = row[0] if row else 0
        async with self._db.execute("SELECT COUNT(*) FROM bids") as cur:
            row = await cur.fetchone()
            bid_count = row[0] if row else 0

        outcome_count = 0
        async with self._db.execute("SELECT COUNT(*) FROM job_outcomes") as cur:
            row = await cur.fetchone()
            outcome_count = row[0] if row else 0

        return {
            "platforms": self._registry.list_platforms(),
            "opportunities": opp_count,
            "bids": bid_count,
            "outcomes": outcome_count,
        }

    # ─── Persistence ───

    async def _persist_opportunity(self, opp: Opportunity) -> None:
        if not self._db:
            return
        # Keep exactly one local row per external platform/platform_id pair.
        if opp.platform_id:
            async with self._db.execute(
                "SELECT id, data FROM opportunities WHERE platform = ?",
                (opp.platform,),
            ) as cur:
                rows = await cur.fetchall()
            for row_id, row_data in rows:
                existing = Opportunity.from_dict(orjson.loads(row_data))
                if (
                    existing.platform_id == opp.platform_id
                    and existing.id != opp.id
                ):
                    await self._db.execute(
                        "DELETE FROM opportunities WHERE id = ?",
                        (row_id,),
                    )
        data = orjson.dumps(opp.to_dict()).decode()
        await self._db.execute(
            "INSERT OR REPLACE INTO opportunities (id, data, platform, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (opp.id, data, opp.platform, opp.status.value, opp.discovered_at),
        )
        await self._db.commit()

    async def _persist_bid(self, bid: Bid) -> None:
        if not self._db:
            return
        data = orjson.dumps(bid.to_dict()).decode()
        await self._db.execute(
            "INSERT OR REPLACE INTO bids (id, data, opportunity_id, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (bid.id, data, bid.opportunity_id, bid.status.value, bid.created_at),
        )
        await self._db.commit()

    async def _persist_outcome(self, outcome: JobOutcome) -> None:
        if not self._db:
            return
        data = orjson.dumps(outcome.to_dict()).decode()
        await self._db.execute(
            "INSERT OR REPLACE INTO job_outcomes (id, data, external_job_id, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (outcome.id, data, outcome.external_job_id, outcome.status.value, outcome.created_at),
        )
        await self._db.commit()
