"""
Tests for Phase 3 operator Telegram commands: /report, /intake, /build.
"""

from __future__ import annotations

from types import SimpleNamespace
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
        # --no-coach bypasses the spec-quality gate; this test verifies
        # the intake-delegation contract, not the coach behavior.
        mock_agent.submit_operator_intake.return_value = {
            "status": "completed",
            "job_kind": "build",
            "job_id": "b456",
            "qualification": {"resolved_work_type": "build", "risk_level": "low"},
            "plan": {"budget": {"estimated_cost_usd": 4.0}},
            "job": {"status": "completed", "metadata": {}},
        }
        result = await handler.handle(
            '/build . --no-coach --description add tests',
            user_id=1, chat_id=1,
        )
        assert "b456" in result
        # Verify submit_operator_intake was called
        mock_agent.submit_operator_intake.assert_called_once()
        # Check work_type was set to build
        intake_arg = mock_agent.submit_operator_intake.call_args[0][0]
        assert str(intake_arg.work_type) in ("build", "OperatorWorkType.BUILD")

    async def test_build_uses_longer_intake_timeout(self, handler, mock_agent, monkeypatch):
        captured: dict[str, float] = {}

        async def fake_wait_for(coro, timeout):
            captured["timeout"] = timeout
            return await coro

        monkeypatch.setattr("agent.social.telegram_handler.asyncio.wait_for", fake_wait_for)
        mock_agent.submit_operator_intake.return_value = {
            "status": "completed",
            "job_kind": "build",
            "job_id": "b600",
            "qualification": {"resolved_work_type": "build", "risk_level": "low"},
            "plan": {"budget": {"estimated_cost_usd": 2.0}},
            "job": {"status": "completed", "metadata": {}},
        }

        result = await handler.handle(
            '/build . --no-coach --description scaffold service',
            user_id=1, chat_id=1,
        )

        assert "b600" in result
        assert captured["timeout"] == 600.0

    async def test_build_concrete_description_skips_gate(self, handler, mock_agent):
        """A well-formed description passes the spec gate without --no-coach.

        Documents that the gate is heuristic, not blanket-blocking: real
        specs with concrete tech terms + acceptance signals go straight
        through to intake.
        """
        mock_agent.submit_operator_intake.return_value = {
            "status": "completed",
            "job_kind": "build",
            "job_id": "b789",
            "qualification": {"resolved_work_type": "build", "risk_level": "low"},
            "plan": {"budget": {"estimated_cost_usd": 3.0}},
            "job": {"status": "completed", "metadata": {}},
        }
        # Concrete: mentions function, returns, raises, pytest, edge cases
        result = await handler.handle(
            '/build . --description '
            'Add a pytest test that calls parse_csv() with empty input '
            'and asserts it returns an empty list and raises ValueError '
            'on malformed JSON header rows',
            user_id=1, chat_id=1,
        )
        assert "b789" in result
        mock_agent.submit_operator_intake.assert_called_once()


class TestSettlementCommand:

    async def test_settlement_list_usage_includes_execute(self, handler, mock_agent):
        settlement = MagicMock()
        settlement.get_pending_settlements.return_value = []
        mock_agent.settlement = settlement

        result = await handler.handle("/settlement", user_id=1, chat_id=1)

        assert "Settlements" in result

    async def test_settlement_execute_success(self, handler, mock_agent):
        settlement = MagicMock()
        settlement.execute_topup = AsyncMock(
            return_value={"ok": True, "amount": 10.0, "retry": {"retried": True, "ok": True}}
        )
        mock_agent.settlement = settlement

        result = await handler.handle("/settlement execute set123", user_id=1, chat_id=1)

        assert "Settlement executed" in result
        assert "$10.0000" in result
        assert "Retry: OK" in result
        settlement.execute_topup.assert_awaited_once_with("set123")

    async def test_settlement_usage_mentions_execute(self, handler, mock_agent):
        settlement = MagicMock()
        mock_agent.settlement = settlement

        result = await handler.handle("/settlement nope", user_id=1, chat_id=1)

        assert "/settlement execute <id>" in result


class TestJobsCommand:

    async def test_status_includes_setup_warnings(self, handler, mock_agent):
        mock_agent.get_status.return_value = {
            "running": False,
            "memory": {"total_memories": 1},
            "tasks": {"total_tasks": 0},
            "brain": {"total_decisions": 0},
            "jobs": {"total_completed": 0, "total_failed": 0},
            "watchdog": {"modules_registered": 1, "modules_healthy": 1},
            "setup_warnings": [
                "AGENT_NAME is not configured; the runtime is still using the generic agent name.",
                "TELEGRAM_BOT_TOKEN is not configured; Telegram control surface is disabled.",
            ],
        }

        result = await handler.handle("/status", user_id=1, chat_id=1)

        assert "Setup warnings" in result
        assert "AGENT_NAME" in result
        assert "TELEGRAM_BOT_TOKEN" in result

    async def test_jobs_no_args_lists_jobs(self, handler, mock_agent):
        mock_agent.list_product_jobs.return_value = [
            {"id": "job1abcdef12", "kind": "review", "status": "completed", "created_at": "2026-03-30T10:00:00"},
            {"id": "job2abcdef34", "kind": "build", "status": "failed", "created_at": "2026-03-30T09:00:00"},
        ]
        result = await handler.handle("/jobs", user_id=1, chat_id=1)
        assert "Recent Jobs" in result
        assert "job1abcdef12" in result
        assert "review" in result

    async def test_jobs_empty(self, handler, mock_agent):
        mock_agent.list_product_jobs.return_value = []
        result = await handler.handle("/jobs", user_id=1, chat_id=1)
        assert "Žiadne joby" in result

    async def test_jobs_detail(self, handler, mock_agent):
        mock_agent.get_product_job.return_value = {
            "id": "job123",
            "kind": "review",
            "status": "completed",
            "created_at": "2026-03-30T10:00:00",
            "metadata": {"verdict": "pass", "finding_counts": {"critical": 0, "high": 1}},
        }
        result = await handler.handle("/jobs job123", user_id=1, chat_id=1)
        assert "Job job123" in result
        assert "pass" in result
        assert "1H" in result

    async def test_jobs_detail_shows_error(self, handler, mock_agent):
        mock_agent.get_product_job.return_value = {
            "id": "job456",
            "kind": "build",
            "status": "failed",
            "created_at": "2026-03-30T10:00:00",
            "error": "Docker build failed: pytest exited with status 1",
            "metadata": {},
        }
        result = await handler.handle("/jobs job456", user_id=1, chat_id=1)
        assert "Job job456" in result
        assert "Docker build failed" in result

    async def test_jobs_detail_shows_codegen_error(self, handler, mock_agent):
        mock_agent.get_product_job.return_value = {
            "id": "job789",
            "kind": "build",
            "status": "failed",
            "created_at": "2026-03-30T10:00:00",
            "error": "Required acceptance criteria unmet",
            "metadata": {"codegen_error": "Invalid authentication credentials"},
        }
        result = await handler.handle("/jobs job789", user_id=1, chat_id=1)
        assert "Job job789" in result
        assert "Codegen error" in result
        assert "Invalid authentication credentials" in result

    async def test_jobs_not_found(self, handler, mock_agent):
        mock_agent.get_product_job.return_value = None
        result = await handler.handle("/jobs nonexistent", user_id=1, chat_id=1)
        assert "nenájdený" in result


class TestDeliverCommand:

    async def test_deliver_no_args_lists(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            {"job_id": "j1", "job_kind": "review", "status": "prepared", "title": "Review delivery"},
        ]
        result = await handler.handle("/deliver", user_id=1, chat_id=1)
        assert "Recent Deliveries" in result
        assert "j1" in result

    async def test_deliver_empty(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = []
        result = await handler.handle("/deliver", user_id=1, chat_id=1)
        assert "Žiadne delivery" in result

    async def test_deliver_detail(self, handler, mock_agent):
        mock_agent.list_delivery_records.return_value = [
            {
                "job_id": "j1",
                "job_kind": "review",
                "status": "awaiting_approval",
                "title": "Review handoff",
                "approval_request_id": "apr123",
                "events": [
                    {"event_type": "created", "status": "prepared"},
                ],
            },
        ]
        result = await handler.handle("/deliver j1", user_id=1, chat_id=1)
        assert "awaiting_approval" in result
        assert "apr123" in result

    async def test_deliver_send_no_bundle(self, handler, mock_agent):
        mock_agent.get_review_delivery_bundle.return_value = None
        mock_agent.get_build_delivery_bundle.return_value = None
        result = await handler.handle("/deliver j1 send", user_id=1, chat_id=1)
        assert "nemá delivery bundle" in result


class TestHelpIncludes:

    async def test_help_shows_new_commands(self, handler):
        result = await handler.handle("/help", user_id=1, chat_id=1)
        assert "/intake" in result
        assert "/build" in result
        assert "/report" in result
        assert "/jobs" in result
        assert "/deliver" in result
        assert "/queue [pending|approve|deny]" in result


class TestQueueCommand:

    async def test_queue_pending_lists_approvals(self, handler, mock_agent):
        approval_queue = MagicMock()
        approval_queue.get_pending.return_value = [
            {
                "id": "apr123",
                "category": "external",
                "risk_level": "medium",
                "description": "Submit bid to Obolos listing",
                "reason": "External write requires approval",
            }
        ]
        mock_agent.approval_queue = approval_queue

        result = await handler.handle("/queue pending", user_id=1, chat_id=1)

        assert "Pending approvals" in result
        assert "apr123" in result
        assert "Submit bid to Obolos listing" in result

    async def test_queue_approve_executes(self, handler, mock_agent):
        approval_queue = MagicMock()
        approval_queue.approve.return_value = SimpleNamespace(id="apr123", status="approved")
        mock_agent.approval_queue = approval_queue

        result = await handler.handle("/queue approve apr123", user_id=1, chat_id=1)

        assert "approved" in result.lower()
        approval_queue.approve.assert_called_once_with("apr123", decided_by="owner")

    async def test_queue_deny_executes(self, handler, mock_agent):
        approval_queue = MagicMock()
        approval_queue.deny.return_value = SimpleNamespace(id="apr123", status="denied")
        mock_agent.approval_queue = approval_queue

        result = await handler.handle("/queue deny apr123 too risky", user_id=1, chat_id=1)

        assert "denied" in result.lower()
        assert "too risky" in result
        approval_queue.deny.assert_called_once_with(
            "apr123", reason="too risky", decided_by="owner",
        )


class TestTypingIndicatorCleanup:
    """The typing indicator task spawned per message must not leak.

    Regression: cancel() alone wasn't enough — the loop needed an explicit
    CancelledError handler and the finally block needed to await the task,
    otherwise long-running chats accumulated dangling tasks and noisy
    "Task was destroyed but it is pending!" warnings.
    """

    async def test_typing_task_does_not_leak_after_handle(self, mock_agent):
        import asyncio

        bot = MagicMock()
        bot._api_call = AsyncMock(return_value={"ok": True})
        # Use a brain that returns instantly so handle() exits fast.
        brain = MagicMock()
        brain.process = AsyncMock(return_value="hello back")

        handler = TelegramHandler(agent=mock_agent, bot=bot, brain=brain)

        before = {t for t in asyncio.all_tasks() if not t.done()}
        result = await handler.handle("hello agent", user_id=1, chat_id=42)
        # Give the loop one tick so cancellation propagates.
        await asyncio.sleep(0)
        after = {t for t in asyncio.all_tasks() if not t.done()}

        leaked = after - before
        assert not leaked, f"handle() leaked {len(leaked)} task(s): {leaked}"
        assert result == "hello back"

    async def test_typing_task_cleaned_up_when_brain_raises(self, mock_agent):
        import asyncio

        bot = MagicMock()
        bot._api_call = AsyncMock(return_value={"ok": True})
        brain = MagicMock()
        brain.process = AsyncMock(side_effect=RuntimeError("boom"))

        handler = TelegramHandler(agent=mock_agent, bot=bot, brain=brain)

        before = {t for t in asyncio.all_tasks() if not t.done()}
        with pytest.raises(RuntimeError):
            await handler.handle("hello agent", user_id=1, chat_id=42)
        await asyncio.sleep(0)
        after = {t for t in asyncio.all_tasks() if not t.done()}

        leaked = after - before
        assert not leaked, (
            f"handle() leaked {len(leaked)} task(s) after exception: {leaked}"
        )


class TestAgentCronStop:
    """AgentCron.stop() must await cancelled tasks, not just call cancel()."""

    async def test_stop_awaits_cancelled_tasks(self):
        from agent.core.cron import AgentCron

        agent = MagicMock()
        agent.watchdog = MagicMock()
        agent.memory = MagicMock()
        agent.tasks = MagicMock()

        cron = AgentCron(agent, telegram_bot=None, owner_chat_id=0)
        # Start the cron — it spawns ~11 background loops, all of which
        # spend their time inside asyncio.sleep().
        await cron.start()
        assert cron._tasks, "cron.start() should spawn background tasks"
        captured = list(cron._tasks)

        await cron.stop()

        # After stop() returns, every task must be done (either cancelled
        # or completed). The previous implementation would only set the
        # cancel flag and return immediately, leaving tasks pending.
        for t in captured:
            assert t.done(), f"cron task {t!r} still pending after stop()"
        assert cron._tasks == [], "cron should clear its task list on stop()"
        # And the running flag should be back to False.
        assert cron._running is False


class TestYesNoCommands:
    """`/yes` and `/no` operator UX shorthand for approval queue."""

    @pytest.fixture
    def handler_with_real_queue(self, mock_agent):
        """Handler wired up with a real ApprovalQueue (in-memory)."""
        from agent.core.approval import ApprovalQueue
        mock_agent.approval_queue = ApprovalQueue()
        return TelegramHandler(agent=mock_agent), mock_agent.approval_queue

    async def test_yes_no_pending_returns_clear_message(self, handler_with_real_queue):
        handler, queue = handler_with_real_queue
        result = await handler.handle("/yes", user_id=1, chat_id=1)
        assert "nečakajú" in result.lower() or "no pending" in result.lower()

    async def test_no_no_pending_returns_clear_message(self, handler_with_real_queue):
        handler, queue = handler_with_real_queue
        result = await handler.handle("/no", user_id=1, chat_id=1)
        assert "nečakajú" in result.lower() or "no pending" in result.lower()

    async def test_yes_unique_pending_approves_it(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        proposal = queue.propose(
            ApprovalCategory.EXTERNAL,
            description="Submit bid X",
            reason="Marketplace bid on obolos.tech",
            proposed_by="marketplace_service",
        )

        result = await handler.handle("/yes", user_id=1, chat_id=1)

        assert "approved" in result.lower()
        assert proposal.id in result
        # Audit trail intact: queue says approved/executed
        after = queue.get_request(proposal.id)
        assert after is not None
        assert after["status"] in ("approved", "executed")

    async def test_no_unique_pending_denies_it(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        proposal = queue.propose(
            ApprovalCategory.FINANCE,
            description="Create paid listing",
            reason="John as client",
            proposed_by="marketplace_service.create_listing",
        )

        result = await handler.handle("/no too expensive", user_id=1, chat_id=1)

        assert "denied" in result.lower()
        assert proposal.id in result
        assert "too expensive" in result
        after = queue.get_request(proposal.id)
        assert after is not None
        assert after["status"] == "denied"

    async def test_yes_with_explicit_id(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        # Two pending → ambiguous without id, must use explicit
        p1 = queue.propose(ApprovalCategory.EXTERNAL, "Bid one", proposed_by="x")
        p2 = queue.propose(ApprovalCategory.EXTERNAL, "Bid two", proposed_by="x")

        result = await handler.handle(f"/yes {p2.id}", user_id=1, chat_id=1)
        assert p2.id in result
        assert "approved" in result.lower()

        # The other one is untouched
        assert queue.get_request(p1.id)["status"] == "pending"

    async def test_no_with_explicit_id_and_reason(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        p1 = queue.propose(ApprovalCategory.EXTERNAL, "Bid one", proposed_by="x")
        p2 = queue.propose(ApprovalCategory.EXTERNAL, "Bid two", proposed_by="x")

        result = await handler.handle(
            f"/no {p2.id} budget too high", user_id=1, chat_id=1,
        )
        assert p2.id in result
        assert "denied" in result.lower()
        assert "budget too high" in result
        assert queue.get_request(p1.id)["status"] == "pending"
        assert queue.get_request(p2.id)["status"] == "denied"

    async def test_yes_ambiguity_refuses_to_guess(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        p1 = queue.propose(ApprovalCategory.EXTERNAL, "Bid A", proposed_by="x")
        p2 = queue.propose(ApprovalCategory.FINANCE, "Listing B", proposed_by="x")

        result = await handler.handle("/yes", user_id=1, chat_id=1)

        # Must not approve anything
        assert queue.get_request(p1.id)["status"] == "pending"
        assert queue.get_request(p2.id)["status"] == "pending"
        # Must guide to use explicit id
        assert "/yes <id>" in result or "/queue" in result
        # Must list the candidates
        assert p1.id in result or p2.id in result

    async def test_no_ambiguity_refuses_to_guess(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        queue.propose(ApprovalCategory.EXTERNAL, "Bid A", proposed_by="x")
        queue.propose(ApprovalCategory.FINANCE, "Listing B", proposed_by="x")

        result = await handler.handle("/no rejected", user_id=1, chat_id=1)
        # Both must remain pending — `/no` did not deny anything
        pending_after = queue.get_pending()
        assert len(pending_after) == 2
        assert "/no <id>" in result or "/queue" in result

    async def test_yes_unknown_id_returns_not_found(self, handler_with_real_queue):
        handler, queue = handler_with_real_queue
        result = await handler.handle("/yes deadbeef1234", user_id=1, chat_id=1)
        assert "not found" in result.lower()

    async def test_no_unknown_id_returns_not_found(self, handler_with_real_queue):
        handler, queue = handler_with_real_queue
        result = await handler.handle("/no abcdef123456", user_id=1, chat_id=1)
        assert "not found" in result.lower()

    async def test_yes_already_approved_reports_state(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        proposal = queue.propose(ApprovalCategory.TOOL, "X", proposed_by="x")
        queue.approve(proposal.id, decided_by="owner")

        result = await handler.handle(f"/yes {proposal.id}", user_id=1, chat_id=1)
        # Truthful: it's already in approved/executed state
        assert "approved" in result.lower() or "executed" in result.lower()

    async def test_no_already_denied_reports_state(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        proposal = queue.propose(ApprovalCategory.TOOL, "X", proposed_by="x")
        queue.deny(proposal.id, reason="initial", decided_by="owner")

        result = await handler.handle(f"/no {proposal.id}", user_id=1, chat_id=1)
        assert "denied" in result.lower()

    async def test_yes_expired_approval_reports_state(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        # ttl=0 → instantly expired on next get_pending() / expire_stale()
        proposal = queue.propose(
            ApprovalCategory.TOOL, "X", proposed_by="x", ttl_seconds=0,
        )
        # Force expiration (matches what `/yes` would do internally)
        queue.expire_stale()

        result = await handler.handle(f"/yes {proposal.id}", user_id=1, chat_id=1)
        # Truthful: state is expired (or treated as already-resolved)
        assert "expired" in result.lower() or "already" in result.lower() or "not found" in result.lower()

    async def test_yes_short_prefix_resolves_when_unique(self, handler_with_real_queue):
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        proposal = queue.propose(ApprovalCategory.EXTERNAL, "Bid X", proposed_by="x")
        # Use first 6 chars as a prefix
        prefix = proposal.id[:6]

        result = await handler.handle(f"/yes {prefix}", user_id=1, chat_id=1)
        assert proposal.id in result
        assert "approved" in result.lower()

    async def test_yes_no_queue_returns_clear_error(self, mock_agent):
        # No approval_queue attribute at all
        mock_agent.approval_queue = None
        handler = TelegramHandler(agent=mock_agent)
        result = await handler.handle("/yes", user_id=1, chat_id=1)
        assert "queue" in result.lower() or "approval" in result.lower()

    async def test_existing_queue_approve_still_works(self, handler_with_real_queue):
        """Regression: /queue approve <id> path is unchanged."""
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        proposal = queue.propose(ApprovalCategory.EXTERNAL, "Test", proposed_by="x")
        result = await handler.handle(
            f"/queue approve {proposal.id}", user_id=1, chat_id=1,
        )
        assert "approved" in result.lower()
        assert proposal.id in result

    async def test_existing_queue_deny_still_works(self, handler_with_real_queue):
        """Regression: /queue deny <id> path is unchanged."""
        from agent.core.approval import ApprovalCategory
        handler, queue = handler_with_real_queue
        proposal = queue.propose(ApprovalCategory.EXTERNAL, "Test", proposed_by="x")
        result = await handler.handle(
            f"/queue deny {proposal.id} bad fit", user_id=1, chat_id=1,
        )
        assert "denied" in result.lower()
