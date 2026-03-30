"""
Tests for Phase 3 operator Telegram commands: /report, /intake, /build.
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
    agent.review = MagicMock()
    agent.review._initialized = True
    # Operator services
    agent.reporting = MagicMock()
    agent.submit_operator_intake = AsyncMock()
    # Ensure run_review_job doesn't auto-create
    agent.run_review_job = None
    return agent


@pytest.fixture
def handler(mock_agent):
    return TelegramHandler(agent=mock_agent)


class TestReportCommand:

    async def test_report_no_args_shows_overview(self, handler, mock_agent):
        mock_agent.reporting.get_report.return_value = {
            "summary": {
                "total_jobs": 5,
                "completed_jobs": 3,
                "blocked_jobs": 1,
                "failed_jobs": 1,
                "total_artifacts": 12,
                "pending_approvals": 2,
                "total_deliveries": 1,
                "total_recorded_cost_usd": 0.0042,
            },
            "inbox": [
                {"kind": "approval", "title": "Pending finance approval"},
            ],
        }
        result = await handler.handle("/report", user_id=1, chat_id=1)
        assert "Operator Report" in result
        assert "Jobs: 5" in result
        assert "Inbox items: 1" in result

    async def test_report_inbox_shows_items(self, handler, mock_agent):
        mock_agent.reporting.get_report.return_value = {
            "summary": {},
            "inbox": [
                {"kind": "approval", "title": "Finance approval needed"},
                {"kind": "job_attention", "title": "Build job failed"},
            ],
        }
        result = await handler.handle("/report inbox", user_id=1, chat_id=1)
        assert "Operator Inbox" in result
        assert "approval" in result
        assert "Build job failed" in result

    async def test_report_inbox_empty(self, handler, mock_agent):
        mock_agent.reporting.get_report.return_value = {
            "summary": {},
            "inbox": [],
        }
        result = await handler.handle("/report inbox", user_id=1, chat_id=1)
        assert "Žiadne attention items" in result

    async def test_report_budget(self, handler, mock_agent):
        mock_agent.reporting.get_report.return_value = {
            "summary": {},
            "inbox": [],
            "budget_posture": {
                "daily_spent_usd": 1.23,
                "daily_hard_cap": 50,
                "monthly_spent_usd": 15.67,
                "monthly_hard_cap": 500,
                "pending_proposals": 0,
            },
        }
        result = await handler.handle("/report budget", user_id=1, chat_id=1)
        assert "Budget Posture" in result
        assert "$1.23" in result

    async def test_report_error_handled(self, handler, mock_agent):
        mock_agent.reporting.get_report.side_effect = RuntimeError("DB offline")
        result = await handler.handle("/report", user_id=1, chat_id=1)
        assert "Report error" in result
        assert "DB offline" in result


class TestIntakeCommand:

    async def test_intake_no_args_shows_usage(self, handler):
        result = await handler.handle("/intake", user_id=1, chat_id=1)
        assert "Použitie" in result
        assert "--description" in result

    async def test_intake_missing_description(self, handler):
        result = await handler.handle("/intake .", user_id=1, chat_id=1)
        assert "Chýba" in result
        assert "--description" in result

    async def test_intake_review_success(self, handler, mock_agent):
        mock_agent.submit_operator_intake.return_value = {
            "status": "completed",
            "job_kind": "review",
            "job_id": "abc123",
            "qualification": {
                "resolved_work_type": "review",
                "risk_level": "low",
            },
            "plan": {
                "budget": {"estimated_cost_usd": 1.50},
            },
            "job": {
                "status": "completed",
                "metadata": {"verdict": "pass"},
            },
        }
        result = await handler.handle(
            '/intake . --description security audit',
            user_id=1, chat_id=1,
        )
        assert "Intake" in result
        assert "completed" in result
        assert "abc123" in result

    async def test_intake_blocked(self, handler, mock_agent):
        mock_agent.submit_operator_intake.return_value = {
            "status": "blocked",
            "error": "Budget hard cap exceeded",
        }
        result = await handler.handle(
            '/intake . --description test',
            user_id=1, chat_id=1,
        )
        assert "blocked" in result.lower()
        assert "Budget" in result

    async def test_intake_nonexistent_path(self, handler):
        result = await handler.handle(
            '/intake nonexistent_dir_xyz --description test',
            user_id=1, chat_id=1,
        )
        assert "neexistuje" in result

    async def test_intake_with_type_build(self, handler, mock_agent):
        mock_agent.submit_operator_intake.return_value = {
            "status": "completed",
            "job_kind": "build",
            "job_id": "build123",
            "qualification": {"resolved_work_type": "build", "risk_level": "medium"},
            "plan": {"budget": {"estimated_cost_usd": 4.50}},
            "job": {"status": "completed", "metadata": {}},
        }
        result = await handler.handle(
            '/intake . --type build --description add tests',
            user_id=1, chat_id=1,
        )
        assert "build" in result.lower()


class TestBuildCommand:

    async def test_build_no_args_shows_usage(self, handler):
        result = await handler.handle("/build", user_id=1, chat_id=1)
        assert "Použitie" in result

    async def test_build_delegates_to_intake(self, handler, mock_agent):
        mock_agent.submit_operator_intake.return_value = {
            "status": "completed",
            "job_kind": "build",
            "job_id": "b456",
            "qualification": {"resolved_work_type": "build", "risk_level": "low"},
            "plan": {"budget": {"estimated_cost_usd": 4.0}},
            "job": {"status": "completed", "metadata": {}},
        }
        result = await handler.handle(
            '/build . --description add tests',
            user_id=1, chat_id=1,
        )
        assert "b456" in result
        # Verify submit_operator_intake was called
        mock_agent.submit_operator_intake.assert_called_once()
        # Check work_type was set to build
        intake_arg = mock_agent.submit_operator_intake.call_args[0][0]
        assert str(intake_arg.work_type) in ("build", "OperatorWorkType.BUILD")


class TestHelpIncludes:

    async def test_help_shows_new_commands(self, handler):
        result = await handler.handle("/help", user_id=1, chat_id=1)
        assert "/intake" in result
        assert "/build" in result
        assert "/report" in result
