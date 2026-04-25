"""Tests for RealEstateNotifier — Telegram send, idempotency, threshold."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.realestate.models import Estate, ScoreBreakdown
from agent.realestate.notifier import RealEstateNotifier
from agent.realestate.store import RealEstateStore


def _make_estate(hash_id: int = 42, price: int = 5_000_000) -> Estate:
    return Estate(
        hash_id=hash_id,
        title="Test byt 2+kk",
        price=price,
        area_m2=60.0,
        price_per_m2=83_333.0,
        url="https://www.sreality.cz/detail/prodej/byt/2+kk/p/42",
        category_type=1,
        category_main=1,
        category_sub=4,
    )


def _make_breakdown(total: int = 75, price_drop_bonus: int = 0) -> ScoreBreakdown:
    return ScoreBreakdown(
        price_score=10,
        area_bonus=10,
        price_drop_bonus=price_drop_bonus,
        floor_plan_bonus=5,
        label_bonus=0,
        scam_penalty=0,
        total=total,
        reasons=["Cena pod mediánom"],
    )


def _make_store(already_notified: bool = False) -> MagicMock:
    store = MagicMock(spec=RealEstateStore)
    store.check_notified = AsyncMock(return_value=already_notified)
    store.log_notification = AsyncMock()
    return store


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# ── Tests ──────────────────────────────────────────────────────────────────────


async def test_notify_sends_message() -> None:
    """Score >= min_score and URL alive → send_message called once."""
    store = _make_store(already_notified=False)
    bot = _make_bot()
    notifier = RealEstateNotifier(store=store, telegram_bot=bot)

    with patch.object(RealEstateNotifier, "_is_url_alive", new=AsyncMock(return_value=True)):
        sent = await notifier.notify_estate(
            _make_estate(), _make_breakdown(total=75), search_name="test_search", min_score=60
        )

    assert sent is True
    bot.send_message.assert_awaited_once()
    store.log_notification.assert_awaited_once()


async def test_idempotency() -> None:
    """Same estate notified twice → send_message called only once (dedup)."""
    bot = _make_bot()
    # First call: not notified yet; second: already notified
    store = MagicMock(spec=RealEstateStore)
    store.log_notification = AsyncMock()
    store.check_notified = AsyncMock(side_effect=[False, True])

    notifier = RealEstateNotifier(store=store, telegram_bot=bot)

    with patch.object(RealEstateNotifier, "_is_url_alive", new=AsyncMock(return_value=True)):
        sent1 = await notifier.notify_estate(
            _make_estate(), _make_breakdown(total=75), search_name="test", min_score=60
        )
        sent2 = await notifier.notify_estate(
            _make_estate(), _make_breakdown(total=75), search_name="test", min_score=60
        )

    assert sent1 is True
    assert sent2 is False
    assert bot.send_message.await_count == 1


async def test_below_threshold() -> None:
    """Score < min_score → not sent, no store interaction."""
    store = _make_store()
    bot = _make_bot()
    notifier = RealEstateNotifier(store=store, telegram_bot=bot)

    sent = await notifier.notify_estate(
        _make_estate(), _make_breakdown(total=40), search_name="test", min_score=60
    )

    assert sent is False
    bot.send_message.assert_not_awaited()
    store.check_notified.assert_not_awaited()


async def test_dead_url_not_sent() -> None:
    """Dead URL → not sent even if score is high."""
    store = _make_store(already_notified=False)
    bot = _make_bot()
    notifier = RealEstateNotifier(store=store, telegram_bot=bot)

    with patch.object(RealEstateNotifier, "_is_url_alive", new=AsyncMock(return_value=False)):
        sent = await notifier.notify_estate(
            _make_estate(), _make_breakdown(total=90), search_name="test", min_score=60
        )

    assert sent is False
    bot.send_message.assert_not_awaited()


async def test_price_drop_event_type() -> None:
    """Price drop breakdown → event_type is 'price_drop'."""
    store = _make_store(already_notified=False)
    bot = _make_bot()
    notifier = RealEstateNotifier(store=store, telegram_bot=bot)

    with patch.object(RealEstateNotifier, "_is_url_alive", new=AsyncMock(return_value=True)):
        await notifier.notify_estate(
            _make_estate(), _make_breakdown(total=80, price_drop_bonus=30),
            search_name="test", min_score=60
        )

    # log_notification called with "price_drop"
    store.log_notification.assert_awaited_once_with(_make_estate().hash_id, "price_drop")
