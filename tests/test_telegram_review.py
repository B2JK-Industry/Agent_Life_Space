"""
Test /review command in Telegram handler.

1. /review with valid file returns code review output
2. /review without argument returns usage help
3. /review with nonexistent file returns error message
4. /review formats issues and suggestions correctly
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.social.telegram_handler import TelegramHandler


@pytest.fixture
def mock_agent():
    """Minimal mock of AgentOrchestrator for command testing."""
    agent = MagicMock()
    agent.memory = MagicMock()
    agent.memory.store = AsyncMock()
    return agent


@pytest.fixture
def handler(mock_agent):
    return TelegramHandler(agent=mock_agent)


class TestReviewCommand:
    """Test /review Telegram command."""

    @pytest.mark.asyncio
    async def test_review_no_args_returns_usage(self, handler):
        """Without file path, show usage."""
        result = await handler.handle("/review", user_id=1, chat_id=1)
        assert "/review" in result
        assert "súbor" in result.lower() or "path" in result.lower() or "použi" in result.lower()

    @pytest.mark.asyncio
    async def test_review_nonexistent_file(self, handler):
        """Nonexistent file returns error."""
        result = await handler.handle("/review nonexistent/file.py", user_id=1, chat_id=1)
        assert "not found" in result.lower() or "nenájdený" in result.lower() or "neexistuje" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_review_valid_file(self, handler):
        """Valid file returns review with results."""
        # Review our own handler file — it exists
        result = await handler.handle("/review agent/social/telegram_handler.py", user_id=1, chat_id=1)
        assert "review" in result.lower() or "Review" in result
        # Should not be an error
        assert "neznámy príkaz" not in result.lower()

    @pytest.mark.asyncio
    async def test_review_shows_issues_if_any(self, handler):
        """Review output includes issues section."""
        result = await handler.handle("/review agent/social/telegram_handler.py", user_id=1, chat_id=1)
        # Should have some structure — either issues or "passed"
        assert "issue" in result.lower() or "warning" in result.lower() or "info" in result.lower() or "passed" in result.lower() or "OK" in result or "✓" in result
