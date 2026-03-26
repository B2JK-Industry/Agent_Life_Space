"""
Test scenarios for Brain Decision Controller.

1. Classification fails fast on unknown categories (no silent fallback)
2. Task scoring matches documented formula
3. Cache works — repeated decisions return CACHED method
4. Input validation rejects bad inputs
5. Finance pre-check returns HYBRID method (not ALGORITHM)
6. LLM routing is acknowledged as heuristic (low confidence on ambiguity)
7. Missing task_id in prioritize_tasks raises
8. Negative amount_usd in finance raises
9. Invalid risk_level raises
"""

from __future__ import annotations

import pytest

from agent.brain.decision_engine import (
    _CATEGORY_METHOD,
    ALGORITHMIC_CATEGORIES,
    HYBRID_CATEGORIES,
    LLM_CATEGORIES,
    DecisionCategory,
    DecisionEngine,
    DecisionMethod,
)


@pytest.fixture
def engine() -> DecisionEngine:
    return DecisionEngine()


class TestCategoryMapping:
    """Every category must be explicitly mapped."""

    def test_all_categories_mapped(self) -> None:
        """No category left unmapped — fails fast instead of silent fallback."""
        for category in DecisionCategory:
            assert category in _CATEGORY_METHOD, (
                f"{category.value} not in _CATEGORY_METHOD"
            )

    def test_classify_known_categories(self, engine: DecisionEngine) -> None:
        assert engine.classify(DecisionCategory.TASK_PRIORITY) == DecisionMethod.ALGORITHM
        assert engine.classify(DecisionCategory.CONTENT_GENERATION) == DecisionMethod.LLM
        assert engine.classify(DecisionCategory.FINANCE) == DecisionMethod.HYBRID

    def test_sets_derived_from_mapping(self) -> None:
        """ALGORITHMIC/LLM/HYBRID sets are derived from _CATEGORY_METHOD, not duplicated."""
        assert DecisionCategory.TASK_PRIORITY in ALGORITHMIC_CATEGORIES
        assert DecisionCategory.CONTENT_GENERATION in LLM_CATEGORIES
        assert DecisionCategory.FINANCE in HYBRID_CATEGORIES


class TestTaskScoring:
    """Scoring must match documented formula."""

    def test_basic_scoring(self, engine: DecisionEngine) -> None:
        score = engine.score_task("t1", importance=0.8, urgency=0.6, effort=0.3)
        # priority_score = 0.8*0.4 + (1-0.3)*0.2 + 1.0*0.1 = 0.32+0.14+0.1 = 0.56
        # urgency_score = 0.6
        # combined = 0.56*0.6 + 0.6*0.4 = 0.336 + 0.24 = 0.576
        assert score.priority_score == 0.56
        assert score.urgency_score == 0.6
        assert score.combined_score == 0.576

    def test_deterministic(self, engine: DecisionEngine) -> None:
        s1 = engine.score_task("t1", 0.8, 0.6, 0.3, True, False)
        s2 = engine.score_task("t1", 0.8, 0.6, 0.3, True, False)
        assert s1.combined_score == s2.combined_score

    def test_higher_importance_wins(self, engine: DecisionEngine) -> None:
        high = engine.score_task("h", importance=0.9, urgency=0.5, effort=0.5)
        low = engine.score_task("l", importance=0.1, urgency=0.5, effort=0.5)
        assert high.combined_score > low.combined_score

    def test_deadline_boosts_urgency(self, engine: DecisionEngine) -> None:
        no_dl = engine.score_task("t1", 0.5, 0.5, 0.5, has_deadline=False)
        dl = engine.score_task("t2", 0.5, 0.5, 0.5, has_deadline=True, deadline_hours=2.0)
        assert dl.urgency_score > no_dl.urgency_score

    def test_critical_deadline(self, engine: DecisionEngine) -> None:
        score = engine.score_task("u", 0.5, 0.1, 0.5, has_deadline=True, deadline_hours=0.5)
        assert score.urgency_score == 1.0

    def test_past_deadline(self, engine: DecisionEngine) -> None:
        """Negative deadline_hours = past deadline = max urgency."""
        score = engine.score_task("u", 0.5, 0.1, 0.5, has_deadline=True, deadline_hours=-2.0)
        assert score.urgency_score == 1.0

    def test_unmet_dependencies_lower_score(self, engine: DecisionEngine) -> None:
        met = engine.score_task("m", 0.5, 0.5, 0.5, dependencies_met=True)
        unmet = engine.score_task("u", 0.5, 0.5, 0.5, dependencies_met=False)
        assert met.combined_score > unmet.combined_score

    def test_prioritize_multiple_tasks(self, engine: DecisionEngine) -> None:
        tasks = [
            {"task_id": "low", "importance": 0.2, "urgency": 0.2, "effort": 0.8},
            {"task_id": "high", "importance": 0.9, "urgency": 0.9, "effort": 0.2},
            {"task_id": "mid", "importance": 0.5, "urgency": 0.5, "effort": 0.5},
        ]
        sorted_tasks = engine.prioritize_tasks(tasks)
        assert sorted_tasks[0].task_id == "high"
        assert sorted_tasks[-1].task_id == "low"

    def test_input_clamping(self, engine: DecisionEngine) -> None:
        score = engine.score_task("t1", importance=5.0, urgency=-1.0, effort=2.0)
        assert score.factors["importance"] == 1.0
        assert score.factors["urgency"] == 0.0
        assert score.factors["effort"] == 1.0


class TestInputValidation:
    """Bad inputs must fail fast, not silently produce garbage."""

    def test_empty_task_id_raises(self, engine: DecisionEngine) -> None:
        with pytest.raises(ValueError, match="task_id"):
            engine.score_task("")

    def test_missing_task_id_in_list_raises(self, engine: DecisionEngine) -> None:
        with pytest.raises(ValueError, match="missing 'task_id'"):
            engine.prioritize_tasks([{"importance": 0.5}])

    def test_empty_error_type_raises(self, engine: DecisionEngine) -> None:
        with pytest.raises(ValueError, match="error_type"):
            engine.decide_error_action("", 0, 3)

    def test_negative_retry_count_raises(self, engine: DecisionEngine) -> None:
        with pytest.raises(ValueError, match="negative"):
            engine.decide_error_action("timeout", -1, 3)

    def test_empty_task_description_raises(self, engine: DecisionEngine) -> None:
        with pytest.raises(ValueError, match="empty"):
            engine.should_use_llm("")

    def test_whitespace_task_description_raises(self, engine: DecisionEngine) -> None:
        with pytest.raises(ValueError, match="empty"):
            engine.should_use_llm("   ")

    def test_negative_finance_amount_raises(self, engine: DecisionEngine) -> None:
        with pytest.raises(ValueError, match="negative"):
            engine.evaluate_finance_proposal(-10.0, "low")

    def test_invalid_risk_level_raises(self, engine: DecisionEngine) -> None:
        with pytest.raises(ValueError, match="Invalid risk_level"):
            engine.evaluate_finance_proposal(10.0, "maybe_risky")


class TestCache:
    """Cache stores and returns repeated decisions."""

    def test_second_call_returns_cached(self, engine: DecisionEngine) -> None:
        d1 = engine.should_use_llm("Write a blog post")
        d2 = engine.should_use_llm("Write a blog post")
        assert d2.method == DecisionMethod.CACHED
        assert d2.action == d1.action

    def test_different_inputs_not_cached(self, engine: DecisionEngine) -> None:
        engine.should_use_llm("Write a blog post")
        d2 = engine.should_use_llm("Sort items by priority")
        assert d2.method != DecisionMethod.CACHED

    def test_cache_stats_tracked(self, engine: DecisionEngine) -> None:
        engine.should_use_llm("Write something")
        engine.should_use_llm("Write something")
        stats = engine.get_stats()
        assert stats["cache_hits"] == 1
        assert stats["cache_size"] >= 1

    def test_cache_eviction(self) -> None:
        engine = DecisionEngine(cache_max_size=2)
        engine.should_use_llm("Write a post")
        engine.should_use_llm("Sort items")
        engine.should_use_llm("Generate report")  # Evicts oldest
        assert engine.get_stats()["cache_size"] == 2


class TestLLMRouting:
    """LLM routing is a keyword heuristic — acknowledged as imperfect."""

    def test_sorting_uses_algorithm(self, engine: DecisionEngine) -> None:
        d = engine.should_use_llm("Sort these items by priority")
        assert d.action == "use_algorithm"

    def test_content_uses_llm(self, engine: DecisionEngine) -> None:
        d = engine.should_use_llm("Write a blog post about AI")
        assert d.action == "use_llm"

    def test_scheduling_uses_algorithm(self, engine: DecisionEngine) -> None:
        d = engine.should_use_llm("Schedule the next health check")
        assert d.action == "use_algorithm"

    def test_analysis_uses_llm(self, engine: DecisionEngine) -> None:
        d = engine.should_use_llm("Analyze text and summarize findings")
        assert d.action == "use_llm"

    def test_unknown_has_low_confidence(self, engine: DecisionEngine) -> None:
        """Unrecognized tasks should signal uncertainty via low confidence."""
        d = engine.should_use_llm("Do the thing with the stuff")
        assert d.action == "use_algorithm"
        assert d.confidence <= 0.4  # Low — we don't know

    def test_routing_decision_is_always_algorithmic(self, engine: DecisionEngine) -> None:
        """Even when deciding to USE LLM, the DECISION itself is algorithmic."""
        d = engine.should_use_llm("Generate creative content")
        assert d.method == DecisionMethod.ALGORITHM

    def test_response_includes_matched_keywords(self, engine: DecisionEngine) -> None:
        d = engine.should_use_llm("Sort and filter data")
        assert "algo_matches" in d.data
        assert "sort" in d.data["algo_matches"]
        assert "filter" in d.data["algo_matches"]


class TestErrorHandling:
    def test_retry_when_under_limit(self, engine: DecisionEngine) -> None:
        d = engine.decide_error_action("timeout", retry_count=1, max_retries=3)
        assert d.action == "retry"
        assert d.confidence == 1.0

    def test_dead_letter_on_transient_exhausted(self, engine: DecisionEngine) -> None:
        d = engine.decide_error_action("timeout", retry_count=3, max_retries=3)
        assert d.action == "dead_letter"

    def test_alert_on_non_transient(self, engine: DecisionEngine) -> None:
        d = engine.decide_error_action("permission_denied", retry_count=3, max_retries=3)
        assert d.action == "dead_letter_with_alert"


class TestFinanceSafety:
    """Finance pre-check: always approval, method is HYBRID."""

    def test_small_amount_requires_approval(self, engine: DecisionEngine) -> None:
        d = engine.evaluate_finance_proposal(5.0, "low")
        assert d.requires_approval is True

    def test_method_is_hybrid(self, engine: DecisionEngine) -> None:
        """Finance flow is hybrid — this is the algorithmic stage of it."""
        d = engine.evaluate_finance_proposal(5.0, "low")
        assert d.method == DecisionMethod.HYBRID

    def test_large_amount_requires_review(self, engine: DecisionEngine) -> None:
        d = engine.evaluate_finance_proposal(500.0, "low")
        assert d.action == "require_detailed_review"
        assert d.requires_approval is True

    def test_high_risk_requires_review(self, engine: DecisionEngine) -> None:
        d = engine.evaluate_finance_proposal(10.0, "high")
        assert d.action == "require_detailed_review"

    def test_zero_amount_still_requires_approval(self, engine: DecisionEngine) -> None:
        d = engine.evaluate_finance_proposal(0.0, "low")
        assert d.requires_approval is True
