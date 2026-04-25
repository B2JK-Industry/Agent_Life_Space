"""Persistent notification deduplication for cron + initiative loops.

Cron loops (marketplace, watchdog, scrapers) opakovane volajú
`bot.send_message` pre rovnaké udalosti — výsledok je Telegram spam.
Tento modul drží `(dedup_key, payload_hash)` v SQLite s TTL a wrappuje
posielanie tak, aby duplicate posiely za TTL-window boli odhadnuté.

Public API:
    dedup = NotificationDedup(db_path)
    await dedup.initialize()

    # Wrapper: vracia True ak poslané, False ak dedup
    sent = await dedup.send_once(
        bot=bot,
        chat_id=chat_id,
        text=msg,
        dedup_key=f"marketplace_subcontract:{job_id}",
        ttl_hours=24,
    )

Schéma:
    CREATE TABLE notification_log (
        dedup_key   TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        sent_at     TEXT NOT NULL,
        chat_id     INTEGER NOT NULL,
        PRIMARY KEY (dedup_key, payload_hash)
    );
    CREATE INDEX idx_sent_at ON notification_log(sent_at);

Cleanup: stále tabuľka; periodic prune-old() volá sa z cron retention loopu.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger(__name__)


def _payload_hash(text: str) -> str:
    """Krátky hash prvých 200 znakov správy pre identifikáciu obsahu."""
    return hashlib.sha256(text[:200].encode("utf-8")).hexdigest()[:16]


class NotificationDedup:
    """Persistent notification deduplication with TTL window."""

    def __init__(self, db_path: str) -> None:
        if not db_path:
            msg = "db_path cannot be empty"
            raise ValueError(msg)
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS notification_log (
                dedup_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                PRIMARY KEY (dedup_key, payload_hash)
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sent_at ON notification_log(sent_at)"
        )
        await self._db.commit()
        cnt = await self._count()
        logger.info("notification_dedup_initialized", count=cnt, db=self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _count(self) -> int:
        assert self._db
        async with self._db.execute(
            "SELECT COUNT(*) FROM notification_log"
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def is_recent(
        self, dedup_key: str, payload_hash: str, ttl_hours: int = 24
    ) -> bool:
        """True if this (key, hash) was sent within ttl_hours."""
        assert self._db
        cutoff = (datetime.now(UTC) - timedelta(hours=ttl_hours)).isoformat()
        async with self._db.execute(
            "SELECT 1 FROM notification_log "
            "WHERE dedup_key = ? AND payload_hash = ? AND sent_at >= ? LIMIT 1",
            (dedup_key, payload_hash, cutoff),
        ) as cur:
            return (await cur.fetchone()) is not None

    async def record(
        self, dedup_key: str, payload_hash: str, chat_id: int
    ) -> None:
        """Persist a sent notification."""
        assert self._db
        await self._db.execute(
            "INSERT OR REPLACE INTO notification_log "
            "(dedup_key, payload_hash, sent_at, chat_id) VALUES (?, ?, ?, ?)",
            (dedup_key, payload_hash, datetime.now(UTC).isoformat(), chat_id),
        )
        await self._db.commit()

    async def send_once(
        self,
        *,
        bot: Any,
        chat_id: int,
        text: str,
        dedup_key: str,
        ttl_hours: int = 24,
        parse_mode: str | None = "Markdown",
    ) -> bool:
        """Send Telegram message only if not deduped within TTL.

        Returns True if sent, False if deduped or bot/chat_id missing.
        """
        if not bot or not chat_id:
            return False
        if not dedup_key:
            msg = "dedup_key required (empty would dedup all messages)"
            raise ValueError(msg)

        ph = _payload_hash(text)
        if await self.is_recent(dedup_key, ph, ttl_hours=ttl_hours):
            logger.info(
                "notification_deduped",
                key=dedup_key,
                payload_hash=ph,
                ttl_hours=ttl_hours,
            )
            return False

        try:
            kwargs: dict[str, Any] = {}
            if parse_mode:
                kwargs["parse_mode"] = parse_mode
            await bot.send_message(chat_id, text, **kwargs)
        except TypeError:
            # Bot signature variant — try without kwargs
            await bot.send_message(chat_id, text)
        except Exception:  # noqa: BLE001
            logger.exception("notification_send_failed", key=dedup_key)
            return False

        await self.record(dedup_key, ph, chat_id)
        return True

    async def prune_older_than(self, hours: int = 168) -> int:
        """Delete entries older than `hours` (default 7 days). Returns deleted count."""
        assert self._db
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        async with self._db.execute(
            "DELETE FROM notification_log WHERE sent_at < ?", (cutoff,)
        ) as cur:
            deleted = cur.rowcount
        await self._db.commit()
        if deleted:
            logger.info("notification_dedup_pruned", deleted=deleted, hours=hours)
        return deleted
