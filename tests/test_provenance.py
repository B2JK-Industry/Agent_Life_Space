"""
Tests for memory provenance model and epistemic status.
"""

from __future__ import annotations

import pytest

from agent.memory.store import MemoryEntry, MemoryStore, MemoryType, ProvenanceStatus


class TestProvenanceStatus:
    """ProvenanceStatus affects relevance scoring and filtering."""

    def test_default_provenance_is_observed(self):
        entry = MemoryEntry(content="test", memory_type=MemoryType.SEMANTIC)
        assert entry.provenance == ProvenanceStatus.OBSERVED

    def test_verified_scores_higher_than_inferred(self):
        verified = MemoryEntry(
            content="fact", memory_type=MemoryType.SEMANTIC,
            tags=["test"], provenance=ProvenanceStatus.VERIFIED,
        )
        inferred = MemoryEntry(
            content="fact", memory_type=MemoryType.SEMANTIC,
            tags=["test"], provenance=ProvenanceStatus.INFERRED,
        )
        # Same content, same tags — verified should score higher
        v_score = verified.compute_relevance(["test"])
        i_score = inferred.compute_relevance(["test"])
        assert v_score > i_score

    def test_stale_scores_lowest(self):
        stale = MemoryEntry(
            content="old fact", memory_type=MemoryType.SEMANTIC,
            tags=["test"], provenance=ProvenanceStatus.STALE,
        )
        observed = MemoryEntry(
            content="old fact", memory_type=MemoryType.SEMANTIC,
            tags=["test"], provenance=ProvenanceStatus.OBSERVED,
        )
        assert stale.compute_relevance(["test"]) < observed.compute_relevance(["test"])

    def test_user_asserted_scores_between_observed_and_inferred(self):
        user = MemoryEntry(
            content="claim", memory_type=MemoryType.SEMANTIC,
            tags=["x"], provenance=ProvenanceStatus.USER_ASSERTED,
        )
        observed = MemoryEntry(
            content="claim", memory_type=MemoryType.SEMANTIC,
            tags=["x"], provenance=ProvenanceStatus.OBSERVED,
        )
        inferred = MemoryEntry(
            content="claim", memory_type=MemoryType.SEMANTIC,
            tags=["x"], provenance=ProvenanceStatus.INFERRED,
        )
        u_score = user.compute_relevance(["x"])
        o_score = observed.compute_relevance(["x"])
        i_score = inferred.compute_relevance(["x"])
        assert i_score < u_score < o_score

    def test_mark_stale(self):
        entry = MemoryEntry(content="fact", memory_type=MemoryType.SEMANTIC)
        entry.mark_stale(reason="outdated")
        assert entry.provenance == ProvenanceStatus.STALE
        assert entry.metadata["stale_reason"] == "outdated"

    def test_verify(self):
        entry = MemoryEntry(
            content="claim", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.USER_ASSERTED,
        )
        entry.verify(source="api_check")
        assert entry.provenance == ProvenanceStatus.VERIFIED
        assert entry.metadata["verified_by"] == "api_check"
        assert "verified_at" in entry.metadata


class TestMemoryExpiry:
    """Memory entries can expire based on timestamp."""

    def test_no_expiry_by_default(self):
        entry = MemoryEntry(content="test", memory_type=MemoryType.WORKING)
        assert not entry.is_expired
        assert entry.expires_at is None

    def test_expired_entry(self):
        entry = MemoryEntry(
            content="temp", memory_type=MemoryType.WORKING,
            expires_at="2020-01-01T00:00:00+00:00",
        )
        assert entry.is_expired

    def test_expired_entry_scores_zero(self):
        entry = MemoryEntry(
            content="temp", memory_type=MemoryType.WORKING,
            tags=["test"], expires_at="2020-01-01T00:00:00+00:00",
        )
        assert entry.compute_relevance(["test"]) == 0.0


class TestProvenanceSerialization:
    """Provenance survives serialization roundtrip."""

    def test_to_dict_includes_provenance(self):
        entry = MemoryEntry(
            content="fact", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.VERIFIED,
            expires_at="2030-01-01T00:00:00+00:00",
        )
        d = entry.to_dict()
        assert d["provenance"] == "verified"
        assert d["expires_at"] == "2030-01-01T00:00:00+00:00"

    def test_from_dict_restores_provenance(self):
        d = {
            "id": "abc123",
            "content": "test",
            "memory_type": "semantic",
            "provenance": "user_asserted",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }
        entry = MemoryEntry.from_dict(d)
        assert entry.provenance == ProvenanceStatus.USER_ASSERTED
        assert entry.expires_at == "2030-01-01T00:00:00+00:00"

    def test_from_dict_handles_missing_provenance(self):
        """Backward compatibility: old entries without provenance default to OBSERVED."""
        d = {
            "id": "old123",
            "content": "old entry",
            "memory_type": "episodic",
        }
        entry = MemoryEntry.from_dict(d)
        assert entry.provenance == ProvenanceStatus.OBSERVED

    def test_from_dict_handles_invalid_provenance(self):
        d = {
            "id": "bad123",
            "content": "bad entry",
            "memory_type": "episodic",
            "provenance": "nonexistent_status",
        }
        entry = MemoryEntry.from_dict(d)
        assert entry.provenance == ProvenanceStatus.OBSERVED


class TestMemoryStoreProvenance:
    """MemoryStore query respects provenance filters."""

    @pytest.fixture
    async def store(self, tmp_path):
        s = MemoryStore(db_path=str(tmp_path / "test.db"))
        await s.initialize()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_query_filter_by_provenance(self, store):
        verified = MemoryEntry(
            content="verified fact", memory_type=MemoryType.SEMANTIC,
            tags=["test"], provenance=ProvenanceStatus.VERIFIED,
        )
        inferred = MemoryEntry(
            content="inferred thing", memory_type=MemoryType.SEMANTIC,
            tags=["test"], provenance=ProvenanceStatus.INFERRED,
        )
        await store.store(verified)
        await store.store(inferred)

        results = await store.query(provenance=ProvenanceStatus.VERIFIED)
        assert len(results) == 1
        assert results[0].provenance == ProvenanceStatus.VERIFIED

    @pytest.mark.asyncio
    async def test_query_exclude_stale(self, store):
        fresh = MemoryEntry(
            content="fresh", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.OBSERVED,
        )
        stale = MemoryEntry(
            content="stale", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.STALE,
        )
        await store.store(fresh)
        await store.store(stale)

        results = await store.query(exclude_stale=True)
        assert all(r.provenance != ProvenanceStatus.STALE for r in results)

    @pytest.mark.asyncio
    async def test_expired_excluded_from_query(self, store):
        valid = MemoryEntry(
            content="valid", memory_type=MemoryType.WORKING,
        )
        expired = MemoryEntry(
            content="expired", memory_type=MemoryType.WORKING,
            expires_at="2020-01-01T00:00:00+00:00",
        )
        await store.store(valid)
        await store.store(expired)

        results = await store.query()
        contents = [r.content for r in results]
        assert "valid" in contents
        assert "expired" not in contents

    @pytest.mark.asyncio
    async def test_provenance_persists_across_reload(self, tmp_path):
        """Provenance survives DB reload."""
        db_path = str(tmp_path / "persist_test.db")

        store1 = MemoryStore(db_path=db_path)
        await store1.initialize()
        entry = MemoryEntry(
            content="persistent fact", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.VERIFIED,
            expires_at="2030-01-01T00:00:00+00:00",
        )
        await store1.store(entry)
        await store1.close()

        store2 = MemoryStore(db_path=db_path)
        await store2.initialize()
        results = await store2.query(keyword="persistent")
        assert len(results) == 1
        assert results[0].provenance == ProvenanceStatus.VERIFIED
        assert results[0].expires_at == "2030-01-01T00:00:00+00:00"
        await store2.close()
