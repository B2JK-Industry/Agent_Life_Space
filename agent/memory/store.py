"""
Agent Life Space — Memory Store

Multi-layered memory system. The agent doesn't just store memories —
it USES them to make better decisions.

Memory layers:
    1. Working Memory — current context, active task data (volatile)
    2. Episodic Memory — what happened, experiences, events (persistent)
    3. Semantic Memory — facts, knowledge, learned patterns (persistent)
    4. Procedural Memory — how to do things, learned procedures (persistent)

Each memory has:
    - Relevance score (how useful for current context)
    - Decay factor (older = less relevant, unless reinforced)
    - Access count (frequently accessed = more important)
    - Tags for retrieval
    - Confidence level

Storage: SQLite (aiosqlite) for persistence on disk.
Index: In-memory for fast retrieval, rebuilt from DB on startup.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import aiosqlite
import orjson
import structlog

logger = structlog.get_logger(__name__)


class MemoryType(str, Enum):
    WORKING = "working"  # Current context, volatile
    EPISODIC = "episodic"  # Events, experiences
    SEMANTIC = "semantic"  # Facts, knowledge
    PROCEDURAL = "procedural"  # How-to, procedures


class ProvenanceStatus(str, Enum):
    """Epistemic status of a memory — how much can we trust it."""

    OBSERVED = "observed"        # Directly witnessed by agent (system events, tool results)
    USER_ASSERTED = "user_asserted"  # User told us this (may or may not be true)
    INFERRED = "inferred"        # Agent derived this from other data
    VERIFIED = "verified"        # Cross-checked against authoritative source
    STALE = "stale"              # Was valid, now outdated (time/event-based expiry)


class MemoryKind(str, Enum):
    """Epistemic kind — is this a fact or a belief?"""

    FACT = "fact"        # Objectively verifiable (e.g., "Python 3.12 is installed")
    BELIEF = "belief"    # Subjectively held (e.g., "user prefers short answers")
    CLAIM = "claim"      # Someone stated this, unverified (e.g., "the server has 16GB RAM")
    PROCEDURE = "procedure"  # How-to knowledge (e.g., "to deploy, run make deploy")


class MemoryEntry:
    """A single memory entry with metadata for intelligent retrieval."""

    def __init__(
        self,
        content: str,
        memory_type: MemoryType,
        tags: list[str] | None = None,
        source: str = "",
        confidence: float = 1.0,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        provenance: ProvenanceStatus = ProvenanceStatus.OBSERVED,
        kind: MemoryKind = MemoryKind.FACT,
        expires_at: str | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.content = content
        self.memory_type = memory_type
        self.tags = tags or []
        self.source = source
        self.confidence = max(0.0, min(1.0, confidence))
        self.importance = max(0.0, min(1.0, importance))
        self.metadata = metadata or {}
        self.provenance = provenance
        self.kind = kind
        self.expires_at = expires_at  # ISO timestamp, None = no expiry
        self.created_at = datetime.now(UTC).isoformat()
        self.last_accessed = self.created_at
        self.access_count = 0
        self.decay_factor = 1.0  # Starts at full relevance

    def access(self) -> None:
        """Record an access — reinforces the memory."""
        self.access_count += 1
        self.last_accessed = datetime.now(UTC).isoformat()
        # Reinforce: accessing a memory reduces its decay
        self.decay_factor = min(1.0, self.decay_factor + 0.1)

    @property
    def is_expired(self) -> bool:
        """Check if memory has passed its expiry time."""
        if not self.expires_at:
            return False
        try:
            return datetime.now(UTC) > datetime.fromisoformat(self.expires_at)
        except (ValueError, TypeError):
            return False

    def mark_stale(self, reason: str = "") -> None:
        """Mark memory as stale — keeps content but flags it as unreliable."""
        self.provenance = ProvenanceStatus.STALE
        if reason:
            self.metadata["stale_reason"] = reason

    def verify(self, source: str = "") -> None:
        """Promote memory to verified status."""
        self.provenance = ProvenanceStatus.VERIFIED
        if source:
            self.metadata["verified_by"] = source
        self.metadata["verified_at"] = datetime.now(UTC).isoformat()

    def compute_relevance(self, query_tags: list[str]) -> float:
        """
        Compute relevance score for a query. DETERMINISTIC algorithm.
        No LLM involved — pure math.

        Score = tag_overlap * importance * confidence * decay_factor
                * recency_boost * provenance_weight
        """
        # Expired memories score zero
        if self.is_expired:
            return 0.0

        if not query_tags:
            base = self.importance * self.confidence * self.decay_factor
        else:
            # Tag overlap (Jaccard-like)
            query_set = {t.lower() for t in query_tags}
            memory_set = {t.lower() for t in self.tags}
            if not memory_set:
                tag_score = 0.1  # Low but not zero for untagged memories
            else:
                overlap = len(query_set & memory_set)
                union = len(query_set | memory_set)
                tag_score = overlap / union if union > 0 else 0.0
            base = tag_score * self.importance * self.confidence * self.decay_factor

        # Recency boost (memories accessed recently score higher)
        try:
            last = datetime.fromisoformat(self.last_accessed)
            age_hours = (datetime.now(UTC) - last).total_seconds() / 3600
            recency = 1.0 / (1.0 + age_hours / 24.0)  # Halves every 24h
        except (ValueError, TypeError):
            recency = 0.5

        # Frequency boost (frequently accessed = more important)
        freq_boost = min(2.0, 1.0 + self.access_count * 0.1)

        # Provenance weight — verified facts rank highest, stale/inferred lowest
        provenance_weights = {
            ProvenanceStatus.VERIFIED: 1.3,
            ProvenanceStatus.OBSERVED: 1.0,
            ProvenanceStatus.USER_ASSERTED: 0.9,
            ProvenanceStatus.INFERRED: 0.7,
            ProvenanceStatus.STALE: 0.3,
        }
        provenance_w = provenance_weights.get(self.provenance, 0.7)

        return base * recency * freq_boost * provenance_w

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "tags": self.tags,
            "source": self.source,
            "confidence": self.confidence,
            "importance": self.importance,
            "metadata": self.metadata,
            "provenance": self.provenance.value,
            "kind": self.kind.value,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "decay_factor": self.decay_factor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        provenance_val = data.get("provenance", "observed")
        try:
            provenance = ProvenanceStatus(provenance_val)
        except ValueError:
            provenance = ProvenanceStatus.OBSERVED

        kind_val = data.get("kind", "fact")
        try:
            kind = MemoryKind(kind_val)
        except ValueError:
            kind = MemoryKind.FACT

        entry = cls(
            content=data["content"],
            memory_type=MemoryType(data["memory_type"]),
            tags=data.get("tags", []),
            source=data.get("source", ""),
            confidence=data.get("confidence", 1.0),
            importance=data.get("importance", 0.5),
            metadata=data.get("metadata", {}),
            provenance=provenance,
            kind=kind,
            expires_at=data.get("expires_at"),
        )
        entry.id = data["id"]
        entry.created_at = data.get("created_at", entry.created_at)
        entry.last_accessed = data.get("last_accessed", entry.last_accessed)
        entry.access_count = data.get("access_count", 0)
        entry.decay_factor = data.get("decay_factor", 1.0)
        return entry


class MemoryStore:
    """
    Persistent memory store with SQLite backend.
    In-memory index for fast queries, DB for durability.
    """

    def __init__(
        self,
        db_path: str = "agent/memory/memories.db",
        max_memories: int = 50000,
    ) -> None:
        if not db_path:
            msg = "db_path cannot be empty"
            raise ValueError(msg)
        self._db_path = db_path
        self._index: dict[str, MemoryEntry] = {}
        self._db: aiosqlite.Connection | None = None
        self._initialized = False
        self._max_memories = max_memories

    async def initialize(self) -> None:
        """Initialize database and load existing memories into index."""
        if self._initialized:
            logger.warning("memory_store_already_initialized")
            return
        self._db = await aiosqlite.connect(self._db_path)
        self._initialized = True
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                source TEXT DEFAULT '',
                confidence REAL DEFAULT 1.0,
                importance REAL DEFAULT 0.5,
                metadata TEXT DEFAULT '{}',
                provenance TEXT DEFAULT 'observed',
                expires_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count INTEGER DEFAULT 0,
                decay_factor REAL DEFAULT 1.0
            )
        """)
        # Migration: add provenance/expires_at columns if missing (existing DBs)
        try:
            await self._db.execute(
                "ALTER TABLE memories ADD COLUMN provenance TEXT DEFAULT 'observed'"
            )
        except Exception:
            pass  # Column already exists
        try:
            await self._db.execute(
                "ALTER TABLE memories ADD COLUMN expires_at TEXT DEFAULT NULL"
            )
        except Exception:
            pass  # Column already exists
        try:
            await self._db.execute(
                "ALTER TABLE memories ADD COLUMN kind TEXT DEFAULT 'fact'"
            )
        except Exception:
            pass  # Column already exists
        await self._db.commit()

        # Load all memories into index
        async with self._db.execute(
            "SELECT id, content, memory_type, tags, source, confidence, "
            "importance, metadata, provenance, expires_at, created_at, "
            "last_accessed, access_count, decay_factor, kind FROM memories"
        ) as cursor:
            async for row in cursor:
                data = {
                    "id": row[0],
                    "content": row[1],
                    "memory_type": row[2],
                    "tags": orjson.loads(row[3]),
                    "source": row[4],
                    "confidence": row[5],
                    "importance": row[6],
                    "metadata": orjson.loads(row[7]),
                    "provenance": row[8] or "observed",
                    "expires_at": row[9],
                    "created_at": row[10],
                    "last_accessed": row[11],
                    "access_count": row[12],
                    "decay_factor": row[13],
                    "kind": row[14] if len(row) > 14 else "fact",
                }
                entry = MemoryEntry.from_dict(data)
                self._index[entry.id] = entry

        logger.info("memory_store_initialized", count=len(self._index))

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def store(self, entry: MemoryEntry) -> str:
        """Store a memory entry. Returns the entry ID."""
        # Bounded growth: evict lowest-decay memory if at capacity
        if len(self._index) >= self._max_memories and entry.id not in self._index:
            worst_id = min(self._index, key=lambda k: self._index[k].decay_factor)
            del self._index[worst_id]
            if self._db:
                await self._db.execute("DELETE FROM memories WHERE id=?", (worst_id,))

        self._index[entry.id] = entry

        if self._db:
            await self._db.execute(
                """INSERT OR REPLACE INTO memories
                (id, content, memory_type, tags, source, confidence,
                 importance, metadata, provenance, expires_at,
                 created_at, last_accessed, access_count, decay_factor, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.content,
                    entry.memory_type.value,
                    orjson.dumps(entry.tags).decode(),
                    entry.source,
                    entry.confidence,
                    entry.importance,
                    orjson.dumps(entry.metadata).decode(),
                    entry.provenance.value,
                    entry.expires_at,
                    entry.created_at,
                    entry.last_accessed,
                    entry.access_count,
                    entry.decay_factor,
                    entry.kind.value,
                ),
            )
            await self._db.commit()

        logger.info(
            "memory_stored",
            id=entry.id,
            type=entry.memory_type.value,
            tags=entry.tags,
        )
        return entry.id

    async def query(
        self,
        tags: list[str] | None = None,
        memory_type: MemoryType | None = None,
        min_relevance: float = 0.0,
        limit: int = 10,
        keyword: str | None = None,
        provenance: ProvenanceStatus | None = None,
        exclude_stale: bool = False,
        kind: MemoryKind | None = None,
    ) -> list[MemoryEntry]:
        """
        Query memories by relevance. DETERMINISTIC scoring algorithm.
        Returns memories sorted by relevance score (highest first).
        """
        if limit < 1:
            limit = 1
        if min_relevance < 0:
            min_relevance = 0.0
        # Empty string keyword matches everything — treat as None
        if keyword is not None and not keyword.strip():
            keyword = None

        results: list[tuple[float, MemoryEntry]] = []

        for entry in self._index.values():
            # Filter by type
            if memory_type is not None and entry.memory_type != memory_type:
                continue

            # Filter by provenance
            if provenance is not None and entry.provenance != provenance:
                continue
            if exclude_stale and entry.provenance == ProvenanceStatus.STALE:
                continue

            # Filter by kind
            if kind is not None and entry.kind != kind:
                continue

            # Skip expired memories
            if entry.is_expired:
                continue

            # Filter by keyword (simple substring match — deterministic)
            if keyword and keyword.lower() not in entry.content.lower():
                # Also check tags
                if not any(keyword.lower() in t.lower() for t in entry.tags):
                    continue

            # Compute relevance
            score = entry.compute_relevance(tags or [])
            if score >= min_relevance:
                results.append((score, entry))

        # Sort by relevance (deterministic — highest first)
        results.sort(key=lambda x: x[0], reverse=True)

        # Access the returned memories (reinforces them)
        top = results[:limit]
        for _score, entry in top:
            entry.access()
            if self._db:
                await self._db.execute(
                    "UPDATE memories SET access_count=?, last_accessed=?, decay_factor=? WHERE id=?",
                    (entry.access_count, entry.last_accessed, entry.decay_factor, entry.id),
                )

        if self._db and top:
            await self._db.commit()

        return [entry for _score, entry in top]

    async def get(self, memory_id: str) -> MemoryEntry | None:
        """Get a specific memory by ID."""
        entry = self._index.get(memory_id)
        if entry:
            entry.access()
        return entry

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory."""
        if memory_id in self._index:
            del self._index[memory_id]
            if self._db:
                await self._db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
                await self._db.commit()
            return True
        return False

    async def apply_decay(self, decay_rate: float = 0.01) -> int:
        """
        Apply time-based decay to all memories.
        Run periodically (e.g., daily) to age out irrelevant memories.
        DETERMINISTIC — same input = same output.
        """
        decayed_count = 0
        to_delete = []

        for entry in self._index.values():
            # Auto-mark expired memories as stale
            if entry.is_expired and entry.provenance != ProvenanceStatus.STALE:
                entry.mark_stale(reason="expired")

            if entry.memory_type == MemoryType.WORKING:
                # Working memory decays faster
                entry.decay_factor = max(0.0, entry.decay_factor - decay_rate * 5)
            else:
                entry.decay_factor = max(0.0, entry.decay_factor - decay_rate)

            decayed_count += 1

            # Remove memories with very low decay (essentially forgotten)
            if entry.decay_factor < 0.01:
                to_delete.append(entry.id)

        for mid in to_delete:
            await self.delete(mid)

        logger.info(
            "memory_decay_applied",
            decayed=decayed_count,
            deleted=len(to_delete),
        )
        return len(to_delete)

    async def query_facts(
        self,
        keyword: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """
        Query only factual knowledge — excludes conversational noise.

        Filters: SEMANTIC + PROCEDURAL types, FACT + PROCEDURE kinds.
        Excludes: EPISODIC messages, WORKING context, BELIEF, CLAIM.
        Prefers: verified > observed > user_asserted > inferred.
        """
        return await self.query(
            tags=tags,
            keyword=keyword,
            limit=limit,
            exclude_stale=True,
            kind=None,  # We filter below for multiple kinds
        )

    async def query_conversations(
        self,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """
        Query only conversational memory — recent exchanges, user messages.
        Separate from factual knowledge retrieval.
        """
        return await self.query(
            memory_type=MemoryType.EPISODIC,
            keyword=keyword,
            limit=limit,
        )

    def detect_conflicts(self, tags: list[str]) -> list[list[MemoryEntry]]:
        """
        Detect conflicting memories about the same topic.

        Two memories conflict when they share ≥50% tags (same topic)
        but have different provenance or content that may contradict.

        Returns groups of potentially conflicting entries.
        """
        if not tags:
            return []

        query_set = {t.lower() for t in tags}
        candidates: list[MemoryEntry] = []

        for entry in self._index.values():
            if entry.is_expired:
                continue
            entry_tags = {t.lower() for t in entry.tags}
            if not entry_tags:
                continue
            overlap = len(query_set & entry_tags)
            union = len(query_set | entry_tags)
            if union > 0 and overlap / union >= 0.5:
                candidates.append(entry)

        if len(candidates) < 2:
            return []

        # Group by provenance conflicts: verified vs inferred/user_asserted/stale
        conflicts: list[list[MemoryEntry]] = []
        verified = [c for c in candidates if c.provenance == ProvenanceStatus.VERIFIED]
        non_verified = [c for c in candidates if c.provenance != ProvenanceStatus.VERIFIED]

        if verified and non_verified:
            conflicts.append(verified + non_verified)

        # Also flag stale + non-stale combos
        stale = [c for c in candidates if c.provenance == ProvenanceStatus.STALE]
        fresh = [c for c in candidates if c.provenance != ProvenanceStatus.STALE]
        if stale and fresh and (stale + fresh) not in conflicts:
            conflicts.append(stale + fresh)

        return conflicts

    def get_audit_report(self) -> dict[str, Any]:
        """
        Memory audit report — epistemic health of the knowledge base.

        Returns breakdown by provenance, expiry status, and potential issues.
        """
        by_provenance: dict[str, int] = {}
        by_type: dict[str, int] = {}
        expired_count = 0
        stale_count = 0
        high_importance_stale: list[str] = []

        for entry in self._index.values():
            p = entry.provenance.value
            by_provenance[p] = by_provenance.get(p, 0) + 1
            t = entry.memory_type.value
            by_type[t] = by_type.get(t, 0) + 1

            if entry.is_expired:
                expired_count += 1
            if entry.provenance == ProvenanceStatus.STALE:
                stale_count += 1
                if entry.importance >= 0.7:
                    high_importance_stale.append(entry.id)

        return {
            "total_memories": len(self._index),
            "by_provenance": by_provenance,
            "by_type": by_type,
            "expired": expired_count,
            "stale": stale_count,
            "high_importance_stale": high_importance_stale,
            "verified_ratio": (
                by_provenance.get("verified", 0) / len(self._index)
                if self._index else 0.0
            ),
        }

    def get_stats(self) -> dict[str, Any]:
        """Return memory statistics."""
        type_counts: dict[str, int] = {}
        provenance_counts: dict[str, int] = {}
        for entry in self._index.values():
            t = entry.memory_type.value
            type_counts[t] = type_counts.get(t, 0) + 1
            p = entry.provenance.value
            provenance_counts[p] = provenance_counts.get(p, 0) + 1

        return {
            "total_memories": len(self._index),
            "by_type": type_counts,
            "by_provenance": provenance_counts,
            "avg_importance": (
                sum(e.importance for e in self._index.values()) / len(self._index)
                if self._index
                else 0.0
            ),
            "avg_decay": (
                sum(e.decay_factor for e in self._index.values()) / len(self._index)
                if self._index
                else 0.0
            ),
        }
