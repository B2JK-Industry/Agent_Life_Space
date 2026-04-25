"""Tests for DailyReporter — report aggregation, top-3, median delta."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.realestate.models import Estate, ScoreBreakdown, SearchConfig
from agent.realestate.report import DailyReporter
from agent.realestate.store import RealEstateStore


def _estate(hash_id: int, price: int, title: str = "Test byt") -> Estate:
    return Estate(
        hash_id=hash_id,
        title=title,
        price=price,
        area_m2=60.0,
        price_per_m2=price / 60.0,
        url="https://example.com",
        category_type=1,
        category_main=1,
        category_sub=4,
    )


def _breakdown(total: int, price_drop_bonus: int = 0) -> ScoreBreakdown:
    return ScoreBreakdown(
        price_score=5,
        area_bonus=10,
        price_drop_bonus=price_drop_bonus,
        floor_plan_bonus=0,
        label_bonus=0,
        scam_penalty=0,
        total=total,
        reasons=[],
    )


def _search(name: str = "test_search", min_score: int = 60) -> SearchConfig:
    return SearchConfig(name=name, params_json={}, min_score=min_score)


def _mock_store() -> MagicMock:
    store = MagicMock(spec=RealEstateStore)
    store.count_price_drops = AsyncMock(return_value=0)
    return store


# ── Tests ──────────────────────────────────────────────────────────────────────


async def test_report_aggregation() -> None:
    """Report includes count, top-3 listing, median delta."""
    store = _mock_store()
    reporter = DailyReporter()
    search = _search(min_score=60)

    scored: list[tuple[Estate, ScoreBreakdown]] = [
        (_estate(1, 4_000_000, "Byt A"), _breakdown(total=80)),
        (_estate(2, 5_000_000, "Byt B"), _breakdown(total=70)),
        (_estate(3, 6_000_000, "Byt C"), _breakdown(total=65)),
        (_estate(4, 7_000_000, "Byt D"), _breakdown(total=55)),  # below min_score
    ]

    report = await reporter.generate_report(
        store=store,
        searches=[search],
        scored_results={"test_search": scored},
    )

    # High-score count = 3 (Byt D score=55 < 60)
    assert "Vysoko skórujúce za 24h: *3*" in report

    # Top 3 present
    assert "Byt A" in report
    assert "Byt B" in report
    assert "Byt C" in report
    # Byt D (score 55) should NOT be in top section
    assert "Byt D" not in report


async def test_report_no_searches() -> None:
    """Report with no searches shows placeholder."""
    store = _mock_store()
    reporter = DailyReporter()
    report = await reporter.generate_report(store=store, searches=[])
    assert "Žiadne aktívne vyhľadávania" in report


async def test_report_median_delta_with_price_drop() -> None:
    """When there are price drops, median delta is shown."""
    store = _mock_store()
    reporter = DailyReporter()
    search = _search(min_score=60)

    scored: list[tuple[Estate, ScoreBreakdown]] = [
        (_estate(1, 4_000_000), _breakdown(total=80, price_drop_bonus=30)),
        (_estate(2, 5_000_000), _breakdown(total=70, price_drop_bonus=30)),
    ]

    report = await reporter.generate_report(
        store=store,
        searches=[search],
        scored_results={"test_search": scored},
    )

    assert "Medián delta ceny" in report
    # Both estates had drops → median should be negative
    assert "-3.5%" in report or "−3.5%" in report or "-3" in report


async def test_report_header_contains_date() -> None:
    """Report starts with daily report header."""
    store = _mock_store()
    reporter = DailyReporter()
    report = await reporter.generate_report(store=store, searches=[_search()])
    assert "Denný report" in report


async def test_report_multiple_searches() -> None:
    """Report sections exist for each search."""
    store = _mock_store()
    reporter = DailyReporter()
    searches = [_search("search_alpha"), _search("search_beta")]

    report = await reporter.generate_report(
        store=store,
        searches=searches,
        scored_results={},
    )

    assert "search_alpha" in report
    assert "search_beta" in report
