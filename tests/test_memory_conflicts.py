"""
Tests for memory conflict detection, belief vs fact, and audit reports.
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


class TestMemoryKind:
    """MemoryKind distinguishes facts from beliefs and claims."""

    def test_default_kind_is_fact(self):
        entry = MemoryEntry(content="test", memory_type=MemoryType.SEMANTIC)
        assert entry.kind == MemoryKind.FACT

    def test_belief_kind(self):
        entry = MemoryEntry(
            content="user prefers short answers",
            memory_type=MemoryType.SEMANTIC,
            kind=MemoryKind.BELIEF,
        )
        assert entry.kind == MemoryKind.BELIEF

    def test_claim_kind(self):
        entry = MemoryEntry(
            content="server has 32GB RAM",
            memory_type=MemoryType.SEMANTIC,
            kind=MemoryKind.CLAIM,
        )
        assert entry.kind == MemoryKind.CLAIM

    def test_procedure_kind(self):
        entry = MemoryEntry(
            content="to deploy run make deploy",
            memory_type=MemoryType.PROCEDURAL,
            kind=MemoryKind.PROCEDURE,
        )
        assert entry.kind == MemoryKind.PROCEDURE

    def test_kind_survives_roundtrip(self):
        entry = MemoryEntry(
            content="belief", memory_type=MemoryType.SEMANTIC,
            kind=MemoryKind.BELIEF,
        )
        d = entry.to_dict()
        restored = MemoryEntry.from_dict(d)
        assert restored.kind == MemoryKind.BELIEF

    def test_from_dict_handles_missing_kind(self):
        d = {"id": "x", "content": "old", "memory_type": "semantic"}
        entry = MemoryEntry.from_dict(d)
        assert entry.kind == MemoryKind.FACT

    def test_from_dict_handles_invalid_kind(self):
        d = {"id": "x", "content": "bad", "memory_type": "semantic", "kind": "invalid"}
        entry = MemoryEntry.from_dict(d)
        assert entry.kind == MemoryKind.FACT


class TestConflictDetection:
    """MemoryStore.detect_conflicts finds contradicting memories."""

    @pytest.fixture
    async def store(self, tmp_path):
        s = MemoryStore(db_path=str(tmp_path / "conflicts.db"))
        await s.initialize()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_no_conflicts_when_empty(self, store):
        assert store.detect_conflicts(["test"]) == []

    @pytest.mark.asyncio
    async def test_no_conflicts_single_entry(self, store):
        await store.store(MemoryEntry(
            content="fact", memory_type=MemoryType.SEMANTIC, tags=["server"],
        ))
        assert store.detect_conflicts(["server"]) == []

    @pytest.mark.asyncio
    async def test_detects_verified_vs_inferred_conflict(self, store):
        await store.store(MemoryEntry(
            content="server has 16GB", memory_type=MemoryType.SEMANTIC,
            tags=["server", "ram"], provenance=ProvenanceStatus.VERIFIED,
        ))
        await store.store(MemoryEntry(
            content="server has 32GB", memory_type=MemoryType.SEMANTIC,
            tags=["server", "ram"], provenance=ProvenanceStatus.INFERRED,
        ))

        conflicts = store.detect_conflicts(["server", "ram"])
        assert len(conflicts) >= 1
        # Conflict group should contain both entries
        all_contents = [e.content for group in conflicts for e in group]
        assert "server has 16GB" in all_contents
        assert "server has 32GB" in all_contents

    @pytest.mark.asyncio
    async def test_detects_stale_vs_fresh(self, store):
        await store.store(MemoryEntry(
            content="Python 3.11", memory_type=MemoryType.SEMANTIC,
            tags=["python", "version"], provenance=ProvenanceStatus.STALE,
        ))
        await store.store(MemoryEntry(
            content="Python 3.12", memory_type=MemoryType.SEMANTIC,
            tags=["python", "version"], provenance=ProvenanceStatus.OBSERVED,
        ))

        conflicts = store.detect_conflicts(["python", "version"])
        assert len(conflicts) >= 1

    @pytest.mark.asyncio
    async def test_no_conflict_same_provenance(self, store):
        await store.store(MemoryEntry(
            content="fact A", memory_type=MemoryType.SEMANTIC,
            tags=["topic"], provenance=ProvenanceStatus.OBSERVED,
        ))
        await store.store(MemoryEntry(
            content="fact B", memory_type=MemoryType.SEMANTIC,
            tags=["topic"], provenance=ProvenanceStatus.OBSERVED,
        ))

        # Two observed facts about same topic — not necessarily a conflict
        conflicts = store.detect_conflicts(["topic"])
        assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_no_conflict_different_topics(self, store):
        await store.store(MemoryEntry(
            content="Python is great", memory_type=MemoryType.SEMANTIC,
            tags=["python"], provenance=ProvenanceStatus.VERIFIED,
        ))
        await store.store(MemoryEntry(
            content="Rust is fast", memory_type=MemoryType.SEMANTIC,
            tags=["rust"], provenance=ProvenanceStatus.INFERRED,
        ))

        conflicts = store.detect_conflicts(["python"])
        assert len(conflicts) == 0


class TestAuditReport:
    """MemoryStore audit report gives epistemic health overview."""

    @pytest.fixture
    async def store(self, tmp_path):
        s = MemoryStore(db_path=str(tmp_path / "audit.db"))
        await s.initialize()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_empty_report(self, store):
        report = store.get_audit_report()
        assert report["total_memories"] == 0
        assert report["verified_ratio"] == 0.0

    @pytest.mark.asyncio
    async def test_report_counts_provenance(self, store):
        await store.store(MemoryEntry(
            content="v1", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.VERIFIED,
        ))
        await store.store(MemoryEntry(
            content="v2", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.VERIFIED,
        ))
        await store.store(MemoryEntry(
            content="i1", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.INFERRED,
        ))

        report = store.get_audit_report()
        assert report["by_provenance"]["verified"] == 2
        assert report["by_provenance"]["inferred"] == 1
        assert report["verified_ratio"] == pytest.approx(2 / 3, abs=0.01)

    @pytest.mark.asyncio
    async def test_report_flags_high_importance_stale(self, store):
        entry = MemoryEntry(
            content="important stale fact", memory_type=MemoryType.SEMANTIC,
            importance=0.9, provenance=ProvenanceStatus.STALE,
        )
        await store.store(entry)

        report = store.get_audit_report()
        assert report["stale"] == 1
        assert entry.id in report["high_importance_stale"]

    @pytest.mark.asyncio
    async def test_stats_includes_provenance(self, store):
        await store.store(MemoryEntry(
            content="test", memory_type=MemoryType.SEMANTIC,
            provenance=ProvenanceStatus.OBSERVED,
        ))
        stats = store.get_stats()
        assert "by_provenance" in stats
        assert stats["by_provenance"]["observed"] == 1
