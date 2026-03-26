"""
Agent Life Space — Memory Inspection

Owner can inspect what the agent "knows" and why.

Provides:
    - Full memory dump with provenance
    - Filtered views (by type, provenance, kind)
    - Conflict report
    - Stale fact report
    - Knowledge health score
"""

from __future__ import annotations

from typing import Any

import structlog

from agent.memory.store import MemoryKind, MemoryStore, ProvenanceStatus

logger = structlog.get_logger(__name__)


class MemoryInspector:
    """
    Inspection API for owner to understand what agent knows and why.
    Read-only — does not modify memories.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def get_overview(self) -> dict[str, Any]:
        """High-level overview of memory state."""
        audit = self._store.get_audit_report()
        stats = self._store.get_stats()
        return {
            "total_memories": stats["total_memories"],
            "by_type": stats.get("by_type", {}),
            "by_provenance": audit.get("by_provenance", {}),
            "verified_ratio": round(audit.get("verified_ratio", 0), 3),
            "stale_count": audit.get("stale", 0),
            "expired_count": audit.get("expired", 0),
            "high_importance_stale": audit.get("high_importance_stale", []),
            "avg_importance": round(stats.get("avg_importance", 0), 3),
            "avg_decay": round(stats.get("avg_decay", 0), 3),
        }

    def get_by_provenance(
        self, provenance: ProvenanceStatus, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get memories filtered by provenance status."""
        results = []
        for entry in self._store._index.values():
            if entry.provenance == provenance and not entry.is_expired:
                results.append({
                    "id": entry.id,
                    "content": entry.content[:200],
                    "type": entry.memory_type.value,
                    "provenance": entry.provenance.value,
                    "kind": entry.kind.value,
                    "confidence": entry.confidence,
                    "importance": entry.importance,
                    "access_count": entry.access_count,
                })
                if len(results) >= limit:
                    break
        return results

    def get_by_kind(
        self, kind: MemoryKind, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get memories filtered by epistemic kind."""
        results = []
        for entry in self._store._index.values():
            if entry.kind == kind and not entry.is_expired:
                results.append({
                    "id": entry.id,
                    "content": entry.content[:200],
                    "provenance": entry.provenance.value,
                    "kind": entry.kind.value,
                    "confidence": entry.confidence,
                })
                if len(results) >= limit:
                    break
        return results

    def get_verified_facts(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get all verified facts — the most trustworthy memories."""
        return self.get_by_provenance(ProvenanceStatus.VERIFIED, limit)

    def get_stale_report(self) -> dict[str, Any]:
        """Report on stale memories that may need attention."""
        stale = []
        high_importance = []
        for entry in self._store._index.values():
            if entry.provenance == ProvenanceStatus.STALE:
                item = {
                    "id": entry.id,
                    "content": entry.content[:150],
                    "importance": entry.importance,
                    "stale_reason": entry.metadata.get("stale_reason", ""),
                }
                stale.append(item)
                if entry.importance >= 0.7:
                    high_importance.append(item)
        return {
            "total_stale": len(stale),
            "high_importance_stale": high_importance,
            "stale_entries": stale[:20],
        }

    def get_conflict_report(self, tags: list[str]) -> dict[str, Any]:
        """Check for conflicting memories about a topic."""
        conflicts = self._store.detect_conflicts(tags)
        if not conflicts:
            return {"has_conflicts": False, "conflict_groups": []}

        groups = []
        for group in conflicts:
            groups.append([
                {
                    "id": e.id,
                    "content": e.content[:150],
                    "provenance": e.provenance.value,
                    "confidence": e.confidence,
                }
                for e in group
            ])
        return {
            "has_conflicts": True,
            "conflict_groups": groups,
            "total_conflicts": len(groups),
        }

    def search_with_provenance(
        self, keyword: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search memories with full provenance info."""
        results = []
        kw_lower = keyword.lower()
        for entry in self._store._index.values():
            if entry.is_expired:
                continue
            if kw_lower in entry.content.lower() or any(
                kw_lower in t.lower() for t in entry.tags
            ):
                results.append({
                    "id": entry.id,
                    "content": entry.content[:200],
                    "type": entry.memory_type.value,
                    "provenance": entry.provenance.value,
                    "kind": entry.kind.value,
                    "confidence": entry.confidence,
                    "importance": entry.importance,
                    "access_count": entry.access_count,
                    "decay_factor": round(entry.decay_factor, 3),
                })
                if len(results) >= limit:
                    break
        # Sort by relevance (importance * confidence * decay)
        results.sort(
            key=lambda r: r["importance"] * r["confidence"] * r["decay_factor"],
            reverse=True,
        )
        return results
