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
        """Fetch opportunities from one or all platforms."""
        all_opps: list[Opportunity] = []

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
                all_opps.extend(opps)
            except Exception:
                logger.warning("marketplace_discover_error", platform=getattr(connector, "platform_id", "?"))

        logger.info("marketplace_discover", total=len(all_opps))
        return all_opps

    async def get_opportunity(self, opportunity_id: str) -> Opportunity | None:
        """Get a persisted opportunity by ID."""
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT data FROM opportunities WHERE id = ?", (opportunity_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return Opportunity.from_dict(orjson.loads(row[0]))
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

    async def submit_bid(self, bid: Bid) -> dict[str, Any]:
        """Submit bid to platform. Approval-gated, persisted, audited."""
        connector = self._registry.get(bid.platform)
        if not connector:
            return {"ok": False, "error": f"No connector for {bid.platform}"}

        if bid.status == BidStatus.SUBMITTED:
            return {"ok": False, "error": "Bid already submitted."}

        # Resolve the opportunity (needed for platform_id / slug)
        opportunity = await self.get_opportunity(bid.opportunity_id)
        if not opportunity:
            return {"ok": False, "error": f"Opportunity {bid.opportunity_id} not found."}

        # Gate through approval queue if available
        if self._approval_queue:
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
            await self._persist_bid(bid)
            return {
                "ok": False,
                "pending_approval": True,
                "approval_id": proposal.id,
                "message": f"Bid requires approval. Use `/queue approve {proposal.id}`",
            }

        result = await connector.submit_bid(self._gateway, bid, opportunity)
        if result.get("ok"):
            bid.status = BidStatus.SUBMITTED
        await self._persist_bid(bid)
        return result

    # ─── Bid queries ───

    async def get_bid(self, bid_id: str) -> Bid | None:
        """Get a persisted bid by ID."""
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT data FROM bids WHERE id = ?", (bid_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return Bid.from_dict(orjson.loads(row[0]))
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

        return {
            "platforms": self._registry.list_platforms(),
            "opportunities": opp_count,
            "bids": bid_count,
        }

    # ─── Persistence ───

    async def _persist_opportunity(self, opp: Opportunity) -> None:
        if not self._db:
            return
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
