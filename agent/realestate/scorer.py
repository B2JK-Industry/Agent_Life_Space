"""
Agent Life Space — Real Estate Watcher Scorer

Deterministic 0-100 scoring for estate listings.
No LLM — pure arithmetic on estate attributes.
"""

from __future__ import annotations

import statistics

import structlog

from agent.realestate.models import Estate, ScoreBreakdown, SearchConfig

logger = structlog.get_logger(__name__)

_SCAM_PRICE_THRESHOLD = 500_000  # CZK — below this → scam filter, score = 0
_AREA_BONUS_THRESHOLD = 50.0     # m²
_PRICE_DROP_THRESHOLD = -3.0     # percent


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


class RealEstateScorer:
    """Deterministic scorer — no external calls, no LLM."""

    def score(
        self,
        estate: Estate,
        search: SearchConfig,  # noqa: ARG002 — reserved for future per-search weights
        median_price_m2: float,
        price_change_pct: float | None = None,
    ) -> ScoreBreakdown:
        """
        Compute a 0-100 score for a single estate.

        Components:
        - base: 50
        - price_score: clamp(-20..+30) = (median - price_per_m2) / median * 100
        - area_bonus: +10 if area >= 50 m²
        - price_drop_bonus: +30 if price_change_pct < -3%
        - floor_plan_bonus: +5 if has_floor_plan
        - label_bonus: +5 if any label contains 'nov' or 'exkluz'
        - scam_penalty: if price < 500k → total = 0
        """
        reasons: list[str] = []

        # ── Price vs. median ──────────────────────────────────────────────
        if median_price_m2 > 0 and estate.price_per_m2 > 0:
            raw_price_score = int(
                (median_price_m2 - estate.price_per_m2) / median_price_m2 * 100
            )
            price_score = _clamp(raw_price_score, -20, 30)
        else:
            price_score = 0

        if price_score > 0:
            reasons.append(
                f"Cena/m² {estate.price_per_m2:,.0f} Kč pod mediánom {median_price_m2:,.0f} Kč"
            )
        elif price_score < 0:
            reasons.append(
                f"Cena/m² {estate.price_per_m2:,.0f} Kč nad mediánom {median_price_m2:,.0f} Kč"
            )

        # ── Area bonus ────────────────────────────────────────────────────
        area_bonus = 10 if estate.area_m2 >= _AREA_BONUS_THRESHOLD else 0
        if area_bonus:
            reasons.append(f"Plocha {estate.area_m2:.0f} m² (≥50 m²)")

        # ── Price drop bonus ──────────────────────────────────────────────
        price_drop_bonus = 0
        if price_change_pct is not None and price_change_pct < _PRICE_DROP_THRESHOLD:
            price_drop_bonus = 30
            reasons.append(f"Pokles ceny o {price_change_pct:.1f}%")

        # ── Floor plan bonus ──────────────────────────────────────────────
        floor_plan_bonus = 5 if estate.has_floor_plan else 0
        if floor_plan_bonus:
            reasons.append("Má pôdorys")

        # ── Label bonus ───────────────────────────────────────────────────
        label_bonus = 0
        label_lower = [lbl.lower() for lbl in estate.labels]
        if any("nov" in lbl or "exkluz" in lbl for lbl in label_lower):
            label_bonus = 5
            matched = [lbl for lbl in estate.labels if "nov" in lbl.lower() or "exkluz" in lbl.lower()]
            reasons.append(f"Label: {', '.join(matched)}")

        # ── Raw total ─────────────────────────────────────────────────────
        base = 50
        raw_total = base + price_score + area_bonus + price_drop_bonus + floor_plan_bonus + label_bonus

        # ── Scam filter ───────────────────────────────────────────────────
        scam_penalty = 0
        if estate.price > 0 and estate.price < _SCAM_PRICE_THRESHOLD:
            scam_penalty = raw_total  # drives total to 0
            reasons = [f"SCAM FILTER: cena {estate.price:,} Kč < 500 000 Kč"]
            raw_total = 0

        total = _clamp(raw_total, 0, 100)

        breakdown = ScoreBreakdown(
            price_score=price_score,
            area_bonus=area_bonus,
            price_drop_bonus=price_drop_bonus,
            floor_plan_bonus=floor_plan_bonus,
            label_bonus=label_bonus,
            scam_penalty=scam_penalty,
            total=total,
            reasons=reasons,
        )

        logger.debug(
            "realestate.scorer.scored",
            hash_id=estate.hash_id,
            total=total,
            price_change_pct=price_change_pct,
        )
        return breakdown

    def compute_median_price_m2(self, estates: list[Estate]) -> float:
        """Return median price/m² from a list of estates. Returns 0.0 if empty."""
        values = [e.price_per_m2 for e in estates if e.price_per_m2 > 0]
        if not values:
            return 0.0
        return statistics.median(values)
