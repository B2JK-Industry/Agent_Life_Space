"""
Test scenarios for Finance Tracker.

SECURITY CRITICAL:
1. Expenses require approval — agent cannot auto-approve
2. Budget limits enforced
3. Negative amounts rejected
4. Complete lifecycle: propose → approve → complete
5. Rejected proposals tracked
6. Income/expense balances correct
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent.finance.tracker import (
    FinanceTracker,
    TransactionStatus,
    TransactionType,
)


@pytest.fixture
async def tracker():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    ft = FinanceTracker(
        db_path=db_path,
        daily_budget_usd=100.0,
        monthly_budget_usd=1000.0,
    )
    await ft.initialize()
    yield ft
    await ft.close()
    os.unlink(db_path)


class TestExpenseProposal:
    """Agent proposes, human approves."""

    @pytest.mark.asyncio
    async def test_propose_expense(self, tracker: FinanceTracker) -> None:
        tx = await tracker.propose_expense(
            amount_usd=12.99,
            description="Buy domain example.com",
            category="infrastructure",
            rationale="Need a domain for the project",
        )
        assert tx.status == TransactionStatus.PROPOSED
        assert tx.type == TransactionType.EXPENSE
        assert tx.amount_usd == 12.99

    @pytest.mark.asyncio
    async def test_negative_amount_rejected(self, tracker: FinanceTracker) -> None:
        with pytest.raises(ValueError, match="negative"):
            await tracker.propose_expense(
                amount_usd=-10.0,
                description="Invalid",
            )

    @pytest.mark.asyncio
    async def test_proposal_includes_budget_check(self, tracker: FinanceTracker) -> None:
        tx = await tracker.propose_expense(
            amount_usd=50.0,
            description="Test expense",
        )
        assert "budget_check" in tx.metadata
        assert tx.metadata["budget_check"]["within_budget"] is True


class TestApprovalFlow:
    """Full approval lifecycle."""

    @pytest.mark.asyncio
    async def test_approve_proposal(self, tracker: FinanceTracker) -> None:
        tx = await tracker.propose_expense(10.0, "Test")
        tx = await tracker.approve(tx.id)
        assert tx.status == TransactionStatus.APPROVED
        assert tx.approved_by == "human"
        assert tx.approved_at is not None

    @pytest.mark.asyncio
    async def test_reject_proposal(self, tracker: FinanceTracker) -> None:
        tx = await tracker.propose_expense(10.0, "Test")
        tx = await tracker.reject(tx.id, reason="Too expensive")
        assert tx.status == TransactionStatus.REJECTED
        assert tx.metadata["rejection_reason"] == "Too expensive"

    @pytest.mark.asyncio
    async def test_complete_approved(self, tracker: FinanceTracker) -> None:
        tx = await tracker.propose_expense(10.0, "Test")
        await tracker.approve(tx.id)
        tx = await tracker.complete(tx.id)
        assert tx.status == TransactionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cannot_complete_without_approval(
        self, tracker: FinanceTracker
    ) -> None:
        tx = await tracker.propose_expense(10.0, "Test")
        with pytest.raises(ValueError, match="approved"):
            await tracker.complete(tx.id)

    @pytest.mark.asyncio
    async def test_cannot_approve_already_rejected(
        self, tracker: FinanceTracker
    ) -> None:
        tx = await tracker.propose_expense(10.0, "Test")
        await tracker.reject(tx.id)
        with pytest.raises(ValueError):
            await tracker.approve(tx.id)


class TestBudgetLimits:
    """Budget limits are enforced algorithmically."""

    @pytest.mark.asyncio
    async def test_within_budget(self, tracker: FinanceTracker) -> None:
        check = tracker.check_budget(50.0)
        assert check["within_budget"] is True
        assert check["daily_remaining"] == 100.0

    @pytest.mark.asyncio
    async def test_exceeds_daily_budget(self, tracker: FinanceTracker) -> None:
        check = tracker.check_budget(150.0)
        assert check["within_budget"] is False
        assert check["denial"]["code"] == "finance_budget_blocked"

    @pytest.mark.asyncio
    async def test_budget_decreases_after_spend(
        self, tracker: FinanceTracker
    ) -> None:
        tx = await tracker.propose_expense(30.0, "Expense 1")
        await tracker.approve(tx.id)
        await tracker.complete(tx.id)

        check = tracker.check_budget()
        assert check["daily_spent"] == 30.0
        assert check["daily_remaining"] == 70.0


class TestIncome:
    """Income tracking."""

    @pytest.mark.asyncio
    async def test_record_income(self, tracker: FinanceTracker) -> None:
        tx = await tracker.record_income(
            amount_usd=50.0,
            description="Freelance payment",
            source="client_a",
        )
        assert tx.type == TransactionType.INCOME
        assert tx.status == TransactionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_negative_income_rejected(self, tracker: FinanceTracker) -> None:
        with pytest.raises(ValueError, match="negative"):
            await tracker.record_income(-10.0, "Bad")

    @pytest.mark.asyncio
    async def test_net_calculation(self, tracker: FinanceTracker) -> None:
        await tracker.record_income(100.0, "Income")
        tx = await tracker.propose_expense(30.0, "Expense")
        await tracker.approve(tx.id)
        await tracker.complete(tx.id)

        stats = tracker.get_stats()
        assert stats["total_income"] == 100.0
        assert stats["total_expenses"] == 30.0
        assert stats["net"] == 70.0


class TestPendingProposals:
    @pytest.mark.asyncio
    async def test_pending_list(self, tracker: FinanceTracker) -> None:
        await tracker.propose_expense(10.0, "A")
        await tracker.propose_expense(20.0, "B")
        tx_c = await tracker.propose_expense(30.0, "C")
        await tracker.approve(tx_c.id)

        pending = tracker.get_pending_proposals()
        assert len(pending) == 2


class TestFinancePersistence:
    @pytest.mark.asyncio
    async def test_persist_and_reload(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            ft1 = FinanceTracker(db_path=db_path)
            await ft1.initialize()
            tx = await ft1.propose_expense(25.0, "Persistent expense")
            tx_id = tx.id
            await ft1.close()

            ft2 = FinanceTracker(db_path=db_path)
            await ft2.initialize()
            loaded = ft2._transactions.get(tx_id)
            assert loaded is not None
            assert loaded.amount_usd == 25.0
            await ft2.close()
        finally:
            os.unlink(db_path)
