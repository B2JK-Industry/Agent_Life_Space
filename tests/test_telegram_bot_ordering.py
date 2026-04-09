"""
Regression tests for the per-chat reply-ordering guarantee in
``agent.social.telegram_bot.TelegramBot``.

Two messages in the *same* chat must serialize so the assistant
replies arrive in the same order the operator sent them. Two
messages in *different* chats must stay concurrent — the lock is
keyed on ``chat_id``, never global.

We avoid the real Telegram HTTP API by stubbing ``send_message`` and
the message callback. The tests assert ordering by inspecting the
recorded send_message arguments.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.social.telegram_bot import TelegramBot

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _make_bot() -> TelegramBot:
    bot = TelegramBot(
        token="test:token",
        allowed_user_ids=[42],
        owner_name="owner",
        state_dir="/tmp/test_telegram_bot_ordering",
    )
    return bot


def _make_message(text: str, chat_id: int, user_id: int = 42) -> dict[str, Any]:
    return {
        "message_id": 1,
        "from": {"id": user_id, "username": "owner", "first_name": "owner"},
        "chat": {"id": chat_id, "type": "private"},
        "text": text,
        "date": 0,
    }


# ─────────────────────────────────────────────
# 1. Lock factory
# ─────────────────────────────────────────────


class TestChatLockFactory:
    """Each chat_id must get its own asyncio.Lock; same chat_id must
    yield the same lock instance."""

    def test_two_chats_get_distinct_locks(self):
        bot = _make_bot()
        l1 = bot._get_chat_lock(100)
        l2 = bot._get_chat_lock(200)
        assert l1 is not l2

    def test_same_chat_returns_same_lock(self):
        bot = _make_bot()
        l1 = bot._get_chat_lock(100)
        l2 = bot._get_chat_lock(100)
        assert l1 is l2


# ─────────────────────────────────────────────
# 2. Same-chat serialization
# ─────────────────────────────────────────────


class TestSameChatSerialization:
    """Two messages in the same chat must serialize: the second
    callback must NOT start before the first reply has landed."""

    @pytest.mark.asyncio
    async def test_same_chat_replies_arrive_in_order(self):
        bot = _make_bot()
        # Replace send_message with a recording stub.
        sends: list[tuple[int, str]] = []

        async def fake_send(chat_id: int, text: str) -> None:
            sends.append((chat_id, text))

        bot.send_message = fake_send  # type: ignore[method-assign]

        # Callback: msg #1 takes 80 ms, msg #2 takes 10 ms. Without the
        # per-chat lock, msg #2 would finish first and reply first.
        async def callback(text: str, *args: Any, **kwargs: Any) -> str:
            if "first" in text:
                await asyncio.sleep(0.08)
                return "REPLY-1"
            await asyncio.sleep(0.01)
            return "REPLY-2"

        bot._message_callback = callback

        msg1 = _make_message("first message", chat_id=100)
        msg2 = _make_message("second message", chat_id=100)

        # Launch both concurrently to mimic the real polling loop.
        await asyncio.gather(
            bot._handle_message(msg1),
            bot._handle_message(msg2),
        )

        assert len(sends) == 2
        # Both replies must land for chat 100 in send-order.
        assert sends[0] == (100, "REPLY-1")
        assert sends[1] == (100, "REPLY-2")

    @pytest.mark.asyncio
    async def test_callback_does_not_overlap_in_same_chat(self):
        """Stronger property: while msg1's callback is running, msg2's
        callback must not start (the lock holds it)."""
        bot = _make_bot()
        bot.send_message = AsyncMock()  # type: ignore[method-assign]

        in_flight = 0
        observed_max = 0

        async def callback(text: str, *args: Any, **kwargs: Any) -> str:
            nonlocal in_flight, observed_max
            in_flight += 1
            observed_max = max(observed_max, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return "ok"

        bot._message_callback = callback

        await asyncio.gather(
            bot._handle_message(_make_message("a", chat_id=300)),
            bot._handle_message(_make_message("b", chat_id=300)),
            bot._handle_message(_make_message("c", chat_id=300)),
        )

        # Same chat → never more than one callback running at a time.
        assert observed_max == 1


# ─────────────────────────────────────────────
# 3. Cross-chat concurrency
# ─────────────────────────────────────────────


class TestCrossChatConcurrency:
    """Two messages in different chats must stay concurrent — the
    lock is keyed per chat_id."""

    @pytest.mark.asyncio
    async def test_different_chats_run_in_parallel(self):
        bot = _make_bot()
        bot.send_message = AsyncMock()  # type: ignore[method-assign]

        in_flight = 0
        observed_max = 0

        async def callback(text: str, *args: Any, **kwargs: Any) -> str:
            nonlocal in_flight, observed_max
            in_flight += 1
            observed_max = max(observed_max, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return "ok"

        bot._message_callback = callback

        await asyncio.gather(
            bot._handle_message(_make_message("a", chat_id=400)),
            bot._handle_message(_make_message("b", chat_id=500)),
            bot._handle_message(_make_message("c", chat_id=600)),
        )

        # Different chats → all three should overlap (parallelism = 3).
        assert observed_max == 3

    @pytest.mark.asyncio
    async def test_one_slow_chat_does_not_block_other(self):
        """A long-running callback in chat A must not block chat B's
        reply."""
        bot = _make_bot()
        send_order: list[int] = []

        async def fake_send(chat_id: int, text: str) -> None:
            send_order.append(chat_id)

        bot.send_message = fake_send  # type: ignore[method-assign]

        async def callback(text: str, *args: Any, **kwargs: Any) -> str:
            if "slow" in text:
                await asyncio.sleep(0.10)
                return "slow-done"
            await asyncio.sleep(0.01)
            return "fast-done"

        bot._message_callback = callback

        # Launch slow first, then fast on a DIFFERENT chat.
        await asyncio.gather(
            bot._handle_message(_make_message("slow message", chat_id=700)),
            bot._handle_message(_make_message("fast message", chat_id=800)),
        )

        # Fast chat must reply first because the slow chat is on its
        # own lock.
        assert send_order[0] == 800
        assert send_order[1] == 700
