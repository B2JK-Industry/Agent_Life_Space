"""
Test scenarios for Semantic Router.

Tests that don't require the model (availability check, structure).
Model-dependent tests are skipped if sentence-transformers not installed.
"""

from __future__ import annotations

import pytest

from agent.brain.semantic_router import _INTENTS, is_available


class TestIntentDefinitions:
    """Intent structure is valid."""

    def test_intents_not_empty(self):
        assert len(_INTENTS) > 0

    def test_all_intents_have_phrases(self):
        for intent_name, phrases in _INTENTS.items():
            assert len(phrases) >= 2, f"Intent '{intent_name}' needs at least 2 example phrases"

    def test_required_intents_exist(self):
        required = ["status", "health", "tasks", "skills", "identity", "programming"]
        for name in required:
            assert name in _INTENTS, f"Missing required intent: {name}"


class TestAvailability:
    """Model availability check."""

    def test_is_available_returns_bool(self):
        result = is_available()
        assert isinstance(result, bool)


@pytest.mark.skipif(not is_available(), reason="sentence-transformers not installed")
class TestClassification:
    """Semantic classification (requires model)."""

    def test_status_query(self):
        from agent.brain.semantic_router import classify_intent
        intent, conf = classify_intent("aký je tvoj stav?")
        assert intent == "status"
        assert conf > 0.5

    def test_greeting(self):
        from agent.brain.semantic_router import classify_intent
        intent, conf = classify_intent("ahoj John")
        assert intent == "greeting"
        assert conf > 0.5

    def test_programming(self):
        from agent.brain.semantic_router import classify_intent
        intent, conf = classify_intent("naprogramuj mi novú funkciu")
        assert intent == "programming"
        assert conf > 0.5

    def test_slovak_health_query(self):
        from agent.brain.semantic_router import classify_intent
        intent, conf = classify_intent("ako je na tom server so zdravím?")
        assert intent == "health"
        assert conf > 0.4
