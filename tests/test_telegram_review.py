"""
Tests for /review Telegram command.

Covers:
- No arguments → usage help
- Nonexistent path → error
- Valid file/dir → structured review output via ReviewService
- Review response format for Telegram
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.social.telegram_handler import TelegramHandler


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.memory = MagicMock()
    agent.memory.store = AsyncMock()
    # ReviewService mock
    agent.review = MagicMock()
    agent.review.initialize = MagicMock()
    agent.review._initialized = True
    # Ensure run_review_job is not auto-created as a callable MagicMock
    # so the handler falls through to agent.review.run_review
    agent.run_review_job = None
    return agent


@pytest.fixture
def handler(mock_agent):
    return TelegramHandler(agent=mock_agent)


class TestReviewCommand:

    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self, handler):
        result = await handler.handle("/review", user_id=1, chat_id=1)
        assert "/review" in result

    @pytest.mark.asyncio
    async def test_no_args_with_space(self, handler):
        result = await handler.handle("/review   ", user_id=1, chat_id=1)
        assert "/review" in result

    @pytest.mark.asyncio
    async def test_nonexistent_file_shows_error(self, handler):
        result = await handler.handle("/review no/such/file.py", user_id=1, chat_id=1)
        assert "neexistuje" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_valid_dir_returns_review(self, handler, mock_agent, tmp_path):
        """Valid directory triggers ReviewService.run_review()."""
        from agent.review.models import ReviewJob, ReviewJobStatus, ReviewReport

        mock_job = ReviewJob(status=ReviewJobStatus.COMPLETED)
        mock_job.report = ReviewReport(
            executive_summary="Test",
            verdict="pass",
            files_analyzed=3,
            total_lines=100,
        )
        mock_agent.review.run_review = AsyncMock(return_value=mock_job)

        result = await handler.handle(f"/review {tmp_path}", user_id=1, chat_id=1)
        assert "Review Report" in result
        assert "pass" in result
        mock_agent.review.run_review.assert_called_once()

    @pytest.mark.asyncio
    async def test_review_with_findings(self, handler, mock_agent, tmp_path):
        """Review with findings shows severity counts and finding titles."""
        from agent.review.models import (
            ReviewFinding,
            ReviewJob,
            ReviewJobStatus,
            ReviewReport,
            Severity,
        )

        mock_job = ReviewJob(status=ReviewJobStatus.COMPLETED)
        mock_job.report = ReviewReport(
            executive_summary="Found issues",
            verdict="pass_with_findings",
            files_analyzed=5,
            total_lines=200,
            findings=[
                ReviewFinding(severity=Severity.HIGH, title="eval() usage",
                              file_path="app.py", line_start=10, recommendation="Remove eval"),
                ReviewFinding(severity=Severity.LOW, title="No README"),
            ],
        )
        mock_agent.review.run_review = AsyncMock(return_value=mock_job)

        result = await handler.handle(f"/review {tmp_path}", user_id=1, chat_id=1)
        assert "1H" in result  # 1 high
        assert "1L" in result  # 1 low
        assert "eval() usage" in result
        assert "Remove eval" in result

    @pytest.mark.asyncio
    async def test_review_not_unknown_command(self, handler, mock_agent, tmp_path):
        """Ensure /review is registered, not treated as unknown."""
        from agent.review.models import ReviewJob, ReviewJobStatus, ReviewReport
        mock_job = ReviewJob(status=ReviewJobStatus.COMPLETED)
        mock_job.report = ReviewReport(verdict="pass", files_analyzed=1, total_lines=10)
        mock_agent.review.run_review = AsyncMock(return_value=mock_job)
        result = await handler.handle(f"/review {tmp_path}", user_id=1, chat_id=1)
        assert "Neznámy príkaz" not in result
