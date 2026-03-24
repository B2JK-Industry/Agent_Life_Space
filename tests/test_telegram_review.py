"""
Tests for /review Telegram command.

Covers:
- No arguments → usage help
- Nonexistent file → error
- Valid Python file → structured review output
- File with known issues → warnings/info shown
- Clean file → OK message
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.social.telegram_handler import TelegramHandler


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.memory = MagicMock()
    agent.memory.store = AsyncMock()
    return agent


@pytest.fixture
def handler(mock_agent):
    return TelegramHandler(agent=mock_agent)


class TestReviewCommand:

    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self, handler):
        result = await handler.handle("/review", user_id=1, chat_id=1)
        assert "/review" in result
        assert "súbor" in result.lower()

    @pytest.mark.asyncio
    async def test_no_args_with_space(self, handler):
        result = await handler.handle("/review   ", user_id=1, chat_id=1)
        assert "/review" in result

    @pytest.mark.asyncio
    async def test_nonexistent_file_shows_error(self, handler):
        result = await handler.handle("/review no/such/file.py", user_id=1, chat_id=1)
        assert "FAILED" in result
        assert "not found" in result.lower() or "Not found" in result

    @pytest.mark.asyncio
    async def test_valid_file_returns_review(self, handler):
        result = await handler.handle(
            "/review agent/brain/programmer.py", user_id=1, chat_id=1
        )
        assert "Code Review" in result
        assert "programmer.py" in result
        assert "riadkov" in result

    @pytest.mark.asyncio
    async def test_review_shows_line_count(self, handler):
        result = await handler.handle(
            "/review agent/brain/programmer.py", user_id=1, chat_id=1
        )
        # Should contain line count in parentheses
        assert "riadkov" in result

    @pytest.mark.asyncio
    async def test_review_file_with_issues(self, handler, tmp_path):
        """File with TODO and bare except should produce warnings/info."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text(textwrap.dedent("""\
            import os
            import sys

            # TODO: fix this later
            def foo():
                try:
                    pass
                except:
                    pass
        """))

        result = await handler.handle(f"/review {bad_file}", user_id=1, chat_id=1)
        assert "Code Review" in result
        # Should find TODO
        assert "TODO" in result or "Info" in result

    @pytest.mark.asyncio
    async def test_review_clean_file(self, handler, tmp_path):
        """Clean file with no issues returns OK."""
        clean_file = tmp_path / "clean.py"
        clean_file.write_text(textwrap.dedent('''\
            """A clean module."""

            def add(a: int, b: int) -> int:
                return a + b
        '''))

        result = await handler.handle(f"/review {clean_file}", user_id=1, chat_id=1)
        assert "OK" in result or "čisto" in result.lower()

    @pytest.mark.asyncio
    async def test_review_not_unknown_command(self, handler):
        """Ensure /review is registered, not treated as unknown."""
        result = await handler.handle("/review agent/core/agent.py", user_id=1, chat_id=1)
        assert "Neznámy príkaz" not in result
