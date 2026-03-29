"""
Agent Life Space — Persistent Conversation Memory

Rieši: "John začína odznova pri každom CLI calle"

Vzor z MemGPT: virtual memory s paging.
    - Core memory: kľúčové fakty (vždy v prompte)
    - Rolling summary: komprimovaná história
    - Recent messages: posledných N surových správ
    - Retrieval: relevantné staré výmeny (FTS search)

Každý exchange sa uloží do SQLite okamžite.
Pri reštarte sa kontext rekonštruuje z DB.
"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite
import structlog

from agent.core.identity import get_agent_identity

logger = structlog.get_logger(__name__)

_PROMPT_EXCLUDED_CORE_KEYS = {
    "owner",
    "owner_name",
    "owner_full_name",
    "language",
    "preferred_language",
    "default_language",
}


class PersistentConversation:
    """
    Hybridná pamäť: surové správy + rolling summary + core facts.
    Navrhnuté pre CLI-per-call pattern.
    """

    def __init__(
        self,
        db_path: str = "agent/memory/conversations.db",
        max_raw_messages: int = 10,
        summary_threshold: int = 20,
    ) -> None:
        self._db_path = db_path
        self._max_raw = max_raw_messages
        self._summary_threshold = summary_threshold
        self._db: aiosqlite.Connection | None = None
        self._fts_available = False

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                started_at REAL,
                summary TEXT DEFAULT '',
                summary_updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL,
                summarized INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS core_memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL
            );
        """)
        # FTS5 virtual table for fast full-text search on messages
        try:
            await self._db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(content, sender, conversation_id, content='messages', content_rowid='id')
            """)
            # Triggers to keep FTS index in sync
            await self._db.executescript("""
                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content, sender, conversation_id)
                    VALUES (new.id, new.content, new.sender, new.conversation_id);
                END;
                CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content, sender, conversation_id)
                    VALUES ('delete', old.id, old.content, old.sender, old.conversation_id);
                END;
            """)
            self._fts_available = True
        except Exception:
            self._fts_available = False
        await self._db.commit()

        # Štatistiky
        async with self._db.execute("SELECT COUNT(*) FROM messages") as cur:
            row = await cur.fetchone()
            total_msgs = row[0] if row else 0
        async with self._db.execute("SELECT COUNT(*) FROM core_memory") as cur:
            row = await cur.fetchone()
            total_facts = row[0] if row else 0

        logger.info("persistent_conversation_initialized",
                     messages=total_msgs, core_facts=total_facts)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # === Store ===

    async def save_exchange(
        self,
        conversation_id: str,
        user_msg: str,
        assistant_msg: str,
        sender: str = "user",
    ) -> None:
        """Ulož user+assistant pár okamžite po každom CLI calle."""
        assert self._db
        now = time.time()

        # Ensure conversation exists
        await self._db.execute(
            "INSERT OR IGNORE INTO conversations (id, started_at) VALUES (?, ?)",
            (conversation_id, now),
        )

        identity = get_agent_identity()
        for role, content, who in [
            ("user", user_msg, sender or "user"),
            ("assistant", assistant_msg, identity.agent_name),
        ]:
            await self._db.execute(
                "INSERT INTO messages (conversation_id, sender, role, content, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, who, role, content, now),
            )
        await self._db.commit()

    async def update_core_fact(self, key: str, value: str) -> None:
        """Upsert kľúčový fakt (napr. 'current_project', 'user_mood')."""
        assert self._db
        await self._db.execute(
            "INSERT OR REPLACE INTO core_memory (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )
        await self._db.commit()

    # === Retrieve ===

    async def build_context(
        self,
        conversation_id: str,
        query: str = "",
    ) -> str:
        """
        Postav memory blok pre injekciu do promptu.

        Vracia formátovaný string pripravený na vloženie do CLI promptu.
        ~2000-3000 tokenov celkom.
        """
        parts = []

        # 1. Core memory — vždy
        core = await self._get_core_memory()
        if core:
            parts.append(f"Čo viem (core memory):\n{core}")

        # 2. Rolling summary — komprimovaná história
        summary = await self._get_summary(conversation_id)
        if summary:
            parts.append(f"Predchádzajúce konverzácie (zhrnutie):\n{summary}")

        # 3. Posledné surové správy — aktuálna session
        recent = await self._get_recent_messages(conversation_id)
        if recent:
            formatted = "\n".join(f"{sender}: {content[:200]}" for sender, content in recent)
            parts.append(f"Posledné správy:\n{formatted}")

        # 4. Relevantné staré výmeny (ak je query)
        if query and len(query) > 10:
            relevant = await self._search_past(query, exclude=conversation_id)
            if relevant:
                formatted = "\n".join(f"[{sender}]: {content[:150]}" for sender, content in relevant)
                parts.append(f"Relevantné z minulosti:\n{formatted}")

        return "\n\n".join(parts) if parts else ""

    async def _get_core_memory(self) -> str:
        assert self._db
        async with self._db.execute(
            "SELECT key, value FROM core_memory ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        filtered = [
            (k, v) for k, v in rows
            if k.lower() not in _PROMPT_EXCLUDED_CORE_KEYS
        ]
        return "\n".join(f"- {k}: {v}" for k, v in filtered) if filtered else ""

    async def _get_summary(self, conversation_id: str) -> str:
        assert self._db
        async with self._db.execute(
            "SELECT summary FROM conversations WHERE id = ?",
            (conversation_id,),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row and row[0] else ""

    async def _get_recent_messages(self, conversation_id: str) -> list[tuple[str, str]]:
        assert self._db
        async with self._db.execute(
            "SELECT sender, content FROM messages "
            "WHERE conversation_id = ? AND summarized = 0 "
            "ORDER BY timestamp DESC LIMIT ?",
            (conversation_id, self._max_raw),
        ) as cur:
            rows = await cur.fetchall()
        return list(reversed(rows))

    async def _search_past(
        self,
        query: str,
        exclude: str = "",
        limit: int = 3,
    ) -> list[tuple[str, str]]:
        """Search past messages. Uses FTS5 if available, falls back to LIKE."""
        assert self._db
        words = [w for w in query.split() if len(w) > 3][:3]
        if not words:
            return []

        # FTS5 path — ranked full-text search
        if self._fts_available:
            fts_query = " OR ".join(f'"{w}"' for w in words)
            sql = (
                "SELECT m.sender, m.content FROM messages m "
                "JOIN messages_fts f ON m.id = f.rowid "
                "WHERE messages_fts MATCH ?"
            )
            params: list[Any] = [fts_query]
            if exclude:
                sql += " AND m.conversation_id != ?"
                params.append(exclude)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
            try:
                async with self._db.execute(sql, params) as cur:
                    return await cur.fetchall()
            except Exception:
                pass  # Fall through to LIKE

        # LIKE fallback
        conditions = " AND ".join("content LIKE ?" for _ in words)
        params = [f"%{w}%" for w in words]

        sql = f"SELECT sender, content FROM messages WHERE {conditions}"
        if exclude:
            sql += " AND conversation_id != ?"
            params.append(exclude)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        async with self._db.execute(sql, params) as cur:
            return await cur.fetchall()

    # === Summarization ===

    async def needs_summary(self, conversation_id: str) -> bool:
        """Má táto konverzácia dosť nesumárnych správ na kompresiu?"""
        assert self._db
        async with self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND summarized = 0",
            (conversation_id,),
        ) as cur:
            row = await cur.fetchone()
        return (row[0] if row else 0) >= self._summary_threshold

    async def get_unsummarized(self, conversation_id: str) -> str:
        """Vráť text nesumárnych správ pre kompresiu."""
        assert self._db
        async with self._db.execute(
            "SELECT sender, content FROM messages "
            "WHERE conversation_id = ? AND summarized = 0 "
            "ORDER BY timestamp ASC",
            (conversation_id,),
        ) as cur:
            rows = await cur.fetchall()
        return "\n".join(f"{sender}: {content}" for sender, content in rows)

    async def store_summary(self, conversation_id: str, summary: str) -> None:
        """Ulož summary a označ správy ako summarized."""
        assert self._db
        # Pridaj k existujúcemu summary
        existing = await self._get_summary(conversation_id)
        combined = (existing + "\n\n" + summary).strip() if existing else summary

        # Limituj na ~1000 slov
        words = combined.split()
        if len(words) > 1000:
            combined = " ".join(words[-1000:])

        await self._db.execute(
            "UPDATE conversations SET summary = ?, summary_updated_at = ? WHERE id = ?",
            (combined, time.time(), conversation_id),
        )
        await self._db.execute(
            "UPDATE messages SET summarized = 1 "
            "WHERE conversation_id = ? AND summarized = 0",
            (conversation_id,),
        )
        await self._db.commit()
        logger.info("conversation_summarized", conversation=conversation_id)

    # === Stats ===

    async def get_stats(self) -> dict[str, Any]:
        assert self._db
        async with self._db.execute("SELECT COUNT(*) FROM messages") as cur:
            total_msgs = (await cur.fetchone())[0]
        async with self._db.execute("SELECT COUNT(*) FROM core_memory") as cur:
            total_facts = (await cur.fetchone())[0]
        async with self._db.execute("SELECT COUNT(*) FROM conversations") as cur:
            total_convs = (await cur.fetchone())[0]
        return {
            "total_messages": total_msgs,
            "core_facts": total_facts,
            "conversations": total_convs,
        }
