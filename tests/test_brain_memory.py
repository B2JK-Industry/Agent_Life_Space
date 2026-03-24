"""
Test scenarios for Brain + Memory integration.

The CRITICAL test: does John's brain actually USE his memory?
Does memory inform decisions? Does learning change behavior?

Practical scenarios:
1. Decision engine scores tasks — memory context could inform priority
2. Learning system + memory: skill results stored as episodic memory
3. Consolidation turns episodic skill records into procedural knowledge
4. Brain can query what it knows via learning system
5. Task scoring is deterministic regardless of memory state
6. Error decisions use correct algorithmic path
7. Finance pre-check blocks high amounts/risk regardless of memory
8. End-to-end: learn skill → consolidate → check knowledge → use skill
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from agent.brain.decision_engine import (
    DecisionCategory,
    DecisionEngine,
    DecisionMethod,
)
from agent.brain.learning import LearningSystem
from agent.brain.skills import MASTERY_THRESHOLD, SkillStatus
from agent.memory.consolidation import MemoryConsolidation
from agent.memory.store import MemoryEntry, MemoryStore, MemoryType


@pytest.fixture
async def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    store = MemoryStore(db_path=db_path)
    await store.initialize()
    yield store
    await store.close()
    os.unlink(db_path)


@pytest.fixture
def brain():
    return DecisionEngine()


@pytest.fixture
def learning_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_path = os.path.join(tmpdir, "skills.json")
        knowledge_dir = os.path.join(tmpdir, "knowledge")
        os.makedirs(knowledge_dir, exist_ok=True)
        yield LearningSystem(skills_path=skills_path, knowledge_dir=knowledge_dir)


class TestBrainDecisionsAreDeterministic:
    """Brain decisions must be deterministic — same input = same output."""

    def test_task_scoring_deterministic(self, brain):
        """Same task params always produce same score."""
        score1 = brain.score_task("task-1", importance=0.8, urgency=0.6, effort=0.3)
        score2 = brain.score_task("task-1", importance=0.8, urgency=0.6, effort=0.3)
        assert score1.combined_score == score2.combined_score
        assert score1.priority_score == score2.priority_score

    def test_classification_deterministic(self, brain):
        """Category classification is a lookup — always same result."""
        for _ in range(10):
            method = brain.classify(DecisionCategory.TASK_PRIORITY)
            assert method == DecisionMethod.ALGORITHM

    def test_llm_routing_deterministic(self, brain):
        """LLM routing heuristic returns same result for same input."""
        d1 = brain.should_use_llm("sort the tasks by priority")
        d2 = brain.should_use_llm("sort the tasks by priority")
        assert d1.action == d2.action
        assert d1.confidence == d2.confidence

    def test_caching_returns_same_decision(self, brain):
        """Second call hits cache but returns equivalent decision."""
        d1 = brain.should_use_llm("generate a summary report")
        d2 = brain.should_use_llm("generate a summary report")
        assert d2.method == DecisionMethod.CACHED
        assert d1.action == d2.action


class TestBrainRoutesCorrectly:
    """Brain routes different task types to correct methods."""

    def test_algo_task_uses_algorithm(self, brain):
        """Sort/filter/calculate tasks should use algorithm."""
        decision = brain.should_use_llm("sort and filter the task list")
        assert decision.action == "use_algorithm"

    def test_creative_task_uses_llm(self, brain):
        """Generate/write/summarize tasks should use LLM."""
        decision = brain.should_use_llm("write a summary of today's events")
        assert decision.action == "use_llm"

    def test_ambiguous_task_low_confidence(self, brain):
        """Ambiguous task gets low confidence."""
        decision = brain.should_use_llm("process the data")
        assert decision.confidence <= 0.5

    def test_all_categories_have_methods(self, brain):
        """Every decision category must have a mapped method."""
        for category in DecisionCategory:
            method = brain.classify(category)
            assert method in DecisionMethod


class TestBrainFinanceSafety:
    """Finance pre-checks enforce safety regardless of context."""

    def test_high_amount_requires_review(self, brain):
        """Over $100 always requires detailed review."""
        decision = brain.evaluate_finance_proposal(150.0, "low")
        assert decision.action == "require_detailed_review"
        assert decision.requires_approval is True

    def test_high_risk_requires_review(self, brain):
        """High/critical risk always requires detailed review."""
        decision = brain.evaluate_finance_proposal(10.0, "critical")
        assert decision.action == "require_detailed_review"

    def test_low_amount_low_risk_passes(self, brain):
        """Small, low-risk proposal goes to human approval."""
        decision = brain.evaluate_finance_proposal(5.0, "low")
        assert decision.action == "propose_to_human"
        assert decision.requires_approval is True  # Still needs approval


class TestBrainPrioritization:
    """Task prioritization produces correct ordering."""

    def test_high_importance_beats_low(self, brain):
        """Higher importance = higher combined score."""
        high = brain.score_task("t1", importance=0.9, urgency=0.5)
        low = brain.score_task("t2", importance=0.1, urgency=0.5)
        assert high.combined_score > low.combined_score

    def test_urgent_task_rises(self, brain):
        """Urgent task with low importance still gets reasonable score."""
        urgent = brain.score_task("t1", importance=0.2, urgency=1.0)
        lazy = brain.score_task("t2", importance=0.2, urgency=0.1)
        assert urgent.combined_score > lazy.combined_score

    def test_deadline_boosts_urgency(self, brain):
        """Task with near deadline gets urgency boost."""
        with_deadline = brain.score_task(
            "t1", importance=0.5, urgency=0.3,
            has_deadline=True, deadline_hours=2,
        )
        without = brain.score_task("t2", importance=0.5, urgency=0.3)
        assert with_deadline.combined_score > without.combined_score

    def test_blocked_task_penalized(self, brain):
        """Task with unmet dependencies scores lower."""
        ready = brain.score_task("t1", importance=0.5, dependencies_met=True)
        blocked = brain.score_task("t2", importance=0.5, dependencies_met=False)
        assert ready.combined_score > blocked.combined_score

    def test_prioritize_tasks_sorted(self, brain):
        """prioritize_tasks returns correctly sorted list."""
        tasks = [
            {"task_id": "low", "importance": 0.1, "urgency": 0.1},
            {"task_id": "high", "importance": 0.9, "urgency": 0.9},
            {"task_id": "mid", "importance": 0.5, "urgency": 0.5},
        ]
        sorted_tasks = brain.prioritize_tasks(tasks)
        assert sorted_tasks[0].task_id == "high"
        assert sorted_tasks[-1].task_id == "low"


class TestLearningInformsDecisions:
    """Learning system provides context for decisions."""

    def test_mastered_skill_no_testing(self, learning_env):
        """Mastered skill allows confident execution."""
        for _ in range(MASTERY_THRESHOLD):
            learning_env.i_did_it("curl", success=True)

        can = learning_env.can_i_do("curl")
        assert can["answer"] == "yes"
        assert can["should_test"] is False

    def test_failed_skill_suggests_caution(self, learning_env):
        """Failed skill warns about past failure."""
        learning_env.i_did_it("docker_run", success=False, error="Permission denied")

        can = learning_env.can_i_do("docker_run")
        assert can["answer"] == "failed_before"
        assert can["should_test"] is True
        assert "Permission denied" in can["last_error"]

    def test_unknown_skill_checks_knowledge(self, learning_env):
        """Unknown skill searches knowledge base for hints."""
        # Store knowledge using the EXACT skill name so search finds it
        learning_env.knowledge.store(
            category="systems",
            name="kubernetes_deploy",
            content="kubectl apply -f deployment.yaml to deploy pods",
            tags=["kubernetes", "deploy"],
        )

        can = learning_env.can_i_do("kubernetes_deploy")
        assert can["answer"] == "unknown"
        assert can["knowledge_found"] is True
        assert len(can["knowledge_hints"]) > 0


class TestEndToEndBrainFlow:
    """Full flow: learn → remember → consolidate → use knowledge."""

    @pytest.mark.asyncio
    async def test_skill_success_to_memory_to_consolidation(self, store, learning_env):
        """
        1. John uses curl successfully → skill updated
        2. Record stored as episodic memory
        3. Consolidation promotes to procedural
        """
        # Step 1: Skill success
        learning_env.i_did_it(
            "curl", success=True,
            what_i_learned="curl -s -H 'Auth: token' https://api.github.com funguje",
        )

        # Step 2: Store episodic memory (simulating what telegram_handler does)
        await store.store(MemoryEntry(
            content="Úspešne som použil curl na GitHub API. Funguje s tokenom.",
            memory_type=MemoryType.EPISODIC,
            tags=["skill", "curl", "success", "github"],
            source="john",
            importance=0.7,
        ))

        # Step 3: Consolidation should promote to procedural
        consolidator = MemoryConsolidation(store)
        report = await consolidator.consolidate()

        assert report["patterns_found"] >= 1
        procedural = await store.query(memory_type=MemoryType.PROCEDURAL, limit=10)
        assert len(procedural) >= 1

        # And the skill is now LEARNED
        skill_check = learning_env.can_i_do("curl")
        assert skill_check["answer"] in ("probably", "yes")

    @pytest.mark.asyncio
    async def test_error_memory_informs_future(self, store, learning_env):
        """
        1. Task fails → error stored in memory + skill
        2. Next time, skill status warns
        3. Brain can check before attempting
        """
        # Step 1: Failure
        learning_env.i_did_it(
            "docker_run", success=False,
            error="Permission denied: /var/run/docker.sock",
        )
        await store.store(MemoryEntry(
            content="Docker chyba: Permission denied na /var/run/docker.sock",
            memory_type=MemoryType.EPISODIC,
            tags=["error", "docker", "permission"],
            source="john",
            importance=0.8,
        ))

        # Step 2: Check skill — knows it failed
        can = learning_env.can_i_do("docker_run")
        assert can["answer"] == "failed_before"

        # Step 3: Memory has the error
        errors = await store.query(tags=["error"], limit=5)
        assert len(errors) >= 1
        assert "docker" in errors[0].content.lower()

    @pytest.mark.asyncio
    async def test_working_memory_tracks_context(self, store):
        """Working memory keeps current goal in mind."""
        consolidator = MemoryConsolidation(store)

        await consolidator.set_working_context(
            current_goal="Testovanie pamäťového systému",
            active_conversation="Daniel chce vedieť či mozog funguje",
        )

        working = await store.query(memory_type=MemoryType.WORKING, limit=1)
        assert len(working) == 1
        assert "Testovanie" in working[0].content
        assert working[0].importance == 1.0  # Working memory is always priority

    @pytest.mark.asyncio
    async def test_memory_types_serve_different_purposes(self, store):
        """Each memory type has distinct role and is queryable separately."""
        await store.store(MemoryEntry(
            content="Daniel mi napísal: otestuj curl",
            memory_type=MemoryType.EPISODIC,
            tags=["telegram"], source="telegram", importance=0.5,
        ))
        await store.store(MemoryEntry(
            content="Server beží na Ubuntu 24.04 s 8GB RAM",
            memory_type=MemoryType.SEMANTIC,
            tags=["system_fact"], source="consolidation", importance=0.7,
        ))
        await store.store(MemoryEntry(
            content="Deploy postup: git pull → restart → health check",
            memory_type=MemoryType.PROCEDURAL,
            tags=["workflow", "deploy"], source="consolidation", importance=0.8,
        ))

        episodic = await store.query(memory_type=MemoryType.EPISODIC, limit=10)
        semantic = await store.query(memory_type=MemoryType.SEMANTIC, limit=10)
        procedural = await store.query(memory_type=MemoryType.PROCEDURAL, limit=10)

        assert len(episodic) == 1
        assert len(semantic) == 1
        assert len(procedural) == 1

        # Procedural has highest importance (actionable knowledge)
        assert procedural[0].importance >= semantic[0].importance

    @pytest.mark.asyncio
    async def test_brain_decision_with_skill_context(self, brain, learning_env):
        """Brain + Learning: check skill before deciding approach."""
        # John has mastered curl
        for _ in range(MASTERY_THRESHOLD):
            learning_env.i_did_it("curl", success=True)

        # Check if John can do it
        can = learning_env.can_i_do("curl")
        assert can["answer"] == "yes"

        # Brain decides this is an algo task (status check)
        decision = brain.should_use_llm("check the health status of all services")
        assert decision.action == "use_algorithm"

        # Combined: John knows HOW (skill) and WHAT METHOD (brain)
        # This is the integration point — skill confidence + brain routing
        assert can["confidence"] > 0.4
        assert decision.confidence > 0.3
