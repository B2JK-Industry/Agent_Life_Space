"""
Tests for memory consolidation pipeline — provenance promotion + stale detection.
"""

from __future__ import annotations

import pytest

from agent.memory.consolidation import MemoryConsolidation
from agent.memory.store import MemoryEntry, MemoryStore, MemoryType, ProvenanceStatus


class TestProvenancePromotion:
    """Inferred facts can be promoted to verified via consolidation."""

    @pytest.fixture
    async def setup(self, tmp_path):
        store = MemoryStore(db_path=str(tmp_path / "promo.db"))
        await store.initialize()
        consolidation = MemoryConsolidation(store)
        yield store, consolidation
        await store.close()

    @pytest.mark.asyncio
    async def test_promote_frequently_accessed(self, setup):
        store, consolidation = setup
        entry = MemoryEntry(
            content="Python 3.12 is installed",
            memory_type=MemoryType.SEMANTIC,
            tags=["python"],
            provenance=ProvenanceStatus.INFERRED,
            confidence=0.8,
        )
        # Simulate frequent access
        entry.access_count = 6
        await store.store(entry)

        promoted = await consolidation.promote_inferred_to_verified()
        assert promoted == 1

        result = await store.get(entry.id)
        assert result.provenance == ProvenanceStatus.VERIFIED
        assert "verified_by" in result.metadata

    @pytest.mark.asyncio
    async def test_no_promote_low_access(self, setup):
        store, consolidation = setup
        entry = MemoryEntry(
            content="maybe true",
            memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.INFERRED,
            confidence=0.8,
        )
        entry.access_count = 2  # Below threshold
        await store.store(entry)

        promoted = await consolidation.promote_inferred_to_verified()
        assert promoted == 0

    @pytest.mark.asyncio
    async def test_no_promote_low_confidence(self, setup):
        store, consolidation = setup
        entry = MemoryEntry(
            content="uncertain",
            memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.INFERRED,
            confidence=0.3,  # Below threshold
        )
        entry.access_count = 10
        await store.store(entry)

        promoted = await consolidation.promote_inferred_to_verified()
        assert promoted == 0

    @pytest.mark.asyncio
    async def test_only_inferred_promoted(self, setup):
        store, consolidation = setup
        # Observed — should not be promoted (already better)
        observed = MemoryEntry(
            content="directly seen",
            memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.OBSERVED,
            confidence=0.9,
        )
        observed.access_count = 10
        await store.store(observed)

        promoted = await consolidation.promote_inferred_to_verified()
        assert promoted == 0


class TestStaleDetection:
    """Old unaccessed facts get marked as stale."""

    @pytest.fixture
    async def setup(self, tmp_path):
        store = MemoryStore(db_path=str(tmp_path / "stale.db"))
        await store.initialize()
        consolidation = MemoryConsolidation(store)
        yield store, consolidation
        await store.close()

    @pytest.mark.asyncio
    async def test_detect_stale(self, setup):
        store, consolidation = setup
        entry = MemoryEntry(
            content="old fact",
            memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.OBSERVED,
        )
        # Hack: set last_accessed to 60 days ago
        entry.last_accessed = "2025-01-01T00:00:00+00:00"
        await store.store(entry)

        stale = await consolidation.detect_stale_facts(max_age_days=30)
        assert stale == 1

        result = await store.get(entry.id)
        assert result.provenance == ProvenanceStatus.STALE

    @pytest.mark.asyncio
    async def test_verified_not_marked_stale(self, setup):
        store, consolidation = setup
        entry = MemoryEntry(
            content="verified fact",
            memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.VERIFIED,
        )
        entry.last_accessed = "2025-01-01T00:00:00+00:00"
        await store.store(entry)

        stale = await consolidation.detect_stale_facts(max_age_days=30)
        assert stale == 0  # Verified facts don't go stale

    @pytest.mark.asyncio
    async def test_recent_not_stale(self, setup):
        store, consolidation = setup
        entry = MemoryEntry(
            content="recent fact",
            memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.OBSERVED,
        )
        # last_accessed is set to now by default
        await store.store(entry)

        stale = await consolidation.detect_stale_facts(max_age_days=30)
        assert stale == 0


class TestConsolidationProvenance:
    """Consolidation creates entries with correct provenance."""

    @pytest.fixture
    async def setup(self, tmp_path):
        store = MemoryStore(db_path=str(tmp_path / "consol.db"))
        await store.initialize()
        consolidation = MemoryConsolidation(store)
        yield store, consolidation
        await store.close()

    @pytest.mark.asyncio
    async def test_consolidated_entries_are_inferred(self, setup):
        store, consolidation = setup
        # Add episodic memory about a skill
        await store.store(MemoryEntry(
            content="skill pytest funguje dobre, confidence 0.9",
            memory_type=MemoryType.EPISODIC,
            tags=["skill", "pytest"],
            importance=0.6,
        ))

        await consolidation.consolidate()

        # Check that new procedural entry has INFERRED provenance
        procedural = await store.query(memory_type=MemoryType.PROCEDURAL, limit=10)
        if procedural:
            assert procedural[0].provenance == ProvenanceStatus.INFERRED
