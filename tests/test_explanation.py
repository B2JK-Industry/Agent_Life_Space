"""
Tests for explanation layer — "why did I do this?"
"""

from __future__ import annotations

from agent.core.explanation import DecisionExplanation, ExplanationLog


class TestDecisionExplanation:
    """Explanations capture decision context."""

    def test_basic_explanation(self):
        exp = DecisionExplanation(
            action_type="message_response",
            action_summary="Odpovedal na 'ahoj'",
            routing_task_type="simple",
            routing_score=0,
            model_used="claude-haiku-4-5-20251001",
        )
        text = exp.explain()
        assert "Odpovedal na 'ahoj'" in text
        assert "simple" in text

    def test_to_dict(self):
        exp = DecisionExplanation(
            action_type="tool_call",
            action_summary="Spustil run_code",
            routing_task_type="programming",
            routing_score=5,
            routing_signals={"programming_keywords": 5},
        )
        d = exp.to_dict()
        assert d["routing"]["task_type"] == "programming"
        assert d["routing"]["signals"]["programming_keywords"] == 5

    def test_explain_with_policy(self):
        exp = DecisionExplanation(
            action_summary="Blokovaný run_code",
            policy_decisions=[
                {"tool": "run_code", "allowed": False, "reason": "safe mode"}
            ],
        )
        text = exp.explain()
        assert "blokované" in text
        assert "safe mode" in text

    def test_explain_with_learning(self):
        exp = DecisionExplanation(
            action_summary="Eskalovaný model",
            learning_escalation="haiku → sonnet (predchádzajúci fail)",
            past_errors_used=["error 1", "error 2"],
        )
        text = exp.explain()
        assert "Eskalácia" in text
        assert "2 relevantných" in text

    def test_explain_with_memory(self):
        exp = DecisionExplanation(
            action_summary="Použil pamäť",
            memories_recalled=5,
            provenance_breakdown={"verified": 3, "observed": 2},
        )
        text = exp.explain()
        assert "5 recalled" in text


class TestExplanationLog:
    """Log stores and retrieves explanations."""

    def test_record_and_retrieve(self):
        log = ExplanationLog()
        exp = DecisionExplanation(action_summary="test")
        log.record(exp)
        assert log.total == 1

    def test_get_last_explanation(self):
        log = ExplanationLog()
        log.record(DecisionExplanation(action_summary="first"))
        log.record(DecisionExplanation(action_summary="second"))
        text = log.get_last_explanation()
        assert "second" in text

    def test_empty_log_returns_none(self):
        log = ExplanationLog()
        assert log.get_last_explanation() is None

    def test_find_by_action_id(self):
        log = ExplanationLog()
        exp = DecisionExplanation(action_id="abc123", action_summary="found me")
        log.record(exp)
        found = log.find_by_action_id("abc123")
        assert found is not None
        assert found.action_summary == "found me"

    def test_find_nonexistent(self):
        log = ExplanationLog()
        assert log.find_by_action_id("nope") is None

    def test_ring_buffer(self):
        log = ExplanationLog(max_entries=3)
        for i in range(5):
            log.record(DecisionExplanation(action_summary=f"entry {i}"))
        assert log.total == 3

    def test_get_recent(self):
        log = ExplanationLog()
        for i in range(5):
            log.record(DecisionExplanation(action_summary=f"entry {i}"))
        recent = log.get_recent(2)
        assert len(recent) == 2
        assert recent[-1]["action_summary"] == "entry 4"
