"""
Agent Life Space — Real Estate Watcher Store

SQLite-backed async store for search configs, price history,
and notification log.  Uses WAL mode for safe concurrent access.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import structlog

from agent.realestate.models import NotifLogEntry, PriceRecord, SearchConfig

logger = structlog.get_logger(__name__)

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS searches (
    name        TEXT    PRIMARY KEY,
    params_json TEXT    NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    min_score   INTEGER NOT NULL DEFAULT 60,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash_id     INTEGER NOT NULL,
    search_name TEXT    NOT NULL,
    snapshot_at TEXT    NOT NULL,
    price       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notif_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash_id     INTEGER NOT NULL,
    event_type  TEXT    NOT NULL,
    sent_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_history_hash ON price_history (hash_id, search_name, snapshot_at);
CREATE INDEX IF NOT EXISTS idx_notif_log_hash     ON notif_log (hash_id, event_type, sent_at);
"""


class RealEstateStore:
    """Async SQLite store for the real estate watcher."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def ensure_tables(self) -> None:
        """Create all tables and indexes if they do not exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_DDL)
            await db.commit()
        logger.debug("realestate.store.ready", db=str(self._db_path))

    # ── Search CRUD ────────────────────────────────────────────────────────

    async def add_search(self, config: SearchConfig) -> None:
        """Insert a new search config (raises if name already exists)."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO searches (name, params_json, active, min_score, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    config.name,
                    json.dumps(config.params_json),
                    int(config.active),
                    config.min_score,
                    config.created_at.isoformat(),
                ),
            )
            await db.commit()
        logger.info("realestate.store.search_added", name=config.name)

    async def get_search(self, name: str) -> SearchConfig | None:
        """Return a single search by name, or None."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT name, params_json, active, min_score, created_at FROM searches WHERE name = ?",
                (name,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return SearchConfig.from_row(row)

    async def list_active(self) -> list[SearchConfig]:
        """Return all active searches."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT name, params_json, active, min_score, created_at FROM searches WHERE active = 1 ORDER BY created_at"
            ) as cursor:
                rows = await cursor.fetchall()
        return [SearchConfig.from_row(r) for r in rows]

    async def list_all(self) -> list[SearchConfig]:
        """Return all searches (active and paused)."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT name, params_json, active, min_score, created_at FROM searches ORDER BY created_at"
            ) as cursor:
                rows = await cursor.fetchall()
        return [SearchConfig.from_row(r) for r in rows]

    async def remove_search(self, name: str) -> bool:
        """Delete a search. Returns True if it existed."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM searches WHERE name = ?", (name,))
            await db.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            logger.info("realestate.store.search_removed", name=name)
        return deleted

    async def pause_search(self, name: str) -> bool:
        """Set active=0. Returns True if search existed."""
        return await self._set_active(name, active=False)

    async def resume_search(self, name: str) -> bool:
        """Set active=1. Returns True if search existed."""
        return await self._set_active(name, active=True)

    async def _set_active(self, name: str, *, active: bool) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE searches SET active = ? WHERE name = ?",
                (int(active), name),
            )
            await db.commit()
            updated = cursor.rowcount > 0
        if updated:
            state = "resumed" if active else "paused"
            logger.info(f"realestate.store.search_{state}", name=name)
        return updated

    # ── Price history ──────────────────────────────────────────────────────

    async def upsert_price(
        self,
        hash_id: int,
        search_name: str,
        price: int,
    ) -> float | None:
        """
        Record the current price snapshot.

        Returns the percentage change vs. the previous snapshot
        (negative = price drop), or None if this is the first record.
        """
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            # Fetch the most recent previous price for this listing
            async with db.execute(
                """
                SELECT price FROM price_history
                WHERE hash_id = ? AND search_name = ?
                ORDER BY snapshot_at DESC
                LIMIT 1
                """,
                (hash_id, search_name),
            ) as cursor:
                prev_row = await cursor.fetchone()

            await db.execute(
                "INSERT INTO price_history (hash_id, search_name, snapshot_at, price) VALUES (?, ?, ?, ?)",
                (hash_id, search_name, now, price),
            )
            await db.commit()

        if prev_row is None:
            return None

        prev_price: int = prev_row[0]
        if prev_price == 0:
            return None

        pct_change = (price - prev_price) / prev_price * 100.0
        if pct_change != 0.0:
            logger.debug(
                "realestate.store.price_change",
                hash_id=hash_id,
                prev=prev_price,
                current=price,
                pct=round(pct_change, 2),
            )
        return pct_change

    async def get_price_history(
        self,
        hash_id: int,
        search_name: str,
        limit: int = 50,
    ) -> list[PriceRecord]:
        """Return the most recent price records for a listing."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                """
                SELECT hash_id, search_name, snapshot_at, price
                FROM price_history
                WHERE hash_id = ? AND search_name = ?
                ORDER BY snapshot_at DESC
                LIMIT ?
                """,
                (hash_id, search_name, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [PriceRecord.from_row(r) for r in rows]

    # ── Notification log ───────────────────────────────────────────────────

    async def check_notified(
        self,
        hash_id: int,
        event_type: str,
        window_hours: int = 24,
    ) -> bool:
        """
        Return True if a notification of this type was already sent
        within the deduplication window.
        """
        cutoff = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                """
                SELECT 1 FROM notif_log
                WHERE hash_id = ? AND event_type = ? AND sent_at >= ?
                LIMIT 1
                """,
                (hash_id, event_type, cutoff),
            ) as cursor:
                row = await cursor.fetchone()
        return row is not None

    async def log_notification(
        self,
        hash_id: int,
        event_type: str,
    ) -> None:
        """Record that a notification was sent right now."""
        sent_at = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO notif_log (hash_id, event_type, sent_at) VALUES (?, ?, ?)",
                (hash_id, event_type, sent_at),
            )
            await db.commit()
        logger.debug("realestate.store.notif_logged", hash_id=hash_id, event_type=event_type)

    async def get_notif_log(
        self,
        hash_id: int,
        event_type: str | None = None,
    ) -> list[NotifLogEntry]:
        """Return notification log entries for a hash_id."""
        async with aiosqlite.connect(self._db_path) as db:
            if event_type is not None:
                async with db.execute(
                    "SELECT hash_id, event_type, sent_at FROM notif_log WHERE hash_id = ? AND event_type = ? ORDER BY sent_at DESC",
                    (hash_id, event_type),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with db.execute(
                    "SELECT hash_id, event_type, sent_at FROM notif_log WHERE hash_id = ? ORDER BY sent_at DESC",
                    (hash_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
        return [NotifLogEntry.from_row(r) for r in rows]

    # ── Reporting helpers ──────────────────────────────────────────────────

    async def count_price_drops(
        self,
        search_name: str,
        since: datetime,
        drop_threshold_pct: float = 3.0,
    ) -> int:
        """
        Count distinct estates with a price drop >= threshold since a given time.
        Used for daily report aggregation.
        """
        since_str = since.isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                """
                WITH ranked AS (
                    SELECT
                        hash_id,
                        price,
                        snapshot_at,
                        ROW_NUMBER() OVER (PARTITION BY hash_id ORDER BY snapshot_at DESC) AS rn
                    FROM price_history
                    WHERE search_name = ?
                ),
                current_prices AS (SELECT hash_id, price FROM ranked WHERE rn = 1),
                prev_prices AS (
                    SELECT hash_id, price FROM price_history
                    WHERE search_name = ? AND snapshot_at < ?
                    GROUP BY hash_id
                    HAVING MAX(snapshot_at) = MAX(snapshot_at)
                )
                SELECT COUNT(*)
                FROM current_prices c
                JOIN prev_prices p ON c.hash_id = p.hash_id
                WHERE CAST(c.price AS REAL) / p.price - 1.0 <= -?
                """,
                (search_name, search_name, since_str, drop_threshold_pct / 100.0),
            ) as cursor:
                row = await cursor.fetchone()
        return row[0] if row else 0
