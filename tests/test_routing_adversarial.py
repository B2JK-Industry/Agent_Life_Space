"""
Adversarial and boundary tests for task classification.

These test cases cover edge cases, tricky phrasings, and inputs
designed to confuse the classifier. They serve as a regression suite
and a quality benchmark for routing.
"""

from __future__ import annotations

import pytest

from agent.core.models import classify_task, classify_task_detailed


class TestAdversarialPhrasing:
    """Inputs designed to confuse the classifier."""

    @pytest.mark.parametrize("text,should_not_be", [
        # Programming keywords in non-programming context
        ("fix mi kávu", "programming"),
        # "napíš" in conversational context
        ("napíš mi niečo milé", "programming"),
        # Short with action verb
        ("spusti", "programming"),
    ])
    def test_false_positive_prevention(self, text, should_not_be):
        result = classify_task(text)
        assert result != should_not_be, (
            f"'{text}' should not route to {should_not_be}, got {result}"
        )

    @pytest.mark.parametrize("text,expected", [
        # Mixed signals: programming keyword + simple greeting
        ("ahoj, oprav bug", "programming"),
        # URL alone should bump complexity
        ("pozri https://github.com/B2JK-Industry/Agent_Life_Space", "analysis"),
    ])
    def test_mixed_signals(self, text, expected):
        assert classify_task(text) == expected


class TestBoundaryInputs:
    """Edge cases and boundary conditions."""

    def test_empty_string(self):
        assert classify_task("") == "chat"

    def test_whitespace_only(self):
        assert classify_task("   ") == "chat"

    def test_single_character(self):
        result = classify_task("?")
        assert result in ("chat", "factual")

    def test_very_long_input(self):
        """Long inputs should not crash or timeout."""
        text = "analyzuj " + "veľmi dlhý text " * 100
        result = classify_task(text)
        assert result in ("analysis", "programming", "chat")

    def test_unicode_input(self):
        result = classify_task("čo robí 🤖?")
        assert result in ("factual", "chat")

    def test_code_injection_in_input(self):
        """Backticks in input should route to programming."""
        result = classify_task("```python\nprint('hello')\n```")
        assert result == "programming"

    def test_multiline_input(self):
        text = "1. oprav bug\n2. pridaj testy\n3. commitni"
        result = classify_task(text)
        assert result == "programming"


class TestClassificationConsistency:
    """Same input should always produce same output (determinism)."""

    def test_deterministic(self):
        text = "analyzuj tento kód a povedz čo robí"
        results = [classify_task(text) for _ in range(10)]
        assert len(set(results)) == 1, "Classification must be deterministic"

    def test_case_sensitivity(self):
        """Classification should be case-insensitive."""
        lower = classify_task("naprogramuj funkciu")
        upper = classify_task("NAPROGRAMUJ FUNKCIU")
        mixed = classify_task("Naprogramuj Funkciu")
        assert lower == upper == mixed


class TestExplainabilityQuality:
    """Signal breakdown should be meaningful and non-empty for non-trivial inputs."""

    def test_programming_has_keyword_signal(self):
        result = classify_task_detailed("implementuj REST endpoint")
        assert result.score > 0
        assert len(result.signals) > 0

    def test_analysis_has_action_signal(self):
        # "analyzuj" is now an analytical verb (not action verb) — it should
        # classify as chat, not programming. A real action verb is needed:
        result = classify_task_detailed("nastav server na produkciu")
        assert result.score > 0

    def test_simple_has_zero_score(self):
        result = classify_task_detailed("ahoj")
        assert result.score == 0

    def test_chat_fallback_has_empty_signals(self):
        result = classify_task_detailed("zaujímavé")
        assert result.task_type == "chat"
        assert result.score == 0
        assert len(result.signals) == 0


class TestRoutingAccuracy:
    """
    Precision/recall benchmark for routing.

    These are ground-truth labels. If accuracy drops below threshold,
    the classifier needs attention.
    """

    GROUND_TRUTH = [
        # (input, expected_type)
        # Simple
        ("ahoj", "simple"),
        ("ok", "simple"),
        ("ďakujem", "simple"),
        ("super", "simple"),
        # Programming
        ("naprogramuj parser pre JSON", "programming"),
        ("oprav bug v memory store", "programming"),
        ("pridaj nový endpoint do API", "programming"),
        ("napíš test pre tool executor", "programming"),
        ("refaktoruj workspace manager", "programming"),
        ("debug prečo padá brain.py", "programming"),
        # Analysis
        ("porovnaj Sonnet a Opus na reasoning úlohách", "analysis"),
        ("analyzuj logy za posledný týždeň", "analysis"),
        ("nájdi všetky TODO v kóde", "analysis"),
        # Factual
        ("koľko je hodín?", "factual"),
        ("aký je dnes deň?", "factual"),
        # Chat
        ("čo si myslíš o budúcnosti AI?", "factual"),  # short question with ?
        ("povedz mi vtip", "chat"),
    ]

    def test_accuracy_above_threshold(self):
        """Overall accuracy must be ≥80%."""
        correct = 0
        total = len(self.GROUND_TRUTH)
        failures = []

        for text, expected in self.GROUND_TRUTH:
            actual = classify_task(text)
            if actual == expected:
                correct += 1
            else:
                failures.append(f"  '{text}': expected={expected}, got={actual}")

        accuracy = correct / total
        assert accuracy >= 0.80, (
            f"Routing accuracy {accuracy:.0%} ({correct}/{total}) is below 80% threshold.\n"
            f"Failures:\n" + "\n".join(failures)
        )

    def test_no_programming_false_negatives(self):
        """Programming tasks must not be misrouted to simple/factual."""
        programming_inputs = [t for t, e in self.GROUND_TRUTH if e == "programming"]
        for text in programming_inputs:
            result = classify_task(text)
            assert result != "simple", f"Programming task '{text}' misrouted to simple"
            assert result != "factual", f"Programming task '{text}' misrouted to factual"
