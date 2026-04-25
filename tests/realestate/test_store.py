"""Tests for RealEstateStore — SQLite CRUD, price history, notif dedup."""
from __future__ import annotations

import aiosqlite
import pytest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent.realestate.models import SearchConfig
from agent.realestate.store import RealEstateStore


@pytest.fixture
async def store(tmp_path: Path) -> RealEstateStore:
    s = RealEstateStore(tmp_path / "test.db")
    await s.ensure_tables()
    return s


def _cfg(name: str = "test_search", active: bool = True) -> SearchConfig:
    return SearchConfig(name=name, params_json={"category_sub_cb": 4}, active=active, min_score=60)


# ── Search CRUD ────────────────────────────────────────────────────────────────


async def test_search_crud(store: RealEstateStore) -> None:
    cfg = _cfg()
    await store.add_search(cfg)

    # get
    fetched = await store.get_search("test_search")
    assert fetched is not None
    assert fetched.name == "test_search"
    assert fetched.active is True
    assert fetched.min_score == 60

    # list_active
    active = await store.list_active()
    assert len(active) == 1

    # pause
    ok = await store.pause_search("test_search")
    assert ok is True
    assert len(await store.list_active()) == 0

    # resume
    ok = await store.resume_search("test_search")
    assert ok is True
    assert len(await store.list_active()) == 1

    # list_all includes paused
    await store.pause_search("test_search")
    all_searches = await store.list_all()
    assert len(all_searches) == 1

    # remove
    removed = await store.remove_search("test_search")
    assert removed is True
    assert await store.get_search("test_search") is None

    # remove non-existent
    assert await store.remove_search("nonexistent") is False


async def test_pause_resume_nonexistent(store: RealEstateStore) -> None:
    assert await store.pause_search("ghost") is False
    assert await store.resume_search("ghost") is False


# ── Price history ──────────────────────────────────────────────────────────────


async def test_price_upsert_and_change(store: RealEstateStore) -> None:
    hash_id = 12345678
    search_name = "test_search"

    # First upsert → no previous price → None
    result1 = await store.upsert_price(hash_id, search_name, 5_000_000)
    assert result1 is None

    # Second upsert same price → 0% change
    result2 = await store.upsert_price(hash_id, search_name, 5_000_000)
    assert result2 == pytest.approx(0.0)

    # Third upsert with 5% drop
    result3 = await store.upsert_price(hash_id, search_name, 4_750_000)
    assert result3 is not None
    assert result3 == pytest.approx(-5.0, abs=0.01)

    # Fourth upsert with increase
    result4 = await store.upsert_price(hash_id, search_name, 5_000_000)
    assert result4 is not None
    assert result4 > 0  # price went up vs previous


async def test_price_history_retrieval(store: RealEstateStore) -> None:
    await store.upsert_price(99, "search_a", 4_000_000)
    await store.upsert_price(99, "search_a", 3_800_000)
    history = await store.get_price_history(99, "search_a")
    assert len(history) == 2
    # Most recent first
    assert history[0].price == 3_800_000


# ── Notification deduplication ─────────────────────────────────────────────────


async def test_notif_dedup(store: RealEstateStore) -> None:
    hash_id = 99999
    event_type = "new_listing"

    # Not yet notified → False
    assert not await store.check_notified(hash_id, event_type)

    # Log it
    await store.log_notification(hash_id, event_type)

    # Within 24h window → True
    assert await store.check_notified(hash_id, event_type)

    # Different event_type → False
    assert not await store.check_notified(hash_id, "price_drop")


async def test_notif_dedup_expired(store: RealEstateStore) -> None:
    """A notification older than 24h should NOT block a new one."""
    hash_id = 88888
    event_type = "new_listing"
    old_time = (datetime.now(UTC) - timedelta(hours=25)).isoformat()

    # Insert directly with old timestamp
    async with aiosqlite.connect(store._db_path) as db:
        await db.execute(
            "INSERT INTO notif_log (hash_id, event_type, sent_at) VALUES (?, ?, ?)",
            (hash_id, event_type, old_time),
        )
        await db.commit()

    # Old record is outside 24h window → check_notified should be False
    assert not await store.check_notified(hash_id, event_type)
