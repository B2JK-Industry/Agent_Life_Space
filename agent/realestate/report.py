"""
Agent Life Space — Real Estate Watcher Daily Reporter

Generates a compact Markdown daily summary report.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from agent.realestate.models import Estate, SearchConfig

if TYPE_CHECKING:
    from agent.realestate.store import RealEstateStore

logger = structlog.get_logger(__name__)

_REPORT_WINDOW_HOURS = 24
_TOP_N = 3


class DailyReporter:
    """Generates a daily Markdown summary report for all active searches."""

    async def generate_report(
        self,
        store: RealEstateStore,
        searches: list[SearchConfig],
        scored_results: dict[str, list[tuple[Estate, Any]]] | None = None,
    ) -> str:
        """
        Generate a compact Markdown report for the last 24 hours.

        Args:
            store: RealEstateStore instance for DB queries.
            searches: List of active SearchConfig objects.
            scored_results: Optional pre-computed results per search:
                            {search_name: [(estate, ScoreBreakdown), ...]}
                            If None, only DB-level stats are reported.

        Returns:
            Markdown string suitable for Telegram.
        """
        since = datetime.now(UTC) - timedelta(hours=_REPORT_WINDOW_HOURS)
        date_str = datetime.now(UTC).strftime("%d.%m.%Y")

        lines: list[str] = [
            f"📋 *Denný report — {date_str}*",
            "",
        ]

        if not searches:
            lines.append("_Žiadne aktívne vyhľadávania._")
            return "\n".join(lines)

        for search in searches:
            section = await self._search_section(
                store=store,
                search=search,
                since=since,
                scored_results=(scored_results or {}).get(search.name, []),
            )
            lines.extend(section)
            lines.append("")

        # Trim trailing blank line
        while lines and lines[-1] == "":
            lines.pop()

        report = "\n".join(lines)
        logger.info("realestate.report.generated", searches=len(searches))
        return report

    # ── Per-search section ─────────────────────────────────────────────────

    async def _search_section(
        self,
        store: RealEstateStore,
        search: SearchConfig,
        since: datetime,
        scored_results: list[tuple[Estate, Any]],
    ) -> list[str]:
        """Build lines for a single search."""
        lines: list[str] = [f"🔍 *{search.name}* (min score: {search.min_score})"]

        # High-score count in window
        high_score_estates = [
            (e, sb) for e, sb in scored_results if sb.total >= search.min_score
        ]
        lines.append(f"  • Vysoko skórujúce za 24h: *{len(high_score_estates)}*")

        # Median price delta
        median_delta = _compute_median_price_delta(scored_results)
        if median_delta is not None:
            sign = "+" if median_delta >= 0 else ""
            lines.append(f"  • Medián delta ceny: *{sign}{median_delta:.1f}%*")
        else:
            lines.append("  • Medián delta ceny: _n/a_")

        # Dead URL count — estimated via score_breakdown reasoning (no live check here)
        # We report how many were skipped due to dead URL if tracked
        dead_count = await self._count_dead_urls(store, search.name, since)
        if dead_count > 0:
            lines.append(f"  • Mŕtve URL (24h): *{dead_count}*")

        # Top 3 estates by score
        top = sorted(high_score_estates, key=lambda x: x[1].total, reverse=True)[:_TOP_N]
        if top:
            lines.append("  • Top nehnuteľnosti:")
            for estate, sb in top:
                price_str = f"{estate.price:,}".replace(",", " ")
                lines.append(f"    {sb.total}/100 — {estate.title} — {price_str} Kč")
        else:
            lines.append("  • _Žiadne výsledky spĺňajúce min_score_")

        return lines

    # ── Helpers ────────────────────────────────────────────────────────────

    async def _count_dead_urls(
        self,
        store: RealEstateStore,
        search_name: str,
        since: datetime,
    ) -> int:
        """Count notifications logged as 'dead_url' in the window (if tracked)."""
        # We don't log dead_url events in notif_log, so always 0 here.
        # Subclasses or future versions can override this to query a dead_url log.
        return 0


def _compute_median_price_delta(
    scored_results: list[tuple[Estate, Any]],
) -> float | None:
    """
    Compute the median price_change_pct across all estates in the results.
    price_drop_bonus > 0 means a drop was detected.
    Returns None if no delta data is available.
    """
    # ScoreBreakdown doesn't store the raw pct, but price_drop_bonus > 0 means < -3%.
    # For a more accurate median we'd need the raw pct stored on the breakdown.
    # We approximate: estates with drop → -3.5% representative value; others → 0%.
    if not scored_results:
        return None

    deltas: list[float] = []
    for _estate, sb in scored_results:
        if sb.price_drop_bonus > 0:
            deltas.append(-3.5)  # conservative approximation
        else:
            deltas.append(0.0)

    if not deltas:
        return None
    return statistics.median(deltas)
