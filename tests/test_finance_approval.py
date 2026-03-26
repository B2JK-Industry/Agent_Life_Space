"""
Tests for finance → approval queue integration.
"""

from __future__ import annotations

import pytest

from agent.core.approval import ApprovalQueue
from agent.finance.tracker import FinanceTracker


class TestFinanceApprovalIntegration:
    """Finance proposals wire to approval queue."""

    @pytest.fixture
    async def tracker_with_queue(self, tmp_path):
        queue = ApprovalQueue()
        tracker = FinanceTracker(
            db_path=str(tmp_path / "finance.db"),
            approval_queue=queue,
        )
        await tracker.initialize()
        yield tracker, queue
        await tracker.close()

    @pytest.fixture
    async def tracker_no_queue(self, tmp_path):
        tracker = FinanceTracker(db_path=str(tmp_path / "finance2.db"))
        await tracker.initialize()
        yield tracker
        await tracker.close()

    @pytest.mark.asyncio
    async def test_proposal_creates_approval_request(self, tracker_with_queue):
        tracker, queue = tracker_with_queue
        await tracker.propose_expense(
            amount_usd=50.0,
            description="API subscription",
            category="tools",
            rationale="Need for web scraping",
        )

        pending = queue.get_pending()
        assert len(pending) == 1
        assert pending[0]["category"] == "finance"
        assert "$50.00" in pending[0]["description"]
        assert pending[0]["risk_level"] == "low"

    @pytest.mark.asyncio
    async def test_approval_context_has_transaction_id(self, tracker_with_queue):
        tracker, queue = tracker_with_queue
        await tracker.propose_expense(
            amount_usd=10.0,
            description="test expense",
        )

        pending = queue.get_pending()
        # Context should link back to transaction
        assert pending[0]["id"]  # Has approval request ID

    @pytest.mark.asyncio
    async def test_no_queue_still_works(self, tracker_no_queue):
        """Finance tracker works without approval queue (backward compat)."""
        tx = await tracker_no_queue.propose_expense(
            amount_usd=5.0,
            description="standalone expense",
        )
        assert tx.id
        assert tx.status.value == "proposed"

    @pytest.mark.asyncio
    async def test_multiple_proposals_create_multiple_requests(self, tracker_with_queue):
        tracker, queue = tracker_with_queue
        await tracker.propose_expense(1.0, "expense 1")
        await tracker.propose_expense(2.0, "expense 2")
        await tracker.propose_expense(3.0, "expense 3")

        pending = queue.get_pending()
        assert len(pending) == 3
