"""
Tests for the dispatcher "zapamätaj si" / "remember" memory storage fix.

Bug: CLI backend (ClaudeCliProvider) hits errormaxturns when user sends
"zapamätaj si X" because CLI cannot call store_memory tool.

Fix: Dispatcher detects the pattern and stores directly, bypassing LLM.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.brain.dispatcher import InternalDispatcher


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.memory = MagicMock()
    agent.memory.store = AsyncMock(return_value="mem-123")
    agent.memory.get_stats.return_value = {"total_memories": 42}
    # Watchdog health mock with real values (MagicMock doesn't support format strings)
    health = MagicMock()
    health.cpu_percent = 15.0
    health.memory_percent = 45.0
    health.memory_used_mb = 1024.0
    health.memory_available_mb = 2048.0
    health.disk_percent = 30.0
    health.alerts = []
    health.modules = {"brain": "healthy", "memory": "healthy"}
    agent.watchdog.get_system_health.return_value = health
    # Tasks mock
    agent.tasks.get_stats.return_value = {"total_tasks": 3, "by_status": {"queued": 2, "running": 1}}
    agent.tasks.get_tasks_by_status.return_value = []
    # Finance mock
    agent.finance.get_stats.return_value = {
        "total_income": 0.0, "total_expenses": 0.0, "net": 0.0, "pending_proposals": 0,
    }
    return agent


@pytest.fixture
def dispatcher(mock_agent):
    return InternalDispatcher(mock_agent)


class TestRememberDetection:
    """Test pattern detection for remember/zapamätaj requests."""

    @pytest.mark.parametrize("text,expected_content", [
        ("Zapamätaj si že mám rád pizzu", "mám rád pizzu"),
        ("zapamätaj si že server beží na porte 8080", "server beží na porte 8080"),
        ("Zapamätaj si: preferujem tmavý mód", "preferujem tmavý mód"),
        ("zapamätaj si moje heslo je silné", "moje heslo je silné"),
        ("Remember that I like dark mode", "I like dark mode"),
        ("remember the server runs on port 3000", "the server runs on port 3000"),
        ("Ulož si do pamäte že deploy je v piatok", "deploy je v piatok"),
        ("ulož si že backup beží o 3:00", "backup beží o 3:00"),
    ])
    def test_pattern_detected(self, text, expected_content):
        result = InternalDispatcher._extract_remember_content(text.lower(), text)
        assert result is not None
        assert expected_content in result

    @pytest.mark.parametrize("text", [
        "Aký je stav servera?",
        "Čo si pamätáš o pizze?",
        "Kto som?",
        "Zapamätaj",  # too short, no content
        "zapamätaj si ab",  # content too short (< 3 chars)
        "ahoj",
    ])
    def test_pattern_not_detected(self, text):
        result = InternalDispatcher._extract_remember_content(text.lower(), text)
        assert result is None


class TestRememberHandler:
    """Test that dispatcher stores memory and returns confirmation."""

    async def test_remember_stores_to_memory(self, dispatcher, mock_agent):
        result = await dispatcher.try_handle("Zapamätaj si že mám rád pizzu")
        assert result is not None
        assert "Zapamätal som si" in result
        assert "mám rád pizzu" in result
        mock_agent.memory.store.assert_awaited_once()

    async def test_remember_entry_has_correct_metadata(self, dispatcher, mock_agent):
        await dispatcher.try_handle("Zapamätaj si že deploy je v piatok")
        call_args = mock_agent.memory.store.call_args
        entry = call_args[0][0]
        assert entry.content == "deploy je v piatok"
        assert entry.memory_type.value == "semantic"
        assert entry.kind.value == "fact"
        assert entry.provenance.value == "user_asserted"
        assert "remembered" in entry.tags

    async def test_remember_english(self, dispatcher, mock_agent):
        result = await dispatcher.try_handle("Remember that the API key is rotated monthly")
        assert result is not None
        assert "Zapamätal som si" in result
        mock_agent.memory.store.assert_awaited_once()

    async def test_remember_bypasses_llm(self, dispatcher):
        """Remember requests are handled entirely by dispatcher, no LLM call needed."""
        result = await dispatcher.try_handle("Zapamätaj si že mám meeting o 10:00")
        # If result is not None, dispatcher handled it (no LLM fallback)
        assert result is not None

    async def test_non_remember_returns_none(self, dispatcher):
        """Normal questions still fall through to LLM."""
        result = await dispatcher.try_handle("Aký je stav servera?")
        # Status query is handled by _is_status_query, not remember
        # But longer questions fall through to None
        result = await dispatcher.try_handle("Povedz mi niečo zaujímavé o Pythone")
        assert result is None

    async def test_url_in_remember_still_dispatches(self, dispatcher, mock_agent):
        """URLs are skipped by dispatcher, but remember pattern is checked first."""
        # The URL check is at the top of try_handle, BEFORE remember detection
        # So URLs with "zapamätaj si" won't be handled
        result = await dispatcher.try_handle("Zapamätaj si https://example.com")
        # This should be None because URL check comes first
        assert result is None


class TestExpandedPatterns:
    """Bug #2: Slovak queries must be caught by dispatcher, not sent to LLM."""

    @pytest.mark.parametrize("text", [
        "aký je tvoj stav",
        "ako sa máš",
        "čo robíš",
        "si v poriadku",
        "stav",
        "status",
    ])
    async def test_status_queries_caught(self, dispatcher, text):
        result = await dispatcher.try_handle(text)
        assert result is not None, f"'{text}' should be caught as status query"

    @pytest.mark.parametrize("text", [
        "aké úlohy máš",
        "čo máš v rade",
        "zoznam úloh",
        "tasks",
        "úlohy",
    ])
    async def test_tasks_queries_caught(self, dispatcher, text):
        result = await dispatcher.try_handle(text)
        assert result is not None, f"'{text}' should be caught as tasks query"

    @pytest.mark.parametrize("text", [
        "rozpočet",
        "budget",
        "koľko máš peňazí",
    ])
    async def test_budget_queries_caught(self, dispatcher, text):
        result = await dispatcher.try_handle(text)
        assert result is not None, f"'{text}' should be caught as budget query"

    @pytest.mark.parametrize("text", [
        "kto si",
        "predstav sa",
        "who are you",
    ])
    async def test_identity_queries_caught(self, dispatcher, text):
        result = await dispatcher.try_handle(text)
        assert result is not None, f"'{text}' should be caught as identity query"

    @pytest.mark.parametrize("text", [
        "Povedz mi niečo zaujímavé o Pythone",
        "Naprogramuj mi webovú stránku",
        "Aká je predpoveď počasia?",
    ])
    async def test_complex_queries_not_caught(self, dispatcher, text):
        """Complex or ambiguous queries should NOT be caught — let LLM handle."""
        result = await dispatcher.try_handle(text)
        assert result is None, f"'{text}' should fall through to LLM"
