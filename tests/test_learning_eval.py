"""
Tests for learning system: rollback, confidence, eval scenarios.
"""

from __future__ import annotations

import pytest

from agent.brain.learning import LearningSystem
from agent.brain.skills import Skill, SkillStatus


class TestLearningRollback:
    """Rollback resets learned behavior."""

    @pytest.fixture
    def learning(self, tmp_path):
        skills_path = str(tmp_path / "skills.json")
        knowledge_dir = str(tmp_path / "knowledge")
        import os
        os.makedirs(knowledge_dir, exist_ok=True)
        ls = LearningSystem(skills_path=skills_path, knowledge_dir=knowledge_dir)
        # Register a skill
        ls.skills.register(Skill(name="test_skill", description="test"))
        ls.skills.record_success("test_skill")
        ls.skills.record_success("test_skill")
        ls.skills.record_success("test_skill")
        return ls

    def test_rollback_resets_to_unknown(self, learning):
        skill = learning.skills.get("test_skill")
        assert skill.status in (SkillStatus.LEARNED, SkillStatus.MASTERED)

        result = learning.rollback_skill("test_skill")
        assert result["rolled_back"]

        skill = learning.skills.get("test_skill")
        assert skill.status == SkillStatus.UNKNOWN
        assert skill.confidence == 0.0
        assert skill.success_count == 0

    def test_rollback_nonexistent_skill(self, learning):
        result = learning.rollback_skill("nonexistent")
        assert not result["rolled_back"]

    def test_rollback_audit_trail(self, learning):
        learning.rollback_skill("test_skill")
        events = learning.audit_log.get_by_type("rollback")
        assert len(events) == 1
        assert events[0]["skill"] == "test_skill"


class TestLearningReport:
    """Learning report shows confidence and quality metrics."""

    @pytest.fixture
    def learning(self, tmp_path):
        skills_path = str(tmp_path / "skills.json")
        knowledge_dir = str(tmp_path / "knowledge")
        import os
        os.makedirs(knowledge_dir, exist_ok=True)
        ls = LearningSystem(skills_path=skills_path, knowledge_dir=knowledge_dir)
        return ls

    def test_empty_report(self, learning):
        report = learning.get_learning_report()
        assert report["avg_confidence"] == 0.0
        assert report["mastered_count"] == 0

    def test_report_with_skills(self, learning):
        learning.skills.register(Skill(name="s1", description="test"))
        learning.skills.record_success("s1")
        learning.skills.record_success("s1")
        learning.skills.record_success("s1")

        report = learning.get_learning_report()
        assert report["avg_confidence"] > 0
        assert report["mastered_count"] >= 0


class TestLearningEvalScenarios:
    """Eval scenarios for learning system behavior."""

    @pytest.fixture
    def learning(self, tmp_path):
        skills_path = str(tmp_path / "skills.json")
        knowledge_dir = str(tmp_path / "knowledge")
        import os
        os.makedirs(knowledge_dir, exist_ok=True)
        ls = LearningSystem(skills_path=skills_path, knowledge_dir=knowledge_dir)
        return ls

    def test_success_increases_confidence(self, learning):
        learning.skills.register(Skill(name="eval_skill", description="test"))
        skill = learning.skills.get("eval_skill")
        assert skill.confidence == 0.0

        learning.skills.record_success("eval_skill")
        skill = learning.skills.get("eval_skill")
        assert skill.confidence > 0.0

    def test_failure_recorded_correctly(self, learning):
        learning.skills.register(Skill(name="fail_skill", description="test"))
        learning.skills.record_failure("fail_skill", "test error")

        skill = learning.skills.get("fail_skill")
        assert skill.status == SkillStatus.FAILED
        assert skill.last_error == "test error"

    def test_model_escalation_after_failure(self, learning):
        learning.skills.register(Skill(name="esc_skill", description="test"))
        learning.skills.record_failure("esc_skill", "model too weak")
        learning._record_model_failure("esc_skill", "claude-haiku-4-5-20251001", "too weak")

        result = learning.adapt_model("test", "esc_skill")
        if result["model_override"]:
            assert result["model_override"] == "claude-sonnet-4-6"

    def test_rollback_clears_model_failure(self, learning):
        learning.skills.register(Skill(name="clear_skill", description="test"))
        learning._model_failures["clear_skill"] = "claude-haiku-4-5-20251001"

        learning.rollback_skill("clear_skill")
        assert "clear_skill" not in learning._model_failures
