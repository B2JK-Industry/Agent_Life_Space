"""
Agent Life Space — Memory Consolidation

Premieňa surové episodické spomienky na vyššie formy pamäte.
Rovnako ako ľudský mozog počas spánku.

Flow:
    EPISODIC (čo sa stalo)
        ↓ konsolidácia
    SEMANTIC (čo z toho vyplýva — fakty, vzory, pravidlá)
        ↓ extrakcia postupov
    PROCEDURAL (ako to robiť — recepty, patterny, workflow)

    WORKING (aktuálny kontext — čo práve robím, čo je cieľ)

Konsolidácia beží periodicky (cron) a:
    1. Prečíta posledné episodické spomienky
    2. Hľadá vzory (opakujúce sa témy, úspechy, zlyhania)
    3. Extrahuje fakty → semantic memory
    4. Extrahuje postupy → procedural memory
    5. Vyčistí duplicitné/nepotrebné episodické záznamy
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import structlog

from agent.memory.store import MemoryEntry, MemoryStore, MemoryType

logger = structlog.get_logger(__name__)


# Patterns to detect in episodic memories
PATTERN_EXTRACTORS = {
    # User preferences (SK + EN)
    "user_preference": {
        "triggers": [
            "daniel chce", "daniel preferuje", "daniel zdôraznil", "daniel povedal",
            "daniel mi", "daniel sa", "majiteľ", "owner", "admin",
        ],
        "target_type": MemoryType.SEMANTIC,
        "tag": "user_preference",
    },
    # Skill results (SK + EN)
    "skill_learned": {
        "triggers": [
            "skill", "funguje", "otestoval", "úspech", "mastered", "learned",
            "success", "confidence", "works", "tested", "passed", "ok",
            "curl", "git", "docker", "pytest", "python",
        ],
        "target_type": MemoryType.PROCEDURAL,
        "tag": "skill_procedure",
    },
    # System knowledge (SK + EN)
    "system_fact": {
        "triggers": [
            "server", "cpu", "ram", "disk", "ubuntu", "port", "ip",
            "uptime", "healthy", "module", "process", "pid", "service",
            "b2jk", "agentlifespace",
        ],
        "target_type": MemoryType.SEMANTIC,
        "tag": "system_fact",
    },
    # Error patterns (SK + EN)
    "error_pattern": {
        "triggers": [
            "chyba", "error", "timeout", "zlyhalo", "nefunguje", "failed",
            "crash", "spadol", "neodpovedá", "rejected", "denied",
        ],
        "target_type": MemoryType.PROCEDURAL,
        "tag": "error_handling",
    },
    # Workflow patterns (SK + EN)
    "workflow": {
        "triggers": [
            "najprv", "potom", "postup", "kroky", "workflow", "pipeline",
            "pull", "restart", "deploy", "commit", "push", "test",
        ],
        "target_type": MemoryType.PROCEDURAL,
        "tag": "workflow",
    },
    # Communication patterns
    "communication": {
        "triggers": [
            "telegram", "odpovedal", "napísal", "responded", "message",
            "poslal", "received", "sent",
        ],
        "target_type": MemoryType.SEMANTIC,
        "tag": "communication_pattern",
    },
    # Agent identity
    "identity": {
        "triggers": [
            "john", "som", "ja ", "moje", "moja", "mám", "viem",
            "bytosť", "agent", "identity",
        ],
        "target_type": MemoryType.SEMANTIC,
        "tag": "self_knowledge",
    },
}


class MemoryConsolidation:
    """
    Konsoliduje episodické spomienky na semantic a procedural.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def consolidate(self) -> dict[str, Any]:
        """
        Hlavná konsolidácia. Spúšťa sa periodicky.
        """
        # Get all episodic memories
        episodic = await self._store.query(
            memory_type=MemoryType.EPISODIC,
            limit=200,
        )

        if not episodic:
            return {"consolidated": 0, "patterns_found": 0}

        # Extract patterns
        patterns_found = 0
        consolidated = 0

        # 1. Pattern matching — hľadaj opakujúce sa témy
        for entry in episodic:
            for _pattern_name, pattern in PATTERN_EXTRACTORS.items():
                content_lower = entry.content.lower()
                if any(trigger in content_lower for trigger in pattern["triggers"]):
                    # Check if we already have this as semantic/procedural
                    existing = await self._store.query(
                        memory_type=pattern["target_type"],
                        keyword=entry.content[:30],
                        limit=1,
                    )
                    if not existing:
                        new_entry = MemoryEntry(
                            content=entry.content,
                            memory_type=pattern["target_type"],
                            tags=entry.tags + [pattern["tag"], "consolidated"],
                            source="consolidation",
                            importance=min(1.0, entry.importance + 0.1),
                            confidence=entry.confidence,
                        )
                        await self._store.store(new_entry)
                        patterns_found += 1
                        consolidated += 1

        # 2. Frequency analysis — čo sa opakuje?
        tag_counts = Counter()
        for entry in episodic:
            for tag in entry.tags:
                tag_counts[tag] += 1

        # Tags with 3+ occurrences are "important topics"
        frequent_topics = {
            tag: count for tag, count in tag_counts.items()
            if count >= 3 and tag not in ("telegram", "user_input", "agent_response")
        }

        if frequent_topics:
            sorted_topics = sorted(frequent_topics.items(), key=lambda x: x[1], reverse=True)[:10]
            topic_summary = ", ".join(
                f"{tag} ({count}×)" for tag, count in sorted_topics
            )
            existing = await self._store.query(
                memory_type=MemoryType.SEMANTIC,
                keyword="frequent_topics",
                limit=1,
            )
            # Update or create
            summary_entry = MemoryEntry(
                content=f"Časté témy v konverzáciách: {topic_summary}",
                memory_type=MemoryType.SEMANTIC,
                tags=["meta", "frequent_topics", "consolidated"],
                source="consolidation",
                importance=0.7,
            )
            await self._store.store(summary_entry)
            consolidated += 1

        # 3. Deduplicate similar episodic memories
        deduplicated = await self._deduplicate_episodic(episodic)

        logger.info(
            "memory_consolidated",
            episodic_count=len(episodic),
            patterns_found=patterns_found,
            consolidated=consolidated,
            deduplicated=deduplicated,
        )

        return {
            "episodic_reviewed": len(episodic),
            "patterns_found": patterns_found,
            "new_semantic_procedural": consolidated,
            "deduplicated": deduplicated,
            "frequent_topics": dict(frequent_topics) if frequent_topics else {},
        }

    async def _deduplicate_episodic(self, entries: list[MemoryEntry]) -> int:
        """Remove near-duplicate episodic memories (keep the more important one)."""
        deleted = 0
        seen_prefixes: dict[str, MemoryEntry] = {}

        for entry in entries:
            # Use first 80 chars as dedup key
            prefix = entry.content[:80].lower().strip()
            if prefix in seen_prefixes:
                existing = seen_prefixes[prefix]
                # Keep the one with higher importance
                if entry.importance < existing.importance:
                    await self._store.delete(entry.id)
                    deleted += 1
                else:
                    await self._store.delete(existing.id)
                    seen_prefixes[prefix] = entry
                    deleted += 1
            else:
                seen_prefixes[prefix] = entry

        return deleted

    async def set_working_context(
        self,
        current_goal: str,
        active_conversation: str = "",
    ) -> str:
        """
        Set working memory — čo John práve robí.
        Working memory je vždy len jedna, prepisuje sa.
        """
        # Delete old working memories
        old_working = await self._store.query(
            memory_type=MemoryType.WORKING,
            limit=50,
        )
        for old in old_working:
            await self._store.delete(old.id)

        # Create new working context
        entry = MemoryEntry(
            content=f"AKTUÁLNY KONTEXT: {current_goal}. {active_conversation}",
            memory_type=MemoryType.WORKING,
            tags=["working", "current_context"],
            source="consolidation",
            importance=1.0,  # Working memory is always most important
        )
        return await self._store.store(entry)

    async def extract_user_patterns(self) -> list[dict[str, str]]:
        """
        Extrahuj vzory z interakcií s Danielom.
        Čo sa opakovane pýta? Čo kritizuje? Čo chváli?
        """
        user_msgs = await self._store.query(
            tags=["user_input"],
            limit=100,
        )

        patterns = []
        criticism_keywords = ["nefunguje", "prečo", "zle", "chyba", "neodpovedá", "timeout"]
        praise_keywords = ["super", "výborne", "funguje", "dobre", "presne"]

        criticism_count = 0
        praise_count = 0

        for msg in user_msgs:
            content_lower = msg.content.lower()
            if any(kw in content_lower for kw in criticism_keywords):
                criticism_count += 1
            if any(kw in content_lower for kw in praise_keywords):
                praise_count += 1

        if criticism_count > 0 or praise_count > 0:
            patterns.append({
                "type": "interaction_balance",
                "criticism": criticism_count,
                "praise": praise_count,
                "ratio": f"{praise_count}:{criticism_count}",
            })

        return patterns
