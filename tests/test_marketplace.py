"""
Tests for the marketplace earning engine bounded context.

Covers:
1. Domain models — Opportunity, Evaluation, Bid serialization
2. Connector registry — registration, lookup
3. Obolos connector — normalization, evaluation, bid preparation
4. Marketplace service — discover, evaluate, bid, engage, persistence
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.marketplace.connectors import ConnectorRegistry
from agent.marketplace.models import (
    Bid,
    BidStatus,
    Evaluation,
    FeasibilityVerdict,
    Opportunity,
    OpportunityStatus,
)
from agent.marketplace.obolos import ObolosConnector
from agent.marketplace.service import MarketplaceService

# ─────────────────────────────────────────────
# Domain Models
# ─────────────────────────────────────────────


class TestOpportunity:
    def test_defaults(self):
        opp = Opportunity(title="Test Job")
        assert opp.status == OpportunityStatus.DISCOVERED
        assert opp.platform == ""
        assert opp.budget_min == 0.0

    def test_to_from_dict(self):
        opp = Opportunity(
            title="Code Review",
            platform="obolos.tech",
            platform_id="slug-123",
            budget_max=50.0,
            skills_required=["python", "testing"],
        )
        d = opp.to_dict()
        opp2 = Opportunity.from_dict(d)
        assert opp2.title == "Code Review"
        assert opp2.platform == "obolos.tech"
        assert opp2.budget_max == 50.0
        assert opp2.skills_required == ["python", "testing"]

    def test_status_roundtrip(self):
        opp = Opportunity(status=OpportunityStatus.ENGAGED)
        d = opp.to_dict()
        assert Opportunity.from_dict(d).status == OpportunityStatus.ENGAGED


class TestEvaluation:
    def test_defaults(self):
        ev = Evaluation()
        assert ev.verdict == FeasibilityVerdict.INFEASIBLE
        assert ev.confidence == 0.0

    def test_to_from_dict(self):
        ev = Evaluation(
            opportunity_id="opp-123",
            verdict=FeasibilityVerdict.FEASIBLE,
            confidence=0.85,
            matched_skills=["python"],
            reasoning="All skills matched.",
        )
        d = ev.to_dict()
        ev2 = Evaluation.from_dict(d)
        assert ev2.verdict == FeasibilityVerdict.FEASIBLE
        assert ev2.confidence == 0.85
        assert ev2.matched_skills == ["python"]


class TestBid:
    def test_defaults(self):
        bid = Bid()
        assert bid.status == BidStatus.DRAFT
        assert bid.price_usd == 0.0

    def test_to_from_dict(self):
        bid = Bid(
            opportunity_id="opp-1",
            platform="obolos.tech",
            price_usd=25.0,
            status=BidStatus.SUBMITTED,
        )
        d = bid.to_dict()
        bid2 = Bid.from_dict(d)
        assert bid2.price_usd == 25.0
        assert bid2.status == BidStatus.SUBMITTED


# ─────────────────────────────────────────────
# Connector Registry
# ─────────────────────────────────────────────


class TestConnectorRegistry:
    def test_register_and_get(self):
        reg = ConnectorRegistry()
        connector = ObolosConnector()
        reg.register(connector)
        assert reg.get("obolos.tech") is connector
        assert reg.get("nonexistent") is None

    def test_list_platforms(self):
        reg = ConnectorRegistry()
        reg.register(ObolosConnector())
        assert "obolos.tech" in reg.list_platforms()

    def test_all(self):
        reg = ConnectorRegistry()
        reg.register(ObolosConnector())
        assert len(reg.all()) == 1


# ─────────────────────────────────────────────
# Obolos Connector
# ─────────────────────────────────────────────


class TestObolosConnector:
    def test_platform_id(self):
        c = ObolosConnector()
        assert c.platform_id == "obolos.tech"

    def test_normalize_api_to_opportunity(self):
        c = ObolosConnector()
        api = {
            "slug": "test-api",
            "name": "Test API",
            "description": "A test API endpoint",
            "price": 10,
            "tags": ["python", "api"],
        }
        opp = c._normalize_api_to_opportunity(api)
        assert opp is not None
        assert opp.title == "Test API"
        assert opp.platform == "obolos.tech"
        assert opp.platform_id == "test-api"
        assert opp.budget_min == 10.0
        assert opp.skills_required == ["python", "api"]

    def test_normalize_empty_slug_returns_none(self):
        c = ObolosConnector()
        assert c._normalize_api_to_opportunity({}) is None

    def test_evaluate_feasible(self):
        c = ObolosConnector()
        opp = Opportunity(skills_required=["python", "api", "testing"])
        ev = c.evaluate_opportunity(opp, ["python", "api", "testing"])
        assert ev.verdict == FeasibilityVerdict.FEASIBLE
        assert ev.confidence > 0.5
        assert len(ev.missing_skills) == 0

    def test_evaluate_partial(self):
        c = ObolosConnector()
        opp = Opportunity(skills_required=["python", "api", "rust", "gpu"])
        ev = c.evaluate_opportunity(opp, [])
        # python and api are in _ALS_CAPABILITIES, rust and gpu are not
        assert ev.verdict in (FeasibilityVerdict.PARTIAL, FeasibilityVerdict.INFEASIBLE)
        assert "rust" in ev.missing_skills or "gpu" in ev.missing_skills

    def test_evaluate_no_requirements(self):
        c = ObolosConnector()
        opp = Opportunity(skills_required=[])
        ev = c.evaluate_opportunity(opp, [])
        assert ev.verdict == FeasibilityVerdict.PARTIAL
        assert "manual review" in ev.reasoning.lower()

    def test_prepare_bid(self):
        c = ObolosConnector()
        opp = Opportunity(
            title="Build me an API",
            budget_max=100.0,
        )
        ev = Evaluation(verdict=FeasibilityVerdict.FEASIBLE, confidence=0.8)
        bid = c.prepare_bid(opp, ev)
        assert bid.platform == "obolos.tech"
        assert bid.price_usd == 80.0  # 80% of max
        assert bid.status == BidStatus.DRAFT
        assert "pipeline" in bid.proposal_text.lower()


# ─────────────────────────────────────────────
# Marketplace Service
# ─────────────────────────────────────────────


class TestMarketplaceService:
    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        stats = await svc.get_stats()
        assert stats["opportunities"] == 0
        assert stats["bids"] == 0
        await svc.close()

    @pytest.mark.asyncio
    async def test_persist_and_list_opportunities(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()

        opp = Opportunity(title="Test", platform="test-platform")
        await svc._persist_opportunity(opp)

        listed = await svc.list_opportunities()
        assert len(listed) == 1
        assert listed[0].title == "Test"
        await svc.close()

    @pytest.mark.asyncio
    async def test_get_opportunity_by_id(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()

        opp = Opportunity(title="Specific", platform="test")
        await svc._persist_opportunity(opp)

        fetched = await svc.get_opportunity(opp.id)
        assert fetched is not None
        assert fetched.title == "Specific"
        await svc.close()

    @pytest.mark.asyncio
    async def test_evaluate_without_connector(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()

        opp = Opportunity(title="Test", platform="unknown-platform")
        ev = svc.evaluate(opp)
        assert ev.verdict == FeasibilityVerdict.INFEASIBLE
        assert "no connector" in ev.reasoning.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_evaluate_with_connector(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(
            title="Python API",
            platform="obolos.tech",
            skills_required=["python", "api"],
        )
        ev = svc.evaluate(opp)
        assert ev.verdict == FeasibilityVerdict.FEASIBLE
        await svc.close()

    @pytest.mark.asyncio
    async def test_engage_creates_project(self, tmp_path: Path):
        from agent.projects.manager import ProjectManager

        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        svc = MarketplaceService(
            projects=pm,
            db_path=str(tmp_path / "mkt.db"),
        )
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(
            title="API Gig",
            platform="obolos.tech",
            platform_id="api-gig-1",
            budget_max=50.0,
            currency="credits",
        )
        bid = Bid(
            opportunity_id=opp.id,
            platform="obolos.tech",
            price_usd=40.0,
        )

        result = await svc.engage(opp, bid)
        assert result["ok"] is True
        assert result["project_id"]

        # Project exists and is active
        project = await pm.get(result["project_id"])
        assert project is not None
        assert "obolos.tech" in project.name
        assert project.status.value == "active"
        assert "marketplace" in project.tags

        await svc.close()
        await pm.close()

    @pytest.mark.asyncio
    async def test_stats(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="A", platform="obolos.tech")
        await svc._persist_opportunity(opp)

        bid = Bid(opportunity_id=opp.id, platform="obolos.tech")
        await svc._persist_bid(bid)

        stats = await svc.get_stats()
        assert stats["opportunities"] == 1
        assert stats["bids"] == 1
        assert "obolos.tech" in stats["platforms"]
        await svc.close()

    @pytest.mark.asyncio
    async def test_service_close_and_reopen(self, tmp_path: Path):
        db = str(tmp_path / "mkt.db")

        svc = MarketplaceService(db_path=db)
        await svc.initialize()
        opp = Opportunity(title="Persist Test", platform="test")
        await svc._persist_opportunity(opp)
        await svc.close()

        # Reopen
        svc2 = MarketplaceService(db_path=db)
        await svc2.initialize()
        fetched = await svc2.get_opportunity(opp.id)
        assert fetched is not None
        assert fetched.title == "Persist Test"
        await svc2.close()


# ─────────────────────────────────────────────
# Gateway contract behavior tests
# ─────────────────────────────────────────────


class TestObolosConnectorGatewayContract:
    """Verify connector uses correct gateway keyword arguments."""

    @pytest.mark.asyncio
    async def test_fetch_opportunities_uses_query_params(self):
        """fetch_opportunities must pass query_params, not params."""
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {"slugs": ["slug-1"]},
            "response_json": {},
        }
        # Detail call for slug-1
        gateway.call_api_via_capability.side_effect = [
            # First call: catalog
            {
                "ok": True,
                "normalized_response": {"slugs": ["slug-1"]},
            },
            # Second call: detail for slug-1
            {
                "ok": True,
                "response_json": {"slug": "slug-1", "name": "Test API", "tags": ["api"]},
            },
        ]

        c = ObolosConnector()
        await c.fetch_opportunities(gateway, category="test", limit=5)

        # First call: catalog
        first_call = gateway.call_api_via_capability.call_args_list[0]
        assert "query_params" in first_call.kwargs
        assert "params" not in first_call.kwargs

    @pytest.mark.asyncio
    async def test_fetch_opportunity_detail_reads_response_json(self):
        """Detail fetch must use response_json, not normalized_response."""
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "response_json": {
                "slug": "my-api",
                "name": "My API",
                "description": "Does things",
                "price": 25,
                "tags": ["python"],
            },
            "normalized_response": {
                "kind": "marketplace_api_call",
                "top_level_keys": ["slug", "name"],
            },
        }

        c = ObolosConnector()
        opp = await c.fetch_opportunity_detail(gateway, "my-api")

        assert opp is not None
        assert opp.title == "My API"
        assert opp.budget_min == 25.0
        assert opp.skills_required == ["python"]

    @pytest.mark.asyncio
    async def test_submit_bid_uses_marketplace_api_call(self):
        """submit_bid must POST to marketplace_api_call_v1 with opportunity slug."""
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "status_code": 200}

        c = ObolosConnector()
        opp = Opportunity(platform="obolos.tech", platform_id="test-slug", title="Test")
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", price_usd=10.0, title="My Bid")

        result = await c.submit_bid(gateway, bid, opp)
        assert result["ok"] is True
        assert bid.status == BidStatus.SUBMITTED

        call_kwargs = gateway.call_api_via_capability.call_args.kwargs
        assert call_kwargs["capability_id"] == "marketplace_api_call_v1"
        assert call_kwargs["resource"] == "test-slug"
        assert call_kwargs["method"] == "POST"
        assert "json_payload" in call_kwargs

    @pytest.mark.asyncio
    async def test_submit_bid_without_opportunity_fails(self):
        """submit_bid without opportunity must fail cleanly."""
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        c = ObolosConnector()
        bid = Bid(opportunity_id="opp-1", platform="obolos.tech")

        result = await c.submit_bid(gateway, bid, None)
        assert result["ok"] is False
        assert "required" in result["error"].lower()
        gateway.call_api_via_capability.assert_not_called()

    @pytest.mark.asyncio
    async def test_submit_bid_gateway_failure(self):
        """Gateway failure must be returned cleanly."""
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": False, "error": "timeout"}

        c = ObolosConnector()
        opp = Opportunity(platform="obolos.tech", platform_id="slug", title="X")
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech")

        result = await c.submit_bid(gateway, bid, opp)
        assert result["ok"] is False
        assert bid.status != BidStatus.SUBMITTED


class TestDiscoveryTwoStepFlow:
    """Discovery must: catalog → slugs → detail for each."""

    @pytest.mark.asyncio
    async def test_catalog_slugs_then_detail(self):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.side_effect = [
            # Catalog returns slugs
            {"ok": True, "normalized_response": {"slugs": ["a", "b"]}},
            # Detail for "a"
            {"ok": True, "response_json": {"slug": "a", "name": "API A", "tags": ["python"]}},
            # Detail for "b"
            {"ok": True, "response_json": {"slug": "b", "name": "API B", "price": 10}},
        ]

        c = ObolosConnector()
        opps = await c.fetch_opportunities(gateway, limit=10)

        assert len(opps) == 2
        assert opps[0].title == "API A"
        assert opps[0].platform_id == "a"
        assert opps[1].title == "API B"
        assert opps[1].budget_min == 10.0

        # 3 calls: 1 catalog + 2 details
        assert gateway.call_api_via_capability.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_catalog_returns_empty(self):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {"slugs": []},
        }

        c = ObolosConnector()
        opps = await c.fetch_opportunities(gateway)
        assert opps == []
        assert gateway.call_api_via_capability.call_count == 1

    @pytest.mark.asyncio
    async def test_catalog_failure_returns_empty(self):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": False,
            "error": "network timeout",
        }

        c = ObolosConnector()
        opps = await c.fetch_opportunities(gateway)
        assert opps == []

    @pytest.mark.asyncio
    async def test_detail_failure_skips_slug(self):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.side_effect = [
            {"ok": True, "normalized_response": {"slugs": ["good", "bad"]}},
            {"ok": True, "response_json": {"slug": "good", "name": "Good API"}},
            {"ok": False, "error": "not found"},
        ]

        c = ObolosConnector()
        opps = await c.fetch_opportunities(gateway, limit=10)
        assert len(opps) == 1
        assert opps[0].platform_id == "good"


class TestMarketplaceServiceBidQueries:
    """Bid query methods: get_bid, list_bids."""

    @pytest.mark.asyncio
    async def test_list_bids_empty(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        assert await svc.list_bids() == []
        await svc.close()

    @pytest.mark.asyncio
    async def test_persist_and_list_bids(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        bid = Bid(opportunity_id="opp-1", platform="test", price_usd=10.0, title="Test Bid")
        await svc._persist_bid(bid)
        bids = await svc.list_bids()
        assert len(bids) == 1
        assert bids[0].title == "Test Bid"
        await svc.close()

    @pytest.mark.asyncio
    async def test_get_bid_by_id(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        bid = Bid(opportunity_id="opp-1", platform="test")
        await svc._persist_bid(bid)
        fetched = await svc.get_bid(bid.id)
        assert fetched is not None
        assert fetched.id == bid.id
        await svc.close()

    @pytest.mark.asyncio
    async def test_get_bid_missing(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        assert await svc.get_bid("nonexistent") is None
        await svc.close()


class TestMarketplaceTrack:
    """Track (project linkage) behavior."""

    @pytest.mark.asyncio
    async def test_track_creates_project(self, tmp_path: Path):
        from agent.projects.manager import ProjectManager

        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()
        svc = MarketplaceService(projects=pm, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Track Test", platform="obolos.tech", platform_id="t1")
        await svc._persist_opportunity(opp)

        result = await svc.track(opp)
        assert result["ok"] is True
        project = await pm.get(result["project_id"])
        assert project is not None
        assert project.status.value == "active"
        assert "marketplace" in project.tags

        # Opportunity status must be TRACKING, not ENGAGED
        reloaded = await svc.get_opportunity(opp.id)
        assert reloaded.status == OpportunityStatus.TRACKING

        await svc.close()
        await pm.close()

    @pytest.mark.asyncio
    async def test_track_with_bid_links_project(self, tmp_path: Path):
        from agent.projects.manager import ProjectManager

        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()
        svc = MarketplaceService(projects=pm, db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()

        opp = Opportunity(title="With Bid", platform="test", budget_max=50.0)
        bid = Bid(opportunity_id=opp.id, platform="test", price_usd=40.0)
        await svc._persist_opportunity(opp)
        await svc._persist_bid(bid)

        result = await svc.track(opp, bid)
        assert result["ok"] is True

        # Bid should have project_id set
        reloaded_bid = await svc.get_bid(bid.id)
        assert reloaded_bid.project_id == result["project_id"]

        await svc.close()
        await pm.close()

    @pytest.mark.asyncio
    async def test_track_without_projects_manager(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        opp = Opportunity(title="No PM", platform="test")
        result = await svc.track(opp)
        assert result["ok"] is False
        assert "not available" in result["error"].lower()
        await svc.close()


class TestMarketplaceServiceSubmit:
    """Service-level submit flow tests."""

    @pytest.mark.asyncio
    async def test_submit_resolves_opportunity_and_calls_connector(self, tmp_path: Path):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True}

        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Submit Test", platform="obolos.tech", platform_id="slug-1", url="https://obolos.tech/api/listings/slug-1")
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", price_usd=20.0, title="Bid X")
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result["ok"] is True

        reloaded = await svc.get_bid(bid.id)
        assert reloaded.status == BidStatus.SUBMITTED
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_already_submitted_fails(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        bid = Bid(
            opportunity_id="opp-1", platform="obolos.tech",
            status=BidStatus.SUBMITTED,
        )
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result["ok"] is False
        assert "already submitted" in result["error"].lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_missing_opportunity_fails(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        bid = Bid(opportunity_id="nonexistent", platform="obolos.tech")
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result["ok"] is False
        assert "not found" in result["error"].lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_with_approval_queue(self, tmp_path: Path):
        from unittest.mock import MagicMock

        approval = MagicMock()
        proposal = MagicMock()
        proposal.id = "approval-123"
        approval.propose.return_value = proposal

        svc = MarketplaceService(
            approval_queue=approval,
            db_path=str(tmp_path / "mkt.db"),
        )
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Gated", platform="obolos.tech", platform_id="gated-slug")
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", price_usd=50.0)
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result["pending_approval"] is True
        assert "approval-123" in result["approval_id"]

        reloaded = await svc.get_bid(bid.id)
        assert reloaded.status == BidStatus.READY
        await svc.close()


class TestMarketplaceTelegramBehavior:
    """Real output behavior tests for /marketplace commands."""

    def _make_handler(self, marketplace_svc):
        """Create a minimal TelegramHandler mock with marketplace wired."""
        from unittest.mock import MagicMock
        agent = MagicMock()
        agent.marketplace = marketplace_svc
        from agent.social.telegram_handler import TelegramHandler
        handler = TelegramHandler.__new__(TelegramHandler)
        handler._agent = agent
        return handler

    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        handler = self._make_handler(svc)

        result = await handler._cmd_marketplace("")
        assert "0 opportunities" in result
        assert "0 bid drafts" in result
        assert "/marketplace discover" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_list_with_data(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Listed Opp", platform="obolos.tech", budget_max=20.0, currency="credits")
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", price_usd=15.0, title="My Bid")
        await svc._persist_bid(bid)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("")
        assert "Listed Opp" in result
        assert "My Bid" in result
        assert "1 opportunities" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_show_existing(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        opp = Opportunity(
            title="Detailed Opp", platform="obolos.tech",
            platform_id="slug-x", budget_max=100.0, currency="credits",
            skills_required=["python", "api"], description="A test listing.",
        )
        await svc._persist_opportunity(opp)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"show {opp.id}")
        assert "Detailed Opp" in result
        assert "obolos.tech" in result
        assert "slug-x" in result
        assert "100.0" in result
        assert "python" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_show_missing(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("show nonexistent")
        assert "not found" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_bids_empty(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("bids")
        assert "no bid drafts" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_bids_with_data(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        bid = Bid(opportunity_id="opp-1", platform="test", price_usd=25.0, title="Draft Bid")
        await svc._persist_bid(bid)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("bids")
        assert "Draft Bid" in result
        assert "$25.00" in result
        assert "draft" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_track_creates_project(self, tmp_path: Path):
        from agent.projects.manager import ProjectManager

        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()
        svc = MarketplaceService(projects=pm, db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()

        opp = Opportunity(title="Track via TG", platform="obolos.tech")
        await svc._persist_opportunity(opp)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"track {opp.id}")
        assert "tracked as als project" in result.lower()
        assert "does not imply platform-side acceptance" in result.lower()
        assert "/projects" in result

        await svc.close()
        await pm.close()

    @pytest.mark.asyncio
    async def test_track_missing_opportunity(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("track nonexistent")
        assert "not found" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_bid_includes_submit_link(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(
            title="Bid Test", platform="obolos.tech",
            skills_required=["python"],
            url="https://obolos.tech/api/listings/bid-test",
        )
        await svc._persist_opportunity(opp)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"bid {opp.id}")
        assert "/marketplace submit" in result
        assert "approval-gated" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_help_lists_all_real_commands(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("unknowncmd")
        assert "/marketplace discover" in result
        assert "/marketplace show" in result
        assert "/marketplace eval" in result
        assert "/marketplace bid" in result
        assert "/marketplace submit" in result
        assert "/marketplace bids" in result
        assert "/marketplace track" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_missing_bid(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("submit nonexistent")
        assert "not found" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_already_submitted(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Done", platform="obolos.tech", platform_id="s")
        await svc._persist_opportunity(opp)
        bid = Bid(
            opportunity_id=opp.id, platform="obolos.tech",
            status=BidStatus.SUBMITTED,
        )
        await svc._persist_bid(bid)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"submit {bid.id}")
        assert "already submitted" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_success_via_gateway(self, tmp_path: Path):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True}

        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Live", platform="obolos.tech", platform_id="live-slug", url="https://obolos.tech/api/listings/live-slug")
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", price_usd=30.0)
        await svc._persist_bid(bid)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"submit {bid.id}")
        assert "submitted" in result.lower()
        assert "does not guarantee acceptance" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_bid_output_includes_submit_hint(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Bid Hint", platform="obolos.tech", skills_required=["python"], url="https://obolos.tech/api/listings/bid-hint")
        await svc._persist_opportunity(opp)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"bid {opp.id}")
        assert "/marketplace submit" in result
        await svc.close()


# ─────────────────────────────────────────────
# Approval → execute gap fix
# ─────────────────────────────────────────────


class TestApprovalExecuteGap:
    """Full approval→execute flow must work without repeated approvals."""

    @pytest.mark.asyncio
    async def test_full_draft_approve_execute_flow(self, tmp_path: Path):
        """DRAFT → propose → READY → approve → submit again → SUBMITTED."""
        from unittest.mock import AsyncMock, MagicMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True}

        # Real-ish approval queue mock
        approval = MagicMock()
        proposal = MagicMock()
        proposal.id = "ap-001"
        approval.propose.return_value = proposal
        # After approval: get_request returns approved status
        approval.get_request.return_value = {"status": "approved", "id": "ap-001"}

        svc = MarketplaceService(
            gateway=gateway, approval_queue=approval,
            db_path=str(tmp_path / "mkt.db"),
        )
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="E2E", platform="obolos.tech", platform_id="slug-e2e", url="https://obolos.tech/api/listings/slug-e2e")
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", price_usd=25.0, title="E2E Bid")
        await svc._persist_bid(bid)

        # Step 1: DRAFT → propose approval → READY
        r1 = await svc.submit_bid(bid)
        assert r1.get("pending_approval") is True
        assert r1["approval_id"] == "ap-001"
        reloaded = await svc.get_bid(bid.id)
        assert reloaded.status == BidStatus.READY
        assert reloaded.metadata["approval_id"] == "ap-001"
        gateway.call_api_via_capability.assert_not_called()

        # Step 2: READY + approved → execute gateway call → SUBMITTED
        r2 = await svc.submit_bid(reloaded)
        assert r2.get("ok") is True
        approval.propose.assert_called_once()  # Only one proposal total
        gateway.call_api_via_capability.assert_called_once()  # Gateway called exactly once

        final = await svc.get_bid(bid.id)
        assert final.status == BidStatus.SUBMITTED
        approval.mark_executed.assert_called_once_with("ap-001")
        await svc.close()

    @pytest.mark.asyncio
    async def test_pending_approval_does_not_execute(self, tmp_path: Path):
        """READY + still pending → no gateway call, return pending."""
        from unittest.mock import AsyncMock, MagicMock

        gateway = AsyncMock()
        approval = MagicMock()
        approval.get_request.return_value = {"status": "pending", "id": "ap-002"}

        svc = MarketplaceService(
            gateway=gateway, approval_queue=approval,
            db_path=str(tmp_path / "mkt.db"),
        )
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Pending", platform="obolos.tech", platform_id="slug-p")
        await svc._persist_opportunity(opp)
        bid = Bid(
            opportunity_id=opp.id, platform="obolos.tech",
            status=BidStatus.READY, metadata={"approval_id": "ap-002"},
        )
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result.get("pending_approval") is True
        assert "still pending" in result.get("message", "").lower()
        gateway.call_api_via_capability.assert_not_called()
        approval.propose.assert_not_called()  # No duplicate proposal
        await svc.close()

    @pytest.mark.asyncio
    async def test_denied_approval_fails_bid(self, tmp_path: Path):
        """READY + denied → bid FAILED, clear error."""
        from unittest.mock import AsyncMock, MagicMock

        gateway = AsyncMock()
        approval = MagicMock()
        approval.get_request.return_value = {
            "status": "denied", "denial_reason": "too expensive",
        }

        svc = MarketplaceService(
            gateway=gateway, approval_queue=approval,
            db_path=str(tmp_path / "mkt.db"),
        )
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Denied", platform="obolos.tech", platform_id="slug-d")
        await svc._persist_opportunity(opp)
        bid = Bid(
            opportunity_id=opp.id, platform="obolos.tech",
            status=BidStatus.READY, metadata={"approval_id": "ap-003"},
        )
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result["ok"] is False
        assert "denied" in result["error"].lower()
        reloaded = await svc.get_bid(bid.id)
        assert reloaded.status == BidStatus.FAILED
        gateway.call_api_via_capability.assert_not_called()
        await svc.close()

    @pytest.mark.asyncio
    async def test_expired_approval_fails_bid(self, tmp_path: Path):
        """READY + expired → bid FAILED."""
        from unittest.mock import AsyncMock, MagicMock

        gateway = AsyncMock()
        approval = MagicMock()
        approval.get_request.return_value = {"status": "expired"}

        svc = MarketplaceService(
            gateway=gateway, approval_queue=approval,
            db_path=str(tmp_path / "mkt.db"),
        )
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Expired", platform="obolos.tech", platform_id="slug-e")
        await svc._persist_opportunity(opp)
        bid = Bid(
            opportunity_id=opp.id, platform="obolos.tech",
            status=BidStatus.READY, metadata={"approval_id": "ap-004"},
        )
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result["ok"] is False
        assert "expired" in result["error"].lower()
        reloaded = await svc.get_bid(bid.id)
        assert reloaded.status == BidStatus.FAILED
        await svc.close()

    @pytest.mark.asyncio
    async def test_no_approval_queue_executes_directly(self, tmp_path: Path):
        """DRAFT + no approval queue → execute directly, no proposal."""
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True}

        svc = MarketplaceService(
            gateway=gateway, approval_queue=None,
            db_path=str(tmp_path / "mkt.db"),
        )
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Direct", platform="obolos.tech", platform_id="slug-dir", url="https://obolos.tech/api/listings/slug-dir")
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", price_usd=10.0)
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result["ok"] is True
        gateway.call_api_via_capability.assert_called_once()

        reloaded = await svc.get_bid(bid.id)
        assert reloaded.status == BidStatus.SUBMITTED
        await svc.close()


# ─────────────────────────────────────────────
# Obolos Listings routes
# ─────────────────────────────────────────────


class TestObolosListingsRoutes:
    """Route resolution for documented listings endpoints."""

    def test_listings_list_route_exists(self):
        from agent.control.policy import get_external_capability_route
        route = get_external_capability_route("obolos_listings_list_primary")
        assert route is not None
        assert route.capability_id == "listings_list_v1"
        assert route.request_mode == "obolos_listings_list_v1"

    def test_listings_detail_route_exists(self):
        from agent.control.policy import get_external_capability_route
        route = get_external_capability_route("obolos_listings_detail_primary")
        assert route is not None
        assert route.capability_id == "listings_detail_v1"

    def test_listings_bid_route_exists(self):
        from agent.control.policy import get_external_capability_route
        route = get_external_capability_route("obolos_listings_bid_primary")
        assert route is not None
        assert route.capability_id == "listings_bid_v1"

    def test_jobs_list_route_exists(self):
        from agent.control.policy import get_external_capability_route
        route = get_external_capability_route("obolos_jobs_list_primary")
        assert route is not None
        assert route.capability_id == "jobs_list_v1"

    def test_jobs_detail_route_exists(self):
        from agent.control.policy import get_external_capability_route
        route = get_external_capability_route("obolos_jobs_detail_primary")
        assert route is not None

    def test_jobs_submit_route_exists(self):
        from agent.control.policy import get_external_capability_route
        route = get_external_capability_route("obolos_jobs_submit_primary")
        assert route is not None
        assert route.capability_id == "jobs_submit_v1"


class TestObolosConnectorListings:
    """Connector listings/jobs methods against mock gateway."""

    @pytest.mark.asyncio
    async def test_list_listings(self):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {
                "listings": [
                    {"id": "L1", "title": "Build an API", "budget": 100, "skills": ["python"]},
                    {"id": "L2", "title": "Review code", "budget": 50},
                ],
            },
        }
        c = ObolosConnector()
        opps = await c.list_listings(gateway, limit=10)
        assert len(opps) == 2
        assert opps[0].platform_id == "L1"
        assert opps[0].title == "Build an API"
        assert opps[1].platform_id == "L2"

        call_kwargs = gateway.call_api_via_capability.call_args.kwargs
        assert call_kwargs["capability_id"] == "listings_list_v1"

    @pytest.mark.asyncio
    async def test_get_listing_detail(self):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {
                "listing": {
                    "id": "L1", "title": "Build API", "description": "Need Python API",
                    "budget": 200, "skills": ["python", "api"],
                },
            },
        }
        c = ObolosConnector()
        opp = await c.get_listing(gateway, "L1")
        assert opp is not None
        assert opp.title == "Build API"
        assert opp.budget_max == 200.0

    @pytest.mark.asyncio
    async def test_submit_listing_bid(self):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {"bid_id": "B123", "status": "pending"},
        }
        c = ObolosConnector()
        bid = Bid(opportunity_id="L1", platform="obolos.tech", price_usd=80.0,
                  proposal_text="I can do this", delivery_days=3)

        result = await c.submit_listing_bid(gateway, "L1", bid)
        assert result["ok"] is True
        assert bid.status == BidStatus.SUBMITTED
        assert bid.metadata.get("platform_bid_id") == "B123"

        call_kwargs = gateway.call_api_via_capability.call_args.kwargs
        assert call_kwargs["capability_id"] == "listings_bid_v1"
        assert call_kwargs["resource"] == "L1"
        assert call_kwargs["json_payload"]["price"] == 80.0

    @pytest.mark.asyncio
    async def test_list_jobs(self):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {
                "jobs": [
                    {"id": "J1", "title": "Build API", "status": "funded"},
                    {"id": "J2", "title": "Review code", "status": "completed"},
                ],
            },
        }
        c = ObolosConnector()
        jobs = await c.list_jobs(gateway, limit=10)
        assert len(jobs) == 2
        assert jobs[0]["id"] == "J1"

    @pytest.mark.asyncio
    async def test_get_job_detail(self):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {
                "job": {"id": "J1", "title": "Build API", "status": "funded", "budget": 200},
            },
        }
        c = ObolosConnector()
        job = await c.get_job(gateway, "J1")
        assert job is not None
        assert job["status"] == "funded"


class TestMarketplaceTelegramListingsJobs:
    """Telegram behavior for listings/jobs commands."""

    def _make_handler(self, marketplace_svc):
        from unittest.mock import MagicMock

        from agent.social.telegram_handler import TelegramHandler
        agent = MagicMock()
        agent.marketplace = marketplace_svc
        handler = TelegramHandler.__new__(TelegramHandler)
        handler._agent = agent
        return handler

    @pytest.mark.asyncio
    async def test_help_lists_listings_and_jobs(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("unknowncmd")
        assert "/marketplace listings" in result
        assert "/marketplace jobs" in result
        assert "/marketplace job" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_listings_empty(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True, "normalized_response": {"listings": []},
        }
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("listings")
        assert "no work listings" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_listings_with_data(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {
                "listings": [{"id": "L1", "title": "Build API", "budget": 100}],
            },
        }
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("listings")
        assert "Build API" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_jobs_empty(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True, "normalized_response": {"jobs": []},
        }
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("jobs")
        assert "no jobs" in result.lower()
        await svc.close()


# ─────────────────────────────────────────────
# Listings-first bid eligibility
# ─────────────────────────────────────────────


class TestBidEligibility:
    """Only work listings are biddable, not generic API marketplace items."""

    def test_listing_opportunity_is_listing(self):
        opp = Opportunity(
            title="Work", platform="obolos.tech",
            url="https://obolos.tech/api/listings/L1", category="listing",
        )
        assert opp.is_listing is True

    def test_api_marketplace_opportunity_is_not_listing(self):
        opp = Opportunity(
            title="API", platform="obolos.tech",
            url="https://obolos.tech/api/some-slug", category="api",
        )
        assert opp.is_listing is False

    def test_empty_url_not_listing(self):
        opp = Opportunity(title="Bare", platform="obolos.tech")
        assert opp.is_listing is False

    def test_category_listing_is_listing(self):
        opp = Opportunity(title="Cat", platform="obolos.tech", category="listing")
        assert opp.is_listing is True

    @pytest.mark.asyncio
    async def test_service_submit_rejects_non_listing(self, tmp_path: Path):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(
            title="API Item", platform="obolos.tech", platform_id="slug",
            url="https://obolos.tech/api/slug",
        )
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", price_usd=10.0)
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result["ok"] is False
        assert "not a work listing" in result["error"].lower()
        gateway.call_api_via_capability.assert_not_called()
        await svc.close()

    @pytest.mark.asyncio
    async def test_service_submit_allows_listing(self, tmp_path: Path):
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {"bid_id": "B1", "status": "pending"},
        }
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(
            title="Work Listing", platform="obolos.tech", platform_id="L1",
            url="https://obolos.tech/api/listings/L1",
        )
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", price_usd=50.0)
        await svc._persist_bid(bid)

        result = await svc.submit_bid(bid)
        assert result["ok"] is True
        gateway.call_api_via_capability.assert_called_once()
        await svc.close()


class TestTelegramBidEligibility:
    """Telegram /marketplace bid rejects non-listing opportunities."""

    def _make_handler(self, marketplace_svc):
        from unittest.mock import MagicMock

        from agent.social.telegram_handler import TelegramHandler
        agent = MagicMock()
        agent.marketplace = marketplace_svc
        handler = TelegramHandler.__new__(TelegramHandler)
        handler._agent = agent
        return handler

    @pytest.mark.asyncio
    async def test_bid_on_api_item_rejected(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(
            title="Some API", platform="obolos.tech",
            url="https://obolos.tech/api/some-api", category="api",
        )
        await svc._persist_opportunity(opp)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"bid {opp.id}")
        assert "not a work listing" in result.lower()
        assert "/marketplace listings" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_bid_on_listing_allowed(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(
            title="Real Work", platform="obolos.tech",
            url="https://obolos.tech/api/listings/W1",
            skills_required=["python"],
        )
        await svc._persist_opportunity(opp)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"bid {opp.id}")
        assert "bid draft created" in result.lower()
        assert "/marketplace submit" in result
        await svc.close()


# ─────────────────────────────────────────────
# Provider lifecycle: complete, reject, reputation, outcomes
# ─────────────────────────────────────────────


class TestJobLifecycleRoutes:
    """Routes exist for job complete/reject/reputation."""

    def test_jobs_complete_route(self):
        from agent.control.policy import get_external_capability_route
        route = get_external_capability_route("obolos_jobs_complete_primary")
        assert route is not None
        assert route.capability_id == "jobs_complete_v1"

    def test_jobs_reject_route(self):
        from agent.control.policy import get_external_capability_route
        route = get_external_capability_route("obolos_jobs_reject_primary")
        assert route is not None
        assert route.capability_id == "jobs_reject_v1"

    def test_anp_reputation_route(self):
        from agent.control.policy import get_external_capability_route
        route = get_external_capability_route("obolos_anp_reputation_primary")
        assert route is not None
        assert route.capability_id == "anp_reputation_v1"


class TestObolosConnectorLifecycle:
    """Connector lifecycle methods use correct capabilities."""

    @pytest.mark.asyncio
    async def test_submit_job_work(self):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        c = ObolosConnector()
        result = await c.submit_job_work(gateway, "J1", summary="Done", proof="hash123")
        assert result["ok"] is True
        kw = gateway.call_api_via_capability.call_args.kwargs
        assert kw["capability_id"] == "jobs_submit_v1"
        assert kw["resource"] == "J1"
        assert kw["json_payload"]["result"] == "Done"

    @pytest.mark.asyncio
    async def test_complete_job(self):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {"revenue": 50, "currency": "USD"}}
        c = ObolosConnector()
        result = await c.complete_job(gateway, "J1", notes="All good")
        assert result["ok"] is True
        kw = gateway.call_api_via_capability.call_args.kwargs
        assert kw["capability_id"] == "jobs_complete_v1"

    @pytest.mark.asyncio
    async def test_reject_job(self):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        c = ObolosConnector()
        result = await c.reject_job(gateway, "J1", reason="Cannot deliver")
        assert result["ok"] is True
        kw = gateway.call_api_via_capability.call_args.kwargs
        assert kw["capability_id"] == "jobs_reject_v1"

    @pytest.mark.asyncio
    async def test_get_reputation(self):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {"agent_id": "ag1", "score": 85, "jobs_completed": 10},
        }
        c = ObolosConnector()
        rep = await c.get_reputation(gateway, "ag1")
        assert rep is not None
        assert rep["score"] == 85


class TestJobOutcomeModel:
    """JobOutcome model serialization."""

    def test_defaults(self):
        from agent.marketplace.models import JobOutcome, JobOutcomeStatus
        o = JobOutcome(external_job_id="J1")
        assert o.status == JobOutcomeStatus.UNKNOWN
        assert o.revenue_amount is None

    def test_roundtrip(self):
        from agent.marketplace.models import JobOutcome, JobOutcomeStatus
        o = JobOutcome(
            external_job_id="J1", status=JobOutcomeStatus.COMPLETED,
            revenue_amount=100.0, revenue_currency="USD",
        )
        d = o.to_dict()
        o2 = JobOutcome.from_dict(d)
        assert o2.status == JobOutcomeStatus.COMPLETED
        assert o2.revenue_amount == 100.0


class TestServiceJobLifecycle:
    """Service-level job lifecycle with outcome recording."""

    @pytest.mark.asyncio
    async def test_complete_job_records_outcome(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True, "normalized_response": {"revenue": 75, "currency": "USD"},
        }
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        result = await svc.complete_job("obolos.tech", "J1", notes="Done")
        assert result["ok"] is True
        assert result.get("outcome_id")

        outcomes = await svc.list_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].external_job_id == "J1"
        assert outcomes[0].status.value == "completed"
        assert outcomes[0].revenue_amount == 75
        await svc.close()

    @pytest.mark.asyncio
    async def test_reject_job_records_outcome(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        result = await svc.reject_job("obolos.tech", "J1", reason="Cannot do")
        assert result["ok"] is True
        outcomes = await svc.list_outcomes()
        assert outcomes[0].status.value == "rejected"
        await svc.close()

    @pytest.mark.asyncio
    async def test_stats_includes_outcomes(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True, "normalized_response": {},
        }
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        await svc.complete_job("obolos.tech", "J1")
        stats = await svc.get_stats()
        assert stats["outcomes"] == 1
        await svc.close()


class TestTelegramJobLifecycle:
    """Telegram commands for job lifecycle."""

    def _make_handler(self, marketplace_svc):
        from unittest.mock import MagicMock

        from agent.social.telegram_handler import TelegramHandler
        agent = MagicMock()
        agent.marketplace = marketplace_svc
        handler = TelegramHandler.__new__(TelegramHandler)
        handler._agent = agent
        return handler

    @pytest.mark.asyncio
    async def test_job_complete_success(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True, "normalized_response": {"revenue": 50, "currency": "USD"},
        }
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("job-complete J1 Great work")
        assert "completed" in result.lower()
        assert "50" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_job_reject_success(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("job-reject J1 Cannot deliver")
        assert "rejected" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_reputation_success(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True,
            "normalized_response": {"agent_id": "ag1", "score": 90, "jobs_completed": 5, "jobs_failed": 0},
        }
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("reputation ag1")
        assert "90" in result
        assert "ag1" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_report_empty(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("report")
        assert "no marketplace activity" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_report_with_data(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {
            "ok": True, "normalized_response": {"revenue": 100, "currency": "USD"},
        }
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        await svc.complete_job("obolos.tech", "J1")
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("report")
        assert "1 completed" in result
        assert "100" in result  # revenue
        await svc.close()

    @pytest.mark.asyncio
    async def test_help_includes_lifecycle_commands(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("unknowncmd")
        assert "job-submit" in result
        assert "job-complete" in result
        assert "job-reject" in result
        assert "reputation" in result
        assert "report" in result
        assert "link-job" in result
        await svc.close()


# ─────────────────────────────────────────────
# Final stretch: linkage, reporting, edge cases, reload
# ─────────────────────────────────────────────


class TestBidJobLinkage:
    """Linking external job IDs to bids."""

    @pytest.mark.asyncio
    async def test_link_job_to_bid(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        opp = Opportunity(title="Link Test", platform="obolos.tech", url="https://obolos.tech/api/listings/L1")
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech", status=BidStatus.SUBMITTED)
        await svc._persist_bid(bid)

        result = await svc.link_job(bid.id, "EXT-J1")
        assert result["ok"] is True

        reloaded = await svc.get_bid(bid.id)
        assert reloaded.external_job_id == "EXT-J1"
        assert reloaded.status == BidStatus.ACCEPTED
        await svc.close()

    @pytest.mark.asyncio
    async def test_link_job_duplicate_rejected(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        bid = Bid(opportunity_id="opp", platform="test", external_job_id="J1")
        await svc._persist_bid(bid)
        result = await svc.link_job(bid.id, "J2")
        assert result["ok"] is False
        assert "already linked" in result["error"].lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_link_job_same_id_ok(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        bid = Bid(opportunity_id="opp", platform="test", external_job_id="J1")
        await svc._persist_bid(bid)
        result = await svc.link_job(bid.id, "J1")
        assert result["ok"] is True  # idempotent
        await svc.close()

    @pytest.mark.asyncio
    async def test_find_linkage_for_job(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        bid = Bid(
            opportunity_id="opp-1", platform="test",
            external_job_id="J1", project_id="proj-1",
        )
        await svc._persist_bid(bid)
        linkage = await svc._find_linkage_for_job("J1")
        assert linkage["bid_id"] == bid.id
        assert linkage["project_id"] == "proj-1"
        await svc.close()


class TestDuplicateLifecycleTransitions:
    """Edge: repeated complete/reject should be rejected cleanly."""

    @pytest.mark.asyncio
    async def test_duplicate_complete_rejected(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        await svc.complete_job("obolos.tech", "J1")
        result2 = await svc.complete_job("obolos.tech", "J1")
        assert result2["ok"] is False
        assert "already" in result2["error"].lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_duplicate_reject_rejected(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        await svc.reject_job("obolos.tech", "J1")
        result2 = await svc.reject_job("obolos.tech", "J1")
        assert result2["ok"] is False
        assert "already" in result2["error"].lower()
        await svc.close()


class TestPersistenceReload:
    """Lifecycle data survives DB close/reopen."""

    @pytest.mark.asyncio
    async def test_bid_with_job_id_survives_reload(self, tmp_path: Path):
        db = str(tmp_path / "mkt.db")
        svc = MarketplaceService(db_path=db)
        await svc.initialize()
        bid = Bid(
            opportunity_id="opp-1", platform="test",
            external_job_id="EXT-J1", project_id="proj-1",
            status=BidStatus.ACCEPTED,
        )
        await svc._persist_bid(bid)
        await svc.close()

        svc2 = MarketplaceService(db_path=db)
        await svc2.initialize()
        reloaded = await svc2.get_bid(bid.id)
        assert reloaded.external_job_id == "EXT-J1"
        assert reloaded.project_id == "proj-1"
        assert reloaded.status == BidStatus.ACCEPTED
        await svc2.close()

    @pytest.mark.asyncio
    async def test_outcome_survives_reload(self, tmp_path: Path):
        from agent.marketplace.models import JobOutcome, JobOutcomeStatus
        db = str(tmp_path / "mkt.db")
        svc = MarketplaceService(db_path=db)
        await svc.initialize()
        outcome = JobOutcome(
            external_job_id="J1", status=JobOutcomeStatus.COMPLETED,
            revenue_amount=100.0, revenue_currency="USD", platform="obolos.tech",
        )
        await svc._persist_outcome(outcome)
        await svc.close()

        svc2 = MarketplaceService(db_path=db)
        await svc2.initialize()
        outcomes = await svc2.list_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].revenue_amount == 100.0
        assert outcomes[0].status == JobOutcomeStatus.COMPLETED
        await svc2.close()


class TestEnrichedReporting:
    """Report and show commands reflect real linkage."""

    def _make_handler(self, marketplace_svc):
        from unittest.mock import MagicMock

        from agent.social.telegram_handler import TelegramHandler
        agent = MagicMock()
        agent.marketplace = marketplace_svc
        handler = TelegramHandler.__new__(TelegramHandler)
        handler._agent = agent
        return handler

    @pytest.mark.asyncio
    async def test_show_displays_linked_bids(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()

        opp = Opportunity(title="Linked Show", platform="obolos.tech",
                          url="https://obolos.tech/api/listings/LS1")
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech",
                  status=BidStatus.SUBMITTED, external_job_id="J99", project_id="P1")
        await svc._persist_bid(bid)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"show {opp.id}")
        assert "Linked bids" in result
        assert "submitted" in result.lower()
        assert "J99" in result
        assert "P1" in result[:200] or "project" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_bids_shows_job_linkage(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()

        bid = Bid(opportunity_id="opp", platform="test", title="Linked Bid",
                  external_job_id="J42", project_id="P2", price_usd=30.0)
        await svc._persist_bid(bid)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("bids")
        assert "J42" in result
        assert "P2" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_report_lifecycle_summary(self, tmp_path: Path):
        from unittest.mock import AsyncMock


        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {"revenue": 75}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        # Create data
        opp = Opportunity(title="Report Test", platform="obolos.tech",
                          url="https://obolos.tech/api/listings/R1")
        await svc._persist_opportunity(opp)
        bid = Bid(opportunity_id=opp.id, platform="obolos.tech",
                  status=BidStatus.SUBMITTED, project_id="P1")
        await svc._persist_bid(bid)
        await svc.complete_job("obolos.tech", "J1")

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("report")
        assert "1 total" in result  # 1 bid
        assert "1 submitted" in result.lower() or "submitted" in result.lower()
        assert "1 completed" in result
        assert "75" in result  # revenue
        await svc.close()

    @pytest.mark.asyncio
    async def test_link_job_telegram(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        bid = Bid(opportunity_id="opp", platform="test", status=BidStatus.SUBMITTED)
        await svc._persist_bid(bid)

        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"link-job {bid.id} EXT-J5")
        assert "linked" in result.lower()
        assert "EXT-J5" in result
        assert "does not imply" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_link_job_missing_bid(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace("link-job nonexistent J1")
        assert "not found" in result.lower()
        await svc.close()


# ─────────────────────────────────────────────
# Final hardening: hints, job-submit persistence, terminal guards
# ─────────────────────────────────────────────


class TestNonListingHints:
    """API marketplace items must not suggest bidding."""

    def _make_handler(self, marketplace_svc):
        from unittest.mock import MagicMock

        from agent.social.telegram_handler import TelegramHandler
        agent = MagicMock()
        agent.marketplace = marketplace_svc
        handler = TelegramHandler.__new__(TelegramHandler)
        handler._agent = agent
        return handler

    @pytest.mark.asyncio
    async def test_show_api_item_no_bid_hint(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        opp = Opportunity(title="API Item", platform="obolos.tech",
                          url="https://obolos.tech/api/some-slug", category="api")
        await svc._persist_opportunity(opp)
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"show {opp.id}")
        assert "/marketplace bid" not in result
        assert "listings" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_show_listing_has_bid_hint(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        await svc.initialize()
        opp = Opportunity(title="Listing", platform="obolos.tech",
                          url="https://obolos.tech/api/listings/L1")
        await svc._persist_opportunity(opp)
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"show {opp.id}")
        assert "/marketplace bid" in result
        await svc.close()

    @pytest.mark.asyncio
    async def test_eval_api_item_no_bid_hint(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        opp = Opportunity(title="API Eval", platform="obolos.tech",
                          url="https://obolos.tech/api/slug", skills_required=["python"])
        await svc._persist_opportunity(opp)
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"eval {opp.id}")
        assert "/marketplace bid" not in result
        assert "listings only" in result.lower() or "listings" in result.lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_eval_listing_has_bid_hint(self, tmp_path: Path):
        svc = MarketplaceService(db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()
        opp = Opportunity(title="Listing Eval", platform="obolos.tech",
                          url="https://obolos.tech/api/listings/L1", skills_required=["python"])
        await svc._persist_opportunity(opp)
        handler = self._make_handler(svc)
        result = await handler._cmd_marketplace(f"eval {opp.id}")
        assert "/marketplace bid" in result
        await svc.close()


class TestJobSubmitPersistence:
    """submit_job_work must persist a SUBMITTED outcome."""

    @pytest.mark.asyncio
    async def test_submit_persists_outcome(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        result = await svc.submit_job_work("obolos.tech", "J1", summary="Done")
        assert result["ok"] is True
        assert result.get("outcome_id")

        outcomes = await svc.list_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].status.value == "submitted"
        assert outcomes[0].external_job_id == "J1"
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_after_terminal_blocked(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        await svc.complete_job("obolos.tech", "J1")
        result = await svc.submit_job_work("obolos.tech", "J1", summary="Late")
        assert result["ok"] is False
        assert "finalized" in result["error"].lower()
        await svc.close()


class TestContradictoryTerminalGuard:
    """Cannot complete after reject, or reject after complete."""

    @pytest.mark.asyncio
    async def test_complete_then_reject_blocked(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        r1 = await svc.complete_job("obolos.tech", "J1")
        assert r1["ok"] is True
        r2 = await svc.reject_job("obolos.tech", "J1", reason="changed mind")
        assert r2["ok"] is False
        assert "finalized" in r2["error"].lower()
        assert "completed" in r2["error"].lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_reject_then_complete_blocked(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        r1 = await svc.reject_job("obolos.tech", "J1")
        assert r1["ok"] is True
        r2 = await svc.complete_job("obolos.tech", "J1")
        assert r2["ok"] is False
        assert "finalized" in r2["error"].lower()
        assert "rejected" in r2["error"].lower()
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_then_complete_allowed(self, tmp_path: Path):
        """Submit is not terminal — complete should still work after it."""
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        await svc.submit_job_work("obolos.tech", "J1", summary="Work done")
        r2 = await svc.complete_job("obolos.tech", "J1")
        assert r2["ok"] is True
        await svc.close()


# ─────────────────────────────────────────────
# Duplicate job-submit guard
# ─────────────────────────────────────────────


class TestDuplicateJobSubmit:
    """Repeated job-submit must not create duplicate SUBMITTED outcomes."""

    @pytest.mark.asyncio
    async def test_second_submit_blocked(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        r1 = await svc.submit_job_work("obolos.tech", "J1", summary="First")
        assert r1["ok"] is True

        r2 = await svc.submit_job_work("obolos.tech", "J1", summary="Duplicate")
        assert r2["ok"] is False
        assert "already submitted" in r2["error"].lower()
        assert r2.get("existing_outcome_id")

        # Only one SUBMITTED outcome exists
        outcomes = await svc.list_outcomes()
        submitted = [o for o in outcomes if o.external_job_id == "J1" and o.status.value == "submitted"]
        assert len(submitted) == 1
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_then_complete_still_works(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {"revenue": 50}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        await svc.submit_job_work("obolos.tech", "J1", summary="Done")
        r2 = await svc.complete_job("obolos.tech", "J1")
        assert r2["ok"] is True

        outcomes = await svc.list_outcomes()
        statuses = sorted(o.status.value for o in outcomes if o.external_job_id == "J1")
        assert statuses == ["completed", "submitted"]
        await svc.close()

    @pytest.mark.asyncio
    async def test_submit_then_reject_still_works(self, tmp_path: Path):
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        await svc.submit_job_work("obolos.tech", "J1", summary="Done")
        r2 = await svc.reject_job("obolos.tech", "J1", reason="Bad")
        assert r2["ok"] is True
        await svc.close()

    @pytest.mark.asyncio
    async def test_report_no_inflated_submitted(self, tmp_path: Path):
        """Report counts must not inflate from blocked duplicate submits."""
        from unittest.mock import AsyncMock
        gateway = AsyncMock()
        gateway.call_api_via_capability.return_value = {"ok": True, "normalized_response": {}}
        svc = MarketplaceService(gateway=gateway, db_path=str(tmp_path / "mkt.db"))
        svc.registry.register(ObolosConnector())
        await svc.initialize()

        await svc.submit_job_work("obolos.tech", "J1", summary="First")
        await svc.submit_job_work("obolos.tech", "J1", summary="Dup")  # blocked
        await svc.submit_job_work("obolos.tech", "J1", summary="Dup2")  # blocked

        outcomes = await svc.list_outcomes()
        submitted = [o for o in outcomes if o.status.value == "submitted"]
        assert len(submitted) == 1  # no inflation
        await svc.close()
