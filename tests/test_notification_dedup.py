"""Tests for NotificationDedup helper used by AgentCron."""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

from agent.core.notification_dedup import NotificationDedup, _payload_hash


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> None:
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_first_send_passes():
    with tempfile.TemporaryDirectory() as tmp:
        d = NotificationDedup(db_path=os.path.join(tmp, "notif.db"))
        await d.initialize()
        bot = FakeBot()
        sent = await d.send_once(
            bot=bot, chat_id=1, text="hello", dedup_key="k:1", ttl_hours=24
        )
        assert sent is True
        assert len(bot.sent) == 1
        await d.close()


@pytest.mark.asyncio
async def test_duplicate_within_ttl_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        d = NotificationDedup(db_path=os.path.join(tmp, "notif.db"))
        await d.initialize()
        bot = FakeBot()
        await d.send_once(
            bot=bot, chat_id=1, text="hello", dedup_key="k:1", ttl_hours=24
        )
        sent2 = await d.send_once(
            bot=bot, chat_id=1, text="hello", dedup_key="k:1", ttl_hours=24
        )
        assert sent2 is False
        assert len(bot.sent) == 1
        await d.close()


@pytest.mark.asyncio
async def test_different_payload_passes():
    """Same dedup_key but different payload hash → both pass."""
    with tempfile.TemporaryDirectory() as tmp:
        d = NotificationDedup(db_path=os.path.join(tmp, "notif.db"))
        await d.initialize()
        bot = FakeBot()
        await d.send_once(
            bot=bot, chat_id=1, text="msg one", dedup_key="k:1", ttl_hours=24
        )
        await d.send_once(
            bot=bot, chat_id=1, text="msg two — different content", dedup_key="k:1", ttl_hours=24
        )
        assert len(bot.sent) == 2
        await d.close()


@pytest.mark.asyncio
async def test_different_key_passes():
    """Same payload but different dedup_key → both pass."""
    with tempfile.TemporaryDirectory() as tmp:
        d = NotificationDedup(db_path=os.path.join(tmp, "notif.db"))
        await d.initialize()
        bot = FakeBot()
        await d.send_once(
            bot=bot, chat_id=1, text="hello", dedup_key="k:1", ttl_hours=24
        )
        await d.send_once(
            bot=bot, chat_id=1, text="hello", dedup_key="k:2", ttl_hours=24
        )
        assert len(bot.sent) == 2
        await d.close()


@pytest.mark.asyncio
async def test_no_bot_returns_false():
    with tempfile.TemporaryDirectory() as tmp:
        d = NotificationDedup(db_path=os.path.join(tmp, "notif.db"))
        await d.initialize()
        sent = await d.send_once(
            bot=None, chat_id=1, text="x", dedup_key="k", ttl_hours=24
        )
        assert sent is False
        await d.close()


@pytest.mark.asyncio
async def test_empty_dedup_key_raises():
    with tempfile.TemporaryDirectory() as tmp:
        d = NotificationDedup(db_path=os.path.join(tmp, "notif.db"))
        await d.initialize()
        bot = FakeBot()
        with pytest.raises(ValueError):
            await d.send_once(
                bot=bot, chat_id=1, text="x", dedup_key="", ttl_hours=24
            )
        await d.close()


@pytest.mark.asyncio
async def test_persistence_across_restart():
    """Dedup persists in SQLite — second connection should see prior records."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "notif.db")

        d1 = NotificationDedup(db_path=path)
        await d1.initialize()
        bot1 = FakeBot()
        await d1.send_once(
            bot=bot1, chat_id=1, text="hello", dedup_key="k", ttl_hours=24
        )
        assert len(bot1.sent) == 1
        await d1.close()

        # New instance — same DB
        d2 = NotificationDedup(db_path=path)
        await d2.initialize()
        bot2 = FakeBot()
        sent = await d2.send_once(
            bot=bot2, chat_id=1, text="hello", dedup_key="k", ttl_hours=24
        )
        assert sent is False  # deduped from disk
        assert len(bot2.sent) == 0
        await d2.close()


@pytest.mark.asyncio
async def test_prune_old_entries():
    """prune_older_than removes entries older than threshold."""
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "notif.db")
        d = NotificationDedup(db_path=path)
        await d.initialize()
        bot = FakeBot()
        await d.send_once(
            bot=bot, chat_id=1, text="x", dedup_key="k1", ttl_hours=24
        )
        await d.close()

        # Manually rewrite sent_at to be old
        c = sqlite3.connect(path)
        c.execute(
            "UPDATE notification_log SET sent_at = '2020-01-01T00:00:00+00:00'"
        )
        c.commit()
        c.close()

        d2 = NotificationDedup(db_path=path)
        await d2.initialize()
        deleted = await d2.prune_older_than(hours=24)
        assert deleted == 1
        await d2.close()


def test_payload_hash_stable():
    h1 = _payload_hash("hello world")
    h2 = _payload_hash("hello world")
    h3 = _payload_hash("different")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 16


def test_payload_hash_truncates():
    """Long messages truncated to 200 chars before hash."""
    long1 = "a" * 500
    long2 = "a" * 200 + "different suffix"
    # Both start with 200 'a's so should hash same
    assert _payload_hash(long1) == _payload_hash(long2)
