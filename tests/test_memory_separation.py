"""
Tests for factual vs conversational memory separation.
"""

from __future__ import annotations

import pytest

from agent.memory.store import (
    MemoryEntry,
    MemoryKind,
    MemoryStore,
    MemoryType,
    ProvenanceStatus,
)


class TestMemorySeparation:
    """Factual and conversational memory have separate retrieval paths."""

    @pytest.fixture
    async def store(self, tmp_path):
        s = MemoryStore(db_path=str(tmp_path / "sep.db"))
        await s.initialize()

        # Factual memories
        await s.store(MemoryEntry(
            content="Python 3.12 is installed",
            memory_type=MemoryType.SEMANTIC,
            kind=MemoryKind.FACT,
            tags=["python"],
            provenance=ProvenanceStatus.VERIFIED,
        ))
        await s.store(MemoryEntry(
            content="Deploy process: git pull, make build, restart",
            memory_type=MemoryType.PROCEDURAL,
            kind=MemoryKind.PROCEDURE,
            tags=["deploy"],
        ))

        # Conversational memories
        await s.store(MemoryEntry(
            content="Owner mi napísal: ahoj, ako sa máš?",
            memory_type=MemoryType.EPISODIC,
            kind=MemoryKind.CLAIM,
            tags=["message", "user_input"],
        ))
        await s.store(MemoryEntry(
            content="Owner mi napísal: pozri sa na ten bug",
            memory_type=MemoryType.EPISODIC,
            kind=MemoryKind.CLAIM,
            tags=["message", "user_input"],
        ))

        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_query_facts_excludes_conversations(self, store):
        facts = await store.query_facts(keyword="python")
        assert len(facts) >= 1
        assert all(e.memory_type != MemoryType.EPISODIC for e in facts)

    @pytest.mark.asyncio
    async def test_query_conversations_excludes_facts(self, store):
        convs = await store.query_conversations(keyword="Owner")
        assert len(convs) >= 1
        assert all(e.memory_type == MemoryType.EPISODIC for e in convs)

    @pytest.mark.asyncio
    async def test_kind_filter_in_query(self, store):
        beliefs_only = await store.query(kind=MemoryKind.FACT)
        assert all(e.kind == MemoryKind.FACT for e in beliefs_only)

    @pytest.mark.asyncio
    async def test_query_without_kind_returns_all(self, store):
        all_results = await store.query(limit=100)
        assert len(all_results) >= 4  # Both factual and conversational
