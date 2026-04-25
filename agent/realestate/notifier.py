"""
Agent Life Space — Real Estate Watcher Notifier

Sends Telegram notifications for high-scoring estates.
Implements 24-hour idempotency deduplication via notif_log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from agent.realestate.models import Estate, ScoreBreakdown

if TYPE_CHECKING:
    from agent.realestate.store import RealEstateStore

logger = structlog.get_logger(__name__)

_DEFAULT_CHAT_ID = "6698890771"
_HEAD_TIMEOUT = 5.0       # seconds for URL liveness check
_EVENT_NEW = "new_listing"
_EVENT_PRICE_DROP = "price_drop"


class RealEstateNotifier:
    """
    Sends scored estate notifications to Telegram.

    Guarantees:
    - 24-hour deduplication via store.check_notified / store.log_notification
    - HEAD check before sending (dead URLs are skipped)
    - Message max 4 lines
    """

    def __init__(
        self,
        store: RealEstateStore,
        telegram_bot: Any,  # duck-typed: needs .send_message(chat_id, text, parse_mode)
        chat_id: str = _DEFAULT_CHAT_ID,
    ) -> None:
        self._store = store
        self._bot = telegram_bot
        self._chat_id = chat_id

    # ── Public API ─────────────────────────────────────────────────────────

    async def notify_estate(
        self,
        estate: Estate,
        score_breakdown: ScoreBreakdown,
        search_name: str,
        min_score: int = 60,
    ) -> bool:
        """
        Send a Telegram notification for an estate if:
        1. score >= min_score
        2. Not already notified in the last 24 hours
        3. URL is alive (HEAD check passes)

        Returns True if notification was sent.
        """
        if score_breakdown.total < min_score:
            return False

        event_type = (
            _EVENT_PRICE_DROP if score_breakdown.price_drop_bonus > 0 else _EVENT_NEW
        )

        # 24h dedup check
        already_sent = await self._store.check_notified(estate.hash_id, event_type)
        if already_sent:
            logger.debug(
                "realestate.notifier.dedup_skip",
                hash_id=estate.hash_id,
                event_type=event_type,
            )
            return False

        # HEAD liveness check
        if not await self._is_url_alive(estate.url):
            logger.warning(
                "realestate.notifier.dead_url",
                hash_id=estate.hash_id,
                url=estate.url,
            )
            return False

        # Build and send message (max 4 lines)
        message = self._build_message(estate, score_breakdown, search_name)
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=message,
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error(
                "realestate.notifier.send_error",
                hash_id=estate.hash_id,
                error=str(exc),
            )
            return False

        await self._store.log_notification(estate.hash_id, event_type)
        logger.info(
            "realestate.notifier.sent",
            hash_id=estate.hash_id,
            score=score_breakdown.total,
            event_type=event_type,
        )
        return True

    # ── Internals ──────────────────────────────────────────────────────────

    def _build_message(
        self,
        estate: Estate,
        sb: ScoreBreakdown,
        search_name: str,
    ) -> str:
        """Build a 4-line Telegram Markdown message."""
        # Line 1: title + score badge
        line1 = f"*{estate.title}* — skóre {sb.total}/100"

        # Line 2: price
        price_str = f"{estate.price:,}".replace(",", " ")
        area_str = f"{estate.area_m2:.0f} m²" if estate.area_m2 > 0 else ""
        if area_str:
            line2 = f"💰 {price_str} Kč | {area_str}"
        else:
            line2 = f"💰 {price_str} Kč"

        # Line 3: top reason (first non-empty)
        top_reason = sb.reasons[0] if sb.reasons else f"Search: {search_name}"
        if sb.price_drop_bonus > 0:
            # Find price drop reason for prominence
            drop_reasons = [r for r in sb.reasons if "Pokles" in r]
            if drop_reasons:
                top_reason = drop_reasons[0]
        line3 = f"📊 {top_reason}"

        # Line 4: URL
        line4 = estate.url

        return "\n".join([line1, line2, line3, line4])

    @staticmethod
    async def _is_url_alive(url: str, timeout: float = _HEAD_TIMEOUT) -> bool:
        """Return True if the URL responds with HTTP 2xx/3xx."""
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                resp = await client.head(url)
                return resp.status_code < 400
        except Exception:
            return False
