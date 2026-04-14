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
