"""Tests for RealEstateScorer — deterministic 0-100 scoring."""
from __future__ import annotations

from agent.realestate.models import Estate, SearchConfig
from agent.realestate.scorer import RealEstateScorer

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_estate(**kwargs) -> Estate:
    defaults = dict(
        hash_id=12345,
        title="Test byt",
        price=5_000_000,
        area_m2=55.0,
        price_per_m2=90_909.0,
        url="https://example.com",
        category_type=1,
        category_main=1,
        category_sub=4,
    )
    defaults.update(kwargs)
    return Estate(**defaults)


def _make_search(**kwargs) -> SearchConfig:
    defaults = dict(name="test_search", params_json={}, min_score=60)
    defaults.update(kwargs)
    return SearchConfig(**defaults)


SCORER = RealEstateScorer()
MEDIAN = 100_000.0  # Kč/m²


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_basic_score():
    """Estate at exactly median price, area<50, no extras → around 50."""
    estate = _make_estate(price=5_000_000, area_m2=40.0, price_per_m2=MEDIAN)
    sb = SCORER.score(estate, _make_search(), median_price_m2=MEDIAN)
    # base=50, price_score=0, area_bonus=0, no other bonuses → total=50
    assert sb.total == 50
    assert sb.area_bonus == 0
    assert sb.price_drop_bonus == 0
    assert sb.floor_plan_bonus == 0
    assert sb.label_bonus == 0


def test_area_bonus():
    """Estate with area >= 50 m² gets +10."""
    estate = _make_estate(area_m2=50.0, price_per_m2=MEDIAN)
    sb = SCORER.score(estate, _make_search(), median_price_m2=MEDIAN)
    assert sb.area_bonus == 10
    assert sb.total == 60  # 50 base + 10 area

    estate_below = _make_estate(area_m2=49.9, price_per_m2=MEDIAN)
    sb_below = SCORER.score(estate_below, _make_search(), median_price_m2=MEDIAN)
    assert sb_below.area_bonus == 0


def test_price_drop_bonus():
    """Price drop > 3% triggers +30 bonus."""
    estate = _make_estate(area_m2=40.0, price_per_m2=MEDIAN)
    sb = SCORER.score(estate, _make_search(), median_price_m2=MEDIAN, price_change_pct=-5.0)
    assert sb.price_drop_bonus == 30
    assert sb.total == 80  # 50 + 30

    # Exactly -3% should NOT trigger (threshold is strictly < -3.0)
    sb_exact = SCORER.score(estate, _make_search(), median_price_m2=MEDIAN, price_change_pct=-3.0)
    assert sb_exact.price_drop_bonus == 0

    # Just below threshold
    sb_just = SCORER.score(estate, _make_search(), median_price_m2=MEDIAN, price_change_pct=-3.1)
    assert sb_just.price_drop_bonus == 30


def test_scam_filter():
    """Price < 500 000 CZK → total score = 0."""
    estate = _make_estate(price=499_999, area_m2=60.0, price_per_m2=MEDIAN)
    sb = SCORER.score(estate, _make_search(), median_price_m2=MEDIAN, price_change_pct=-10.0)
    assert sb.total == 0
    assert sb.scam_penalty > 0


def test_floor_plan_label_bonus():
    """has_floor_plan → +5, label 'nová' → +5."""
    estate = _make_estate(
        area_m2=40.0,
        price_per_m2=MEDIAN,
        has_floor_plan=True,
        labels=["nová"],
    )
    sb = SCORER.score(estate, _make_search(), median_price_m2=MEDIAN)
    assert sb.floor_plan_bonus == 5
    assert sb.label_bonus == 5
    assert sb.total == 60  # 50 + 5 + 5

    # 'exkluzívne' also triggers label bonus
    estate2 = _make_estate(area_m2=40.0, price_per_m2=MEDIAN, labels=["exkluzívne"])
    sb2 = SCORER.score(estate2, _make_search(), median_price_m2=MEDIAN)
    assert sb2.label_bonus == 5


def test_deterministic():
    """Same input always produces same output."""
    estate = _make_estate(
        price=5_000_000, area_m2=55.0, price_per_m2=MEDIAN,
        has_floor_plan=True, labels=["nová"],
    )
    search = _make_search()
    results = [SCORER.score(estate, search, median_price_m2=MEDIAN, price_change_pct=-4.0) for _ in range(5)]
    totals = [r.total for r in results]
    assert len(set(totals)) == 1, f"Non-deterministic: {totals}"


def test_price_score_below_median():
    """Estate cheaper than median gets positive price_score."""
    estate = _make_estate(area_m2=40.0, price_per_m2=80_000.0)  # 20% below MEDIAN
    sb = SCORER.score(estate, _make_search(), median_price_m2=MEDIAN)
    assert sb.price_score > 0
    assert sb.total > 50


def test_compute_median_price_m2():
    """compute_median_price_m2 returns correct median."""
    estates = [
        _make_estate(hash_id=i, price_per_m2=float(v))
        for i, v in enumerate([80_000, 100_000, 120_000])
    ]
    median = SCORER.compute_median_price_m2(estates)
    assert median == 100_000.0

    assert SCORER.compute_median_price_m2([]) == 0.0
