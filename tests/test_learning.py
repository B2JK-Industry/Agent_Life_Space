"""
Test scenarios for Learning System (Skills + Knowledge + Memory integration).

Practical scenarios:
1. John checks if he can do a skill — returns correct advice
2. Skill lifecycle: UNKNOWN → LEARNED → MASTERED after repeated success
3. Skill failure records error and changes status
4. Knowledge base stores and retrieves learned content
5. Learning system connects skills + knowledge — i_did_it updates both
6. learn_new_skill registers skill AND saves knowledge
7. find_relevant searches across skills AND knowledge
8. what_do_i_know returns complete summary
9. Skill confidence grows with successful usage
10. Failed skill can recover after success
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent.brain.knowledge import KnowledgeBase
from agent.brain.learning import LearningSystem
from agent.brain.skills import Skill, SkillRegistry, SkillStatus, MASTERY_THRESHOLD


@pytest.fixture
def learning_dir():
    """Create temporary directory for skills and knowledge."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_path = os.path.join(tmpdir, "skills.json")
        knowledge_dir = os.path.join(tmpdir, "knowledge")
        os.makedirs(knowledge_dir, exist_ok=True)
        yield tmpdir, skills_path, knowledge_dir


@pytest.fixture
def learning(learning_dir):
    tmpdir, skills_path, knowledge_dir = learning_dir
    return LearningSystem(skills_path=skills_path, knowledge_dir=knowledge_dir)


class TestSkillLifecycle:
    """Skill status transitions from UNKNOWN to MASTERED."""

    def test_unknown_skill_check(self, learning):
        """Unknown skill returns 'not_yet' with test suggestion."""
        result = learning.can_i_do("curl")
        assert result["answer"] == "not_yet"
        assert result["should_test"] is True

    def test_nonexistent_skill_check(self, learning):
        """Skill not in registry returns 'unknown'."""
        result = learning.can_i_do("quantum_computing")
        assert result["answer"] == "unknown"
        assert result["skill_exists"] is False
        assert result["should_test"] is True

    def test_skill_becomes_learned_after_success(self, learning):
        """One success moves UNKNOWN → LEARNED."""
        learning.i_did_it("curl", success=True)

        result = learning.can_i_do("curl")
        assert result["answer"] == "probably"
        assert result["skill_exists"] is True
        assert result["confidence"] > 0

    def test_skill_becomes_mastered(self, learning):
        """After MASTERY_THRESHOLD successes, skill is MASTERED."""
        for _ in range(MASTERY_THRESHOLD):
            learning.i_did_it("curl", success=True)

        result = learning.can_i_do("curl")
        assert result["answer"] == "yes"
        assert result["confidence"] > 0.4

    def test_skill_failure_records_error(self, learning):
        """Failed skill records the error."""
        learning.i_did_it("docker_run", success=False, error="Permission denied")

        result = learning.can_i_do("docker_run")
        assert result["answer"] == "failed_before"
        assert "Permission denied" in result["last_error"]

    def test_failed_skill_recovers(self, learning):
        """A failed skill can recover with subsequent success."""
        learning.i_did_it("docker_run", success=False, error="Permission denied")
        learning.i_did_it("docker_run", success=True)

        result = learning.can_i_do("docker_run")
        assert result["answer"] == "probably"
        assert result["confidence"] > 0


class TestSkillConfidence:
    """Confidence scoring is deterministic and grows with usage."""

    def test_zero_confidence_for_unused(self):
        """Unused skill has zero confidence."""
        skill = Skill("test_skill")
        assert skill.confidence == 0.0

    def test_confidence_grows_with_success(self):
        """Each success increases confidence."""
        skill = Skill("test_skill")
        prev = 0.0
        for _ in range(5):
            skill.record_success()
            assert skill.confidence >= prev
            prev = skill.confidence

    def test_failure_reduces_confidence(self):
        """Mixing failures reduces confidence vs pure success."""
        pure_success = Skill("skill_a")
        mixed = Skill("skill_b")

        # Need enough volume that the ratio difference matters
        for _ in range(10):
            pure_success.record_success()
            mixed.record_success()
        mixed.record_failure("error")
        mixed.record_failure("error2")

        assert pure_success.confidence > mixed.confidence

    def test_needs_testing_for_early_learned(self):
        """LEARNED skill with < 3 successes still needs testing."""
        skill = Skill("new_skill")
        skill.record_success()
        skill.record_success()
        assert skill.needs_testing is True

        skill.record_success()
        assert skill.needs_testing is False


class TestKnowledgeBase:
    """Knowledge base stores and retrieves learning."""

    def test_store_and_retrieve(self, learning):
        """Store knowledge and get it back."""
        learning.knowledge.store(
            category="skills",
            name="curl_usage",
            content="curl -s -H 'Authorization: token ...' https://api.github.com/...",
            tags=["curl", "github", "api"],
        )

        result = learning.knowledge.get("skills", "curl_usage")
        assert result is not None
        assert "curl -s" in result

    def test_search_finds_relevant(self, learning):
        """Search across knowledge base finds matches."""
        learning.knowledge.store(
            category="systems",
            name="github_api",
            content="GitHub API requires token, rate limit 5000/hour",
            tags=["github"],
        )

        results = learning.knowledge.search("github")
        assert len(results) >= 1
        assert any("github" in r["name"].lower() for r in results)

    def test_search_empty_returns_nothing(self, learning):
        """Searching for nonexistent topic returns empty."""
        results = learning.knowledge.search("blockchain_quantum_ai")
        assert len(results) == 0

    def test_list_categories(self, learning):
        """All expected categories exist."""
        all_items = learning.knowledge.list_all()
        assert "skills" in all_items
        assert "systems" in all_items
        assert "learned" in all_items


class TestLearningIntegration:
    """Skills + Knowledge working together through LearningSystem."""

    def test_i_did_it_saves_knowledge(self, learning):
        """Success with what_i_learned saves to both skills AND knowledge."""
        result = learning.i_did_it(
            "git_commit",
            success=True,
            what_i_learned="Git push vyžaduje GITHUB_TOKEN v environment",
        )

        assert result["action"] == "success"
        assert result["knowledge_saved"] is True

        # Knowledge is actually in the KB
        kb = learning.knowledge.search("git_commit")
        assert len(kb) >= 1

    def test_learn_new_skill(self, learning):
        """learn_new_skill registers AND saves knowledge."""
        result = learning.learn_new_skill(
            name="docker_build",
            description="Buildovanie Docker images",
            category="docker",
            command_example="docker build -t myimage .",
            knowledge_content="Dockerfile musí byť v root projektu. Multi-stage build šetrí veľkosť.",
        )

        assert result["registered"] is True
        assert result["knowledge_saved"] is True

        # Skill exists
        can = learning.can_i_do("docker_build")
        assert can["skill_exists"] is True

        # Knowledge exists
        kb = learning.knowledge.search("docker_build")
        assert len(kb) >= 1

    def test_find_relevant_searches_both(self, learning):
        """find_relevant returns matches from skills AND knowledge."""
        # Add knowledge about git
        learning.knowledge.store(
            category="systems",
            name="git_workflow",
            content="Git workflow: branch → commit → push → PR",
            tags=["git"],
        )

        result = learning.find_relevant("git")
        assert result["total_found"] >= 1
        # Should find default git skills + our knowledge entry
        assert len(result["matching_skills"]) >= 1 or len(result["knowledge_entries"]) >= 1

    def test_what_do_i_know_summary(self, learning):
        """what_do_i_know returns complete picture."""
        summary = learning.what_do_i_know()
        assert "skills" in summary
        assert "knowledge" in summary
        assert summary["skills"]["total"] > 0  # Default skills exist

    def test_mastered_skill_no_testing_needed(self, learning):
        """Mastered skill returns confident answer without testing."""
        for _ in range(MASTERY_THRESHOLD):
            learning.i_did_it("curl", success=True)

        result = learning.can_i_do("curl")
        assert result["answer"] == "yes"
        assert result["should_test"] is False
        assert result["confidence"] > 0.4


class TestAutoSkillTesting:
    """Event-driven skill testing — test when needed, not on cron."""

    def test_try_skill_with_known_command(self, learning):
        """try_skill runs test and records result."""
        result = learning.try_skill("python_run")
        assert result["tested"] is True
        assert result["success"] is True
        assert "hello from john" in result["output"]

        # Skill should now be LEARNED
        skill = learning.skills.get("python_run")
        assert skill.success_count >= 1

    def test_try_skill_no_test_command(self, learning):
        """Skill without test command returns tested=False."""
        learning.learn_new_skill(
            name="quantum_computing",
            description="Quantum stuff",
        )
        result = learning.try_skill("quantum_computing")
        assert result["tested"] is False

    def test_can_i_do_auto_test(self, learning):
        """can_i_do with auto_test=True tests unknown skill on the spot."""
        # python_run is UNKNOWN initially
        assert learning.skills.get("python_run").needs_testing is True

        result = learning.can_i_do("python_run", auto_test=True)

        # After auto-test, should be LEARNED (not "not_yet")
        assert result["answer"] == "probably"
        assert result["confidence"] > 0

    def test_mastered_skill_skips_auto_test(self, learning):
        """Mastered skill doesn't waste time re-testing."""
        for _ in range(MASTERY_THRESHOLD):
            learning.i_did_it("curl", success=True)

        result = learning.can_i_do("curl", auto_test=True)
        assert result["answer"] == "yes"
        assert result["should_test"] is False
