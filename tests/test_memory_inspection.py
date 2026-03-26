"""
Tests for memory inspection — owner visibility into agent knowledge.
"""

from __future__ import annotations

import pytest

from agent.memory.inspection import MemoryInspector
from agent.memory.store import (
    MemoryEntry,
    MemoryKind,
    MemoryStore,
    MemoryType,
    ProvenanceStatus,
)


class TestMemoryInspector:
    """Owner can inspect what the agent knows."""

    @pytest.fixture
    async def setup(self, tmp_path):
        store = MemoryStore(db_path=str(tmp_path / "inspect.db"))
        await store.initialize()
        inspector = MemoryInspector(store)
        yield store, inspector
        await store.close()

    @pytest.mark.asyncio
    async def test_overview_empty(self, setup):
        _, inspector = setup
        overview = inspector.get_overview()
        assert overview["total_memories"] == 0
        assert overview["verified_ratio"] == 0

    @pytest.mark.asyncio
    async def test_overview_with_data(self, setup):
        store, inspector = setup
        await store.store(MemoryEntry(
            content="fact1", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.VERIFIED,
        ))
        await store.store(MemoryEntry(
            content="fact2", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.INFERRED,
        ))
        overview = inspector.get_overview()
        assert overview["total_memories"] == 2
        assert overview["verified_ratio"] == 0.5

    @pytest.mark.asyncio
    async def test_get_by_provenance(self, setup):
        store, inspector = setup
        await store.store(MemoryEntry(
            content="verified", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.VERIFIED,
        ))
        await store.store(MemoryEntry(
            content="inferred", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.INFERRED,
        ))
        results = inspector.get_by_provenance(ProvenanceStatus.VERIFIED)
        assert len(results) == 1
        assert results[0]["content"] == "verified"

    @pytest.mark.asyncio
    async def test_get_by_kind(self, setup):
        store, inspector = setup
        await store.store(MemoryEntry(
            content="a fact", memory_type=MemoryType.SEMANTIC,
            kind=MemoryKind.FACT,
        ))
        await store.store(MemoryEntry(
            content="a belief", memory_type=MemoryType.SEMANTIC,
            kind=MemoryKind.BELIEF,
        ))
        results = inspector.get_by_kind(MemoryKind.BELIEF)
        assert len(results) == 1
        assert results[0]["kind"] == "belief"

    @pytest.mark.asyncio
    async def test_stale_report(self, setup):
        store, inspector = setup
        entry = MemoryEntry(
            content="old fact", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.STALE, importance=0.9,
        )
        entry.metadata["stale_reason"] = "not accessed"
        await store.store(entry)

        report = inspector.get_stale_report()
        assert report["total_stale"] == 1
        assert len(report["high_importance_stale"]) == 1

    @pytest.mark.asyncio
    async def test_search_with_provenance(self, setup):
        store, inspector = setup
        await store.store(MemoryEntry(
            content="Python 3.12 installed", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.VERIFIED, tags=["python"],
        ))
        results = inspector.search_with_provenance("python")
        assert len(results) == 1
        assert results[0]["provenance"] == "verified"

    @pytest.mark.asyncio
    async def test_conflict_report(self, setup):
        store, inspector = setup
        await store.store(MemoryEntry(
            content="RAM is 16GB", memory_type=MemoryType.SEMANTIC,
            tags=["server", "ram"], provenance=ProvenanceStatus.VERIFIED,
        ))
        await store.store(MemoryEntry(
            content="RAM is 32GB", memory_type=MemoryType.SEMANTIC,
            tags=["server", "ram"], provenance=ProvenanceStatus.INFERRED,
        ))
        report = inspector.get_conflict_report(["server", "ram"])
        assert report["has_conflicts"]
        assert report["total_conflicts"] >= 1
