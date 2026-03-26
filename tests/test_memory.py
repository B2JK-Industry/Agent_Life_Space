"""
Test scenarios for Memory Store.

Practical scenarios:
1. Store and retrieve memories
2. Relevance scoring is deterministic
3. Memory decay works over time
4. Tag-based retrieval finds relevant memories
5. Keyword search works
6. Working memory decays faster than long-term
7. Frequently accessed memories rank higher
8. SQLite persistence survives restart
9. Memory types are properly segregated
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent.memory.store import MemoryEntry, MemoryStore, MemoryType


@pytest.fixture
async def store():
    """Create a temporary memory store for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = MemoryStore(db_path=db_path)
    await store.initialize()
    yield store
    await store.close()
    os.unlink(db_path)


class TestMemoryEntry:
    """Memory entry creation and metadata."""

    def test_create_entry(self) -> None:
        entry = MemoryEntry(
            content="Python is great for AI",
            memory_type=MemoryType.SEMANTIC,
            tags=["python", "ai", "programming"],
            source="research",
            importance=0.8,
        )
        assert entry.content == "Python is great for AI"
        assert entry.memory_type == MemoryType.SEMANTIC
        assert len(entry.tags) == 3
        assert entry.confidence == 1.0
        assert entry.importance == 0.8
        assert entry.access_count == 0
        assert entry.decay_factor == 1.0

    def test_confidence_clamped(self) -> None:
        """Confidence must be 0-1."""
        entry = MemoryEntry(
            content="test",
            memory_type=MemoryType.SEMANTIC,
            confidence=5.0,
        )
        assert entry.confidence == 1.0

        entry2 = MemoryEntry(
            content="test",
            memory_type=MemoryType.SEMANTIC,
            confidence=-1.0,
        )
        assert entry2.confidence == 0.0

    def test_access_reinforces(self) -> None:
        """Accessing a memory increases its staying power."""
        entry = MemoryEntry(
            content="test",
            memory_type=MemoryType.EPISODIC,
        )
        entry.decay_factor = 0.5
        entry.access()
        assert entry.access_count == 1
        assert entry.decay_factor == 0.6  # Reinforced

    def test_serialization_round_trip(self) -> None:
        """Memory entries survive dict serialization."""
        entry = MemoryEntry(
            content="test fact",
            memory_type=MemoryType.SEMANTIC,
            tags=["test", "fact"],
            importance=0.9,
            metadata={"source_url": "https://example.com"},
        )
        data = entry.to_dict()
        restored = MemoryEntry.from_dict(data)
        assert restored.content == entry.content
        assert restored.memory_type == entry.memory_type
        assert restored.tags == entry.tags
        assert restored.importance == entry.importance


class TestRelevanceScoring:
    """Relevance scoring must be deterministic — same input = same output."""

    def test_matching_tags_score_higher(self) -> None:
        entry = MemoryEntry(
            content="Python web frameworks",
            memory_type=MemoryType.SEMANTIC,
            tags=["python", "web", "frameworks"],
            importance=0.8,
        )
        score_match = entry.compute_relevance(["python", "web"])
        score_nomatch = entry.compute_relevance(["rust", "systems"])
        assert score_match > score_nomatch

    def test_deterministic(self) -> None:
        """Same query on same memory = same score. Always."""
        entry = MemoryEntry(
            content="test",
            memory_type=MemoryType.SEMANTIC,
            tags=["a", "b"],
            importance=0.7,
        )
        score1 = entry.compute_relevance(["a"])
        score2 = entry.compute_relevance(["a"])
        # Tiny float drift from time-based recency is acceptable
        assert abs(score1 - score2) < 1e-6

    def test_higher_importance_scores_higher(self) -> None:
        high = MemoryEntry(
            content="important",
            memory_type=MemoryType.SEMANTIC,
            tags=["test"],
            importance=0.9,
        )
        low = MemoryEntry(
            content="not important",
            memory_type=MemoryType.SEMANTIC,
            tags=["test"],
            importance=0.1,
        )
        assert high.compute_relevance(["test"]) > low.compute_relevance(["test"])

    def test_empty_tags_still_scores(self) -> None:
        """Memories without tags still get a base score."""
        entry = MemoryEntry(
            content="untagged memory",
            memory_type=MemoryType.SEMANTIC,
            importance=0.5,
        )
        score = entry.compute_relevance(["anything"])
        assert score > 0  # Not zero


class TestMemoryStore:
    """Store, query, and manage memories."""

    @pytest.mark.asyncio
    async def test_store_and_get(self, store: MemoryStore) -> None:
        entry = MemoryEntry(
            content="Test memory",
            memory_type=MemoryType.SEMANTIC,
            tags=["test"],
        )
        mem_id = await store.store(entry)
        retrieved = await store.get(mem_id)
        assert retrieved is not None
        assert retrieved.content == "Test memory"

    @pytest.mark.asyncio
    async def test_query_by_tags(self, store: MemoryStore) -> None:
        """Query returns relevant memories sorted by score."""
        await store.store(
            MemoryEntry(
                content="Python async patterns",
                memory_type=MemoryType.SEMANTIC,
                tags=["python", "async"],
                importance=0.8,
            )
        )
        await store.store(
            MemoryEntry(
                content="Rust ownership model",
                memory_type=MemoryType.SEMANTIC,
                tags=["rust", "memory"],
                importance=0.7,
            )
        )
        await store.store(
            MemoryEntry(
                content="Python web frameworks",
                memory_type=MemoryType.SEMANTIC,
                tags=["python", "web"],
                importance=0.9,
            )
        )

        results = await store.query(tags=["python"])
        assert len(results) >= 2
        # Python memories should rank higher than Rust memory
        contents = [r.content for r in results]
        assert "Rust ownership model" not in contents[:2]

    @pytest.mark.asyncio
    async def test_query_by_type(self, store: MemoryStore) -> None:
        await store.store(
            MemoryEntry(
                content="Learned to use git rebase",
                memory_type=MemoryType.PROCEDURAL,
                tags=["git"],
            )
        )
        await store.store(
            MemoryEntry(
                content="Git was created by Linus",
                memory_type=MemoryType.SEMANTIC,
                tags=["git"],
            )
        )

        procedural = await store.query(memory_type=MemoryType.PROCEDURAL)
        assert len(procedural) == 1
        assert procedural[0].content == "Learned to use git rebase"

    @pytest.mark.asyncio
    async def test_query_by_keyword(self, store: MemoryStore) -> None:
        await store.store(
            MemoryEntry(
                content="The API rate limit is 100 requests per minute",
                memory_type=MemoryType.SEMANTIC,
                tags=["api", "limits"],
            )
        )
        await store.store(
            MemoryEntry(
                content="Server runs on port 8080",
                memory_type=MemoryType.SEMANTIC,
                tags=["server", "config"],
            )
        )

        results = await store.query(keyword="rate limit")
        assert len(results) == 1
        assert "rate limit" in results[0].content

    @pytest.mark.asyncio
    async def test_delete_memory(self, store: MemoryStore) -> None:
        entry = MemoryEntry(
            content="To be deleted",
            memory_type=MemoryType.WORKING,
        )
        mem_id = await store.store(entry)
        assert await store.delete(mem_id)
        assert await store.get(mem_id) is None

    @pytest.mark.asyncio
    async def test_access_increases_count(self, store: MemoryStore) -> None:
        """Querying memories reinforces them (access count increases)."""
        entry = MemoryEntry(
            content="Frequently needed fact",
            memory_type=MemoryType.SEMANTIC,
            tags=["important"],
            importance=0.9,
        )
        mem_id = await store.store(entry)

        # Query twice
        await store.query(tags=["important"])
        await store.query(tags=["important"])

        retrieved = await store.get(mem_id)
        assert retrieved is not None
        assert retrieved.access_count >= 2

    @pytest.mark.asyncio
    async def test_stats(self, store: MemoryStore) -> None:
        await store.store(
            MemoryEntry(content="a", memory_type=MemoryType.SEMANTIC)
        )
        await store.store(
            MemoryEntry(content="b", memory_type=MemoryType.EPISODIC)
        )
        await store.store(
            MemoryEntry(content="c", memory_type=MemoryType.SEMANTIC)
        )

        stats = store.get_stats()
        assert stats["total_memories"] == 3
        assert stats["by_type"]["semantic"] == 2
        assert stats["by_type"]["episodic"] == 1


class TestMemoryDecay:
    """Working memory decays faster than long-term."""

    @pytest.mark.asyncio
    async def test_decay_reduces_factor(self, store: MemoryStore) -> None:
        entry = MemoryEntry(
            content="Temporary info",
            memory_type=MemoryType.SEMANTIC,
        )
        await store.store(entry)

        await store.apply_decay(decay_rate=0.1)
        assert entry.decay_factor < 1.0

    @pytest.mark.asyncio
    async def test_working_memory_decays_faster(self, store: MemoryStore) -> None:
        working = MemoryEntry(
            content="Current task context",
            memory_type=MemoryType.WORKING,
        )
        semantic = MemoryEntry(
            content="Long term fact",
            memory_type=MemoryType.SEMANTIC,
        )
        await store.store(working)
        await store.store(semantic)

        await store.apply_decay(decay_rate=0.1)

        # Working memory should have decayed more
        assert working.decay_factor < semantic.decay_factor

    @pytest.mark.asyncio
    async def test_very_decayed_memories_deleted(self, store: MemoryStore) -> None:
        """Memories with near-zero decay are forgotten (deleted)."""
        entry = MemoryEntry(
            content="Forgotten",
            memory_type=MemoryType.WORKING,
        )
        entry.decay_factor = 0.005  # Almost gone
        await store.store(entry)

        deleted_count = await store.apply_decay(decay_rate=0.01)
        assert deleted_count >= 1
        assert store.get_stats()["total_memories"] == 0


class TestMemoryPersistence:
    """Memories must survive DB reconnection."""

    @pytest.mark.asyncio
    async def test_persistence_across_restart(self) -> None:
        """Store memory, close DB, reopen, memory is still there."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Store a memory
            store1 = MemoryStore(db_path=db_path)
            await store1.initialize()
            entry = MemoryEntry(
                content="Persistent fact",
                memory_type=MemoryType.SEMANTIC,
                tags=["persistent"],
                importance=0.95,
            )
            mem_id = await store1.store(entry)
            await store1.close()

            # Reopen and verify
            store2 = MemoryStore(db_path=db_path)
            await store2.initialize()
            retrieved = await store2.get(mem_id)
            assert retrieved is not None
            assert retrieved.content == "Persistent fact"
            assert retrieved.importance == 0.95
            assert retrieved.tags == ["persistent"]
            await store2.close()
        finally:
            os.unlink(db_path)
