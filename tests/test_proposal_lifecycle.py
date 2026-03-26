"""
Tests for finance proposal lifecycle — end-to-end flow.
"""

from __future__ import annotations

import pytest

from agent.finance.tracker import FinanceTracker, TransactionStatus


class TestProposalLifecycle:
    """Full lifecycle: propose → approve → complete."""

    @pytest.fixture
    async def tracker(self, tmp_path):
        t = FinanceTracker(db_path=str(tmp_path / "lifecycle.db"))
        await t.initialize()
        yield t
        await t.close()

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, tracker):
        # 1. Propose
        tx = await tracker.propose_expense(5.0, "API key", category="tools")
        assert tx.status == TransactionStatus.PROPOSED

        # 2. Approve
        tx = await tracker.approve(tx.id)
        assert tx.status == TransactionStatus.APPROVED

        # 3. Complete
        tx = await tracker.complete(tx.id)
        assert tx.status == TransactionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_propose_reject(self, tracker):
        tx = await tracker.propose_expense(100.0, "expensive")
        tx = await tracker.reject(tx.id, reason="too much")
        assert tx.status == TransactionStatus.REJECTED

    @pytest.mark.asyncio
    async def test_cannot_complete_without_approval(self, tracker):
        tx = await tracker.propose_expense(5.0, "test")
        with pytest.raises(ValueError, match="must be approved"):
            await tracker.complete(tx.id)

    @pytest.mark.asyncio
    async def test_cannot_approve_completed(self, tracker):
        tx = await tracker.propose_expense(5.0, "test")
        await tracker.approve(tx.id)
        await tracker.complete(tx.id)
        with pytest.raises(ValueError):
            await tracker.approve(tx.id)

    @pytest.mark.asyncio
    async def test_multiple_proposals(self, tracker):
        tx1 = await tracker.propose_expense(5.0, "expense 1")
        await tracker.propose_expense(10.0, "expense 2")
        await tracker.propose_expense(15.0, "expense 3")

        pending = tracker.get_pending_proposals()
        assert len(pending) == 3

        # Approve only first
        await tracker.approve(tx1.id)
        pending = tracker.get_pending_proposals()
        assert len(pending) == 2

    @pytest.mark.asyncio
    async def test_budget_check_during_proposal(self, tracker):
        # Budget is checked but not enforced for proposals
        tx = await tracker.propose_expense(
            1000.0, "over budget",
            rationale="testing budget check",
        )
        # Should still create the proposal
        assert tx.id
        assert tx.status == TransactionStatus.PROPOSED
