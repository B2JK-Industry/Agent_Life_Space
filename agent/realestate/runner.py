"""
Agent Life Space — Real Estate Watcher Runner

Orchestrates the scrape → score → notify pipeline for all active searches.
Handles per-search warm-up (silent first run) and daily report dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from agent.realestate.models import SearchConfig

if TYPE_CHECKING:
    from agent.realestate.notifier import RealEstateNotifier
    from agent.realestate.report import DailyReporter
    from agent.realestate.scorer import RealEstateScorer
    from agent.realestate.scraper import RealtyScraper
    from agent.realestate.store import RealEstateStore

logger = structlog.get_logger(__name__)

_DEFAULT_CHAT_ID = "6698890771"
_DEFAULT_SEARCH_NAME = "praha_2kk_8m"
_DEFAULT_SEARCH_PARAMS: dict[str, Any] = {
    "category_type_cb": 1,
    "category_main_cb": 1,
    "category_sub_cb": 4,
    "locality_region_id": 10,
    "price_max": 8000000,
    "per_page": 60,
}


class RealEstateRunner:
    """
    Orchestrates the real estate watcher pipeline.

    Responsibilities:
    - Per-search warm-up: silent first run stores baseline prices, skips notifications.
    - run_cycle: fetch → compute median → score → notify (if notifier + bot set).
    - run_report: generate Markdown daily summary and send via Telegram.
    - setup_default_search: insert 'praha_2kk_8m' if missing from store.
    - initialize: ensure_tables + default_search + silent warm-up for all active searches.
    """

    def __init__(
        self,
        store: RealEstateStore,
        scraper: RealtyScraper,
        scorer: RealEstateScorer,
        notifier: RealEstateNotifier | None = None,
        reporter: DailyReporter | None = None,
    ) -> None:
        self.store = store
        self._scraper = scraper
        self._scorer = scorer
        self.notifier = notifier
        self._reporter = reporter
        # In-memory set of search names that have completed warm-up.
        # New searches (added via Telegram) are auto-detected as unwarmed
        # on the next run_cycle call.
        self._warmed_up: set[str] = set()

    # ── Public lifecycle ────────────────────────────────────────────────────

    def set_telegram_bot(self, bot: Any) -> None:
        """Inject the Telegram bot into the notifier (called after telegram init)."""
        if self.notifier is not None:
            self.notifier._bot = bot

    async def initialize(self) -> None:
        """Ensure DB tables exist, insert default search, run warm-up cycle."""
        await self.store.ensure_tables()
        await self.setup_default_search()
        # Warm-up: persist baseline prices silently for all active searches.
        await self.run_cycle(silent=True)
        logger.info("realestate.runner.initialized")

    async def setup_default_search(self) -> None:
        """Insert the default 'praha_2kk_8m' search config if it does not exist."""
        existing = await self.store.get_search(_DEFAULT_SEARCH_NAME)
        if existing is not None:
            return
        config = SearchConfig(
            name=_DEFAULT_SEARCH_NAME,
            params_json=_DEFAULT_SEARCH_PARAMS,
            active=True,
            min_score=60,
        )
        await self.store.add_search(config)
        logger.info(
            "realestate.runner.default_search_created",
            name=_DEFAULT_SEARCH_NAME,
        )

    # ── Pipeline ────────────────────────────────────────────────────────────

    async def run_cycle(self, silent: bool = False) -> None:
        """
        Scrape → score → notify for all active searches.

        silent=True  — warm-up mode: prices are stored but notifications are skipped.
        New searches (not yet in self._warmed_up) are automatically treated as warm-up
        regardless of the silent flag, so their first cycle never fires notifications.
        """
        searches = await self.store.list_active()
        if not searches:
            logger.debug("realestate.runner.no_active_searches")
            return

        for search in searches:
            try:
                await self._process_search(search, silent=silent)
            except Exception:
                logger.exception(
                    "realestate.runner.search_error", search=search.name
                )

    async def run_report(self) -> None:
        """Generate and send the daily Markdown report via Telegram."""
        if self._reporter is None:
            logger.warning("realestate.runner.no_reporter")
            return

        searches = await self.store.list_all()
        report_text = await self._reporter.generate_report(self.store, searches)

        bot = self.notifier._bot if self.notifier is not None else None
        chat_id = (
            self.notifier._chat_id if self.notifier is not None else _DEFAULT_CHAT_ID
        )

        if bot is None:
            logger.warning("realestate.runner.report_no_bot")
            return

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=report_text,
                parse_mode="Markdown",
            )
            logger.info("realestate.runner.report_sent", searches=len(searches))
        except Exception:
            logger.exception("realestate.runner.report_send_error")

    # ── Internals ────────────────────────────────────────────────────────────

    async def _process_search(self, search: SearchConfig, *, silent: bool) -> None:
        """Fetch, score, and notify estates for one search config."""
        # New searches always get a silent warm-up on first encounter.
        is_warmup = silent or (search.name not in self._warmed_up)
        estates = await self._scraper.scrape_search(search, silent=is_warmup)
        self._warmed_up.add(search.name)

        # scrape_search returns [] in silent/warm-up mode — nothing to score.
        if not estates:
            return

        # Compute median price/m² for this batch (used as scoring baseline).
        median = self._scorer.compute_median_price_m2(estates)

        for estate in estates:
            price_change = await self._get_price_change_pct(
                estate.hash_id, search.name
            )
            breakdown = self._scorer.score(estate, search, median, price_change)

            if self.notifier is not None and self.notifier._bot is not None:
                await self.notifier.notify_estate(
                    estate,
                    breakdown,
                    search.name,
                    search.min_score,
                )

    async def _get_price_change_pct(
        self,
        hash_id: int,
        search_name: str,
    ) -> float | None:
        """
        Return % price change between the last two snapshots for an estate.

        Returns None for new listings (fewer than 2 records).
        history is sorted DESC, so history[0] is the most recent snapshot.
        """
        history = await self.store.get_price_history(hash_id, search_name, limit=2)
        if len(history) < 2:
            return None
        current_price = history[0].price
        previous_price = history[1].price
        if previous_price == 0:
            return None
        return (current_price - previous_price) / previous_price * 100.0
