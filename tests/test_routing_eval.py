"""
Eval-driven tests for task classification / model routing.

Each test case is a (input, expected_type) pair.
This serves as a regression + eval suite for routing quality.
"""

from __future__ import annotations

import pytest

from agent.core.models import classify_task, classify_task_detailed


class TestSimpleRouting:
    """Simple messages route to FAST tier."""

    @pytest.mark.parametrize("text,expected", [
        ("ahoj", "simple"),
        ("čau", "simple"),
        ("ok", "simple"),
        ("ďakujem", "simple"),
        ("hi", "simple"),
    ])
    def test_greetings(self, text, expected):
        assert classify_task(text) == expected


class TestProgrammingRouting:
    """Programming tasks route to POWERFUL tier."""

    @pytest.mark.parametrize("text,expected", [
        ("naprogramuj funkciu na parsovanie CSV", "programming"),
        ("oprav bug v agent/core/brain.py", "programming"),
        ("napíš test pre memory store", "programming"),
        ("refaktoruj tool executor", "programming"),
        ("commitni zmeny a pushni na github", "programming"),
    ])
    def test_programming(self, text, expected):
        assert classify_task(text) == expected


class TestAnalysisRouting:
    """Analysis tasks route to BALANCED tier."""

    @pytest.mark.parametrize("text,expected", [
        ("analyzuj túto chybu v logoch", "analysis"),
        ("porovnaj tieto dva prístupy k memory systému", "analysis"),
        ("nájdi v kóde všetky TODO položky", "analysis"),
    ])
    def test_analysis(self, text, expected):
        assert classify_task(text) == expected


class TestFactualRouting:
    """Short factual questions route to FAST tier."""

    @pytest.mark.parametrize("text,expected", [
        ("koľko je hodín?", "factual"),
        ("aký je dnes deň?", "factual"),
    ])
    def test_factual(self, text, expected):
        assert classify_task(text) == expected


class TestCodeContentDetection:
    """Messages containing code should route to programming."""

    @pytest.mark.parametrize("text", [
        "čo robí tento kód ```def foo(): return 42```",
        "import os\nos.listdir('.')",
        "class MyAgent: pass",
    ])
    def test_code_content_routes_to_programming(self, text):
        assert classify_task(text) == "programming"


class TestExplainability:
    """Classification provides signal breakdown for debugging."""

    def test_detailed_result_has_signals(self):
        result = classify_task_detailed("naprogramuj funkciu na parsovanie CSV")
        assert result.task_type == "programming"
        assert result.score > 0
        assert "programming_keywords" in result.signals

    def test_simple_has_simple_match_signal(self):
        result = classify_task_detailed("ahoj")
        assert result.task_type == "simple"
        assert "simple_match" in result.signals

    def test_chat_fallback_has_zero_score(self):
        result = classify_task_detailed("zaujímavé, povedz mi viac")
        assert result.task_type == "chat"
        assert result.score == 0
