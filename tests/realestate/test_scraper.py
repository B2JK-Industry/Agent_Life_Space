"""Tests for RealtyScraper — fixture-based parsing and client-side filtering."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.realestate.models import SearchConfig
from agent.realestate.scraper import RealtyScraper, _parse_estate
from agent.realestate.store import RealEstateStore


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_response() -> dict:
    with open(FIXTURES_DIR / "sreality_response.json") as f:
        return json.load(f)


@pytest.fixture
def mock_store(tmp_path) -> RealEstateStore:
    store = MagicMock(spec=RealEstateStore)
    store.upsert_price = AsyncMock(return_value=None)
    return store


@pytest.fixture
def mock_http(sample_response):
    """httpx.AsyncClient mock that returns the fixture on first page, empty on page 2+."""
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = sample_response
    response.raise_for_status = MagicMock()
    client.get = AsyncMock(return_value=response)
    return client


def _make_search(price_max: int | None = None) -> SearchConfig:
    params: dict = {"category_sub_cb": 4}
    if price_max is not None:
        params["price_max"] = price_max
    return SearchConfig(name="test", params_json=params)


# ── Unit: _parse_estate ────────────────────────────────────────────────────────


def test_parse_response_fixture(sample_response):
    """Fixture JSON parses into correct Estate objects."""
    raw_estates = sample_response["_embedded"]["estates"]
    estates = [_parse_estate(raw) for raw in raw_estates]
    estates = [e for e in estates if e is not None]

    assert len(estates) == 3

    first = next(e for e in estates if e.hash_id == 11111111)
    assert first.title == "Prodej bytu 2+kk 60 m²"
    assert first.price == 5_500_000
    assert first.area_m2 == 60.0
    assert first.price_per_m2 == pytest.approx(91_666.67, abs=1.0)
    assert first.has_floor_plan is True
    assert "nová" in first.labels
    assert first.category_type == 1
    assert first.category_main == 1
    assert first.category_sub == 4
    assert "prodej" in first.url
    assert "byt" in first.url
    assert "2+kk" in first.url or "p" in first.url


def test_parse_missing_hash_id():
    """Raw dict without hash_id returns None."""
    assert _parse_estate({}) is None
    assert _parse_estate({"name": "No ID"}) is None


# ── Integration: fetch_listings with client-side price filter ──────────────────


async def test_client_side_price_filter(mock_store, mock_http, sample_response):
    """Estates with price > price_max are filtered out."""
    # Estate 22222222 costs 9_000_000 and 33333333 costs 3_500_000
    # With price_max=6_000_000: only hash_ids 11111111 and 33333333 pass
    search = _make_search(price_max=6_000_000)
    scraper = RealtyScraper(store=mock_store, http_client=mock_http)

    # Make second page return empty to stop pagination
    empty_response = {"_embedded": {"estates": []}}
    mock_http.get = AsyncMock(side_effect=[
        _mock_resp(sample_response),
        _mock_resp(empty_response),
    ])

    estates = await scraper.fetch_listings(search)
    prices = [e.price for e in estates]
    assert all(p <= 6_000_000 for p in prices), f"Expected all <= 6M, got {prices}"
    assert 9_000_000 not in prices
    hash_ids = [e.hash_id for e in estates]
    assert 11111111 in hash_ids
    assert 22222222 not in hash_ids


async def test_fetch_no_price_max(mock_store, mock_http, sample_response):
    """Without price_max all estates from fixture are returned."""
    search = _make_search()
    scraper = RealtyScraper(store=mock_store, http_client=mock_http)
    mock_http.get = AsyncMock(side_effect=[
        _mock_resp(sample_response),
        _mock_resp({"_embedded": {"estates": []}}),
    ])
    estates = await scraper.fetch_listings(search)
    assert len(estates) == 3


def _mock_resp(data: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp
