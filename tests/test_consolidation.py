"""
Test scenarios for Memory Consolidation.

Practical scenarios:
1. Episodic memories with matching triggers get consolidated to semantic/procedural
2. Deduplication removes near-identical episodic memories
3. Frequency analysis detects repeated topics
4. Working memory is set and overwrites previous
5. User pattern extraction finds criticism/praise
6. Consolidation is idempotent — running twice doesn't create duplicates
7. Multiple pattern types extracted from single consolidation run
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from agent.memory.consolidation import MemoryConsolidation
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


@pytest.fixture
async def consolidator(store):
    return MemoryConsolidation(store)


class TestPatternExtraction:
    """Consolidation extracts semantic/procedural from episodic memories."""

    @pytest.mark.asyncio
    async def test_user_preference_becomes_semantic(self, store, consolidator):
        """When Daniel says he wants something, it becomes a semantic memory."""
        await store.store(MemoryEntry(
            content="Daniel chce aby som odpovedal stručne a po slovensky",
            memory_type=MemoryType.EPISODIC,
            tags=["telegram", "user_input"],
            source="telegram",
            importance=0.6,
        ))

        report = await consolidator.consolidate()

        assert report["patterns_found"] >= 1
        semantic = await store.query(memory_type=MemoryType.SEMANTIC, limit=10)
        contents = [m.content for m in semantic]
        assert any("Daniel chce" in c for c in contents)

    @pytest.mark.asyncio
    async def test_skill_learned_becomes_procedural(self, store, consolidator):
        """When a skill succeeds, it creates a procedural memory."""
        await store.store(MemoryEntry(
            content="Otestoval som curl na GitHub API a funguje správne",
            memory_type=MemoryType.EPISODIC,
            tags=["skill", "curl", "github"],
            source="john",
            importance=0.7,
        ))

        report = await consolidator.consolidate()

        assert report["patterns_found"] >= 1
        procedural = await store.query(memory_type=MemoryType.PROCEDURAL, limit=10)
        assert len(procedural) >= 1
        assert any("skill_procedure" in m.tags for m in procedural)

    @pytest.mark.asyncio
    async def test_system_fact_becomes_semantic(self, store, consolidator):
        """System facts (server, CPU, RAM) become semantic memories."""
        await store.store(MemoryEntry(
            content="Server b2jk-agentlifespace beží na Ubuntu 24.04, CPU i7-5500U, 8GB RAM",
            memory_type=MemoryType.EPISODIC,
            tags=["system", "health"],
            source="watchdog",
            importance=0.5,
        ))

        report = await consolidator.consolidate()

        semantic = await store.query(memory_type=MemoryType.SEMANTIC, limit=10)
        assert any("system_fact" in m.tags for m in semantic)

    @pytest.mark.asyncio
    async def test_error_pattern_becomes_procedural(self, store, consolidator):
        """Error patterns become procedural memories (how to handle errors)."""
        await store.store(MemoryEntry(
            content="Timeout error pri volaní Claude API — zvýšil som timeout na 120s",
            memory_type=MemoryType.EPISODIC,
            tags=["error", "api"],
            source="john",
            importance=0.8,
        ))

        report = await consolidator.consolidate()

        procedural = await store.query(memory_type=MemoryType.PROCEDURAL, limit=10)
        assert any("error_handling" in m.tags for m in procedural)

    @pytest.mark.asyncio
    async def test_workflow_becomes_procedural(self, store, consolidator):
        """Workflow descriptions become procedural memories."""
        await store.store(MemoryEntry(
            content="Postup deploy: najprv git pull, potom restart service, test health",
            memory_type=MemoryType.EPISODIC,
            tags=["workflow", "deploy"],
            source="john",
            importance=0.7,
        ))

        report = await consolidator.consolidate()

        procedural = await store.query(memory_type=MemoryType.PROCEDURAL, limit=10)
        assert any("workflow" in m.tags for m in procedural)

    @pytest.mark.asyncio
    async def test_no_duplicate_consolidation(self, store, consolidator):
        """Running consolidation twice doesn't create duplicates."""
        await store.store(MemoryEntry(
            content="Daniel chce aby John bol autonómny",
            memory_type=MemoryType.EPISODIC,
            tags=["telegram", "user_input"],
            source="telegram",
            importance=0.6,
        ))

        report1 = await consolidator.consolidate()
        report2 = await consolidator.consolidate()

        # Second run should find 0 new patterns (already consolidated)
        assert report2["patterns_found"] == 0

    @pytest.mark.asyncio
    async def test_multiple_patterns_single_run(self, store, consolidator):
        """One consolidation run extracts multiple pattern types."""
        entries = [
            MemoryEntry(
                content="Daniel preferuje stručné odpovede",
                memory_type=MemoryType.EPISODIC,
                tags=["telegram", "user_input"],
                source="telegram", importance=0.6,
            ),
            MemoryEntry(
                content="Git push funguje cez GITHUB_TOKEN, otestoval som to",
                memory_type=MemoryType.EPISODIC,
                tags=["skill", "git"],
                source="john", importance=0.7,
            ),
            MemoryEntry(
                content="Server má CPU na 35% a RAM na 62%",
                memory_type=MemoryType.EPISODIC,
                tags=["health"],
                source="watchdog", importance=0.4,
            ),
        ]
        for entry in entries:
            await store.store(entry)

        report = await consolidator.consolidate()

        assert report["patterns_found"] >= 2
        semantic = await store.query(memory_type=MemoryType.SEMANTIC, limit=10)
        procedural = await store.query(memory_type=MemoryType.PROCEDURAL, limit=10)
        assert len(semantic) >= 1
        assert len(procedural) >= 1


class TestDeduplication:
    """Consolidation removes near-duplicate episodic memories."""

    @pytest.mark.asyncio
    async def test_duplicates_removed(self, store, consolidator):
        """Two episodic memories with same first 80 chars — one gets removed."""
        # Dedup uses first 80 chars as key — both must share that prefix
        shared_prefix = "Daniel mi napísal presne toto: ahoj, ako sa máš? Čo nového na svete? Povedz mi niečo."
        await store.store(MemoryEntry(
            content=shared_prefix,
            memory_type=MemoryType.EPISODIC,
            tags=["telegram"], source="telegram", importance=0.3,
        ))
        await store.store(MemoryEntry(
            content=shared_prefix + " Mám pre teba úlohu navyše.",
            memory_type=MemoryType.EPISODIC,
            tags=["telegram"], source="telegram", importance=0.5,
        ))

        report = await consolidator.consolidate()

        assert report["deduplicated"] >= 1

    @pytest.mark.asyncio
    async def test_different_memories_not_deduplicated(self, store, consolidator):
        """Clearly different memories should both survive."""
        await store.store(MemoryEntry(
            content="Daniel mi napísal o serveri a jeho konfigurácii",
            memory_type=MemoryType.EPISODIC,
            tags=["telegram"], source="telegram", importance=0.5,
        ))
        await store.store(MemoryEntry(
            content="Spustil som pytest a všetkých 219 testov prešlo",
            memory_type=MemoryType.EPISODIC,
            tags=["skill", "testing"], source="john", importance=0.6,
        ))

        report = await consolidator.consolidate()

        assert report["deduplicated"] == 0


class TestFrequencyAnalysis:
    """Consolidation detects frequently occurring topics."""

    @pytest.mark.asyncio
    async def test_frequent_tags_create_summary(self, store, consolidator):
        """Tags appearing 3+ times create a topic summary."""
        for i in range(4):
            await store.store(MemoryEntry(
                content=f"Git operácia #{i}: commit a push",
                memory_type=MemoryType.EPISODIC,
                tags=["git", "code"],
                source="john", importance=0.5,
            ))

        report = await consolidator.consolidate()

        # Should detect "git" as frequent topic
        assert "git" in report.get("frequent_topics", {}) or report["new_semantic_procedural"] > 0


class TestWorkingMemory:
    """Working memory tracks current context."""

    @pytest.mark.asyncio
    async def test_set_working_context(self, store, consolidator):
        """Working memory is set and queryable."""
        mem_id = await consolidator.set_working_context(
            current_goal="Opraviť konsolidáciu pamäte",
            active_conversation="Daniel pýta na testy",
        )

        assert mem_id
        working = await store.query(memory_type=MemoryType.WORKING, limit=1)
        assert len(working) == 1
        assert "Opraviť konsolidáciu" in working[0].content

    @pytest.mark.asyncio
    async def test_working_context_overwrites(self, store, consolidator):
        """Setting working context replaces old one."""
        await consolidator.set_working_context(current_goal="Stará úloha")
        await consolidator.set_working_context(current_goal="Nová úloha")

        working = await store.query(memory_type=MemoryType.WORKING, limit=10)
        assert len(working) == 1
        assert "Nová úloha" in working[0].content


class TestUserPatterns:
    """Extraction of user interaction patterns."""

    @pytest.mark.asyncio
    async def test_extract_criticism_and_praise(self, store, consolidator):
        """Detect Daniel's criticism and praise patterns."""
        messages = [
            "Daniel mi napísal: super, to funguje perfektne!",
            "Daniel mi napísal: nefunguje to, prečo je tam chyba?",
            "Daniel mi napísal: výborne, presne toto som chcel",
            "Daniel mi napísal: zle, neodpovedá server",
        ]
        for msg in messages:
            await store.store(MemoryEntry(
                content=msg,
                memory_type=MemoryType.EPISODIC,
                tags=["telegram", "user_input"],
                source="telegram", importance=0.5,
            ))

        patterns = await consolidator.extract_user_patterns()

        assert len(patterns) >= 1
        balance = patterns[0]
        assert balance["type"] == "interaction_balance"
        assert int(balance["criticism"]) >= 2
        assert int(balance["praise"]) >= 2
