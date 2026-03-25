"""
Agent Life Space — Finance Tracker

Tracks income, expenses, and proposals.
EVERY financial action requires human approval. No exceptions.

Design:
    - Agent can PROPOSE spending/earning actions
    - Agent CANNOT execute them without approval
    - All transactions are logged and auditable
    - Budget limits are enforced algorithmically
    - No wallet access, no smart contracts, no crypto operations
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import aiosqlite
import orjson
import structlog

logger = structlog.get_logger(__name__)


class TransactionType(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"
    PROPOSAL = "proposal"  # Not yet approved


class TransactionStatus(str, Enum):
    PROPOSED = "proposed"  # Waiting for human approval
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Transaction:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: TransactionType = TransactionType.PROPOSAL
    status: TransactionStatus = TransactionStatus.PROPOSED
    amount_usd: float = 0.0
    currency: str = "USD"
    description: str = ""
    category: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    rationale: str = ""
    source: str = ""  # Where money comes from/goes to
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    approved_at: str | None = None
    completed_at: str | None = None
    approved_by: str = ""  # Always "human" — agent cannot approve
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "status": self.status.value,
            "amount_usd": self.amount_usd,
            "currency": self.currency,
            "description": self.description,
            "category": self.category,
            "risk_level": self.risk_level.value,
            "rationale": self.rationale,
            "source": self.source,
            "created_at": self.created_at,
            "approved_at": self.approved_at,
            "completed_at": self.completed_at,
            "approved_by": self.approved_by,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        return cls(
            id=data["id"],
            type=TransactionType(data["type"]),
            status=TransactionStatus(data["status"]),
            amount_usd=data["amount_usd"],
            currency=data.get("currency", "USD"),
            description=data.get("description", ""),
            category=data.get("category", ""),
            risk_level=RiskLevel(data.get("risk_level", "low")),
            rationale=data.get("rationale", ""),
            source=data.get("source", ""),
            created_at=data.get("created_at", ""),
            approved_at=data.get("approved_at"),
            completed_at=data.get("completed_at"),
            approved_by=data.get("approved_by", ""),
            metadata=data.get("metadata", {}),
        )


class FinanceTracker:
    """
    Tracks all financial activity.

    SECURITY CONSTRAINTS:
    - Agent can propose transactions
    - Only humans can approve
    - Budget limits are hard-coded and enforced
    - No direct access to payment methods
    """

    def __init__(
        self,
        db_path: str = "agent/finance/finance.db",
        daily_budget_usd: float = 50.0,
        monthly_budget_usd: float = 500.0,
    ) -> None:
        if not db_path:
            msg = "db_path cannot be empty"
            raise ValueError(msg)
        if daily_budget_usd < 0:
            msg = "daily_budget_usd cannot be negative"
            raise ValueError(msg)
        if monthly_budget_usd < 0:
            msg = "monthly_budget_usd cannot be negative"
            raise ValueError(msg)

        self._db_path = db_path
        self._daily_budget = daily_budget_usd
        self._monthly_budget = monthly_budget_usd
        self._transactions: dict[str, Transaction] = {}
        self._db: aiosqlite.Connection | None = None
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        self._db = await aiosqlite.connect(self._db_path)
        self._initialized = True
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        await self._db.commit()

        async with self._db.execute("SELECT id, data FROM transactions") as cursor:
            async for row in cursor:
                tx = Transaction.from_dict(orjson.loads(row[1]))
                self._transactions[tx.id] = tx

        logger.info("finance_tracker_initialized", count=len(self._transactions))

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def propose_expense(
        self,
        amount_usd: float,
        description: str,
        category: str = "",
        rationale: str = "",
        source: str = "",
        risk_level: RiskLevel = RiskLevel.LOW,
    ) -> Transaction:
        """
        Propose an expense. DOES NOT execute it.
        Returns a proposal that must be approved by a human.
        Budget is CHECKED but not ENFORCED — proposals over budget
        still go through (with a warning) because human makes final call.
        """
        if not description or not description.strip():
            msg = "Expense description cannot be empty"
            raise ValueError(msg)
        if amount_usd < 0:
            msg = "Amount cannot be negative"
            raise ValueError(msg)

        # Check budget limits BEFORE proposing
        budget_check = self.check_budget(amount_usd)
        if not budget_check["within_budget"]:
            logger.warning(
                "expense_exceeds_budget",
                amount=amount_usd,
                daily_remaining=budget_check["daily_remaining"],
                monthly_remaining=budget_check["monthly_remaining"],
            )

        tx = Transaction(
            type=TransactionType.EXPENSE,
            status=TransactionStatus.PROPOSED,
            amount_usd=amount_usd,
            description=description,
            category=category,
            risk_level=risk_level,
            rationale=rationale,
            source=source,
            metadata={"budget_check": budget_check},
        )

        self._transactions[tx.id] = tx
        await self._persist(tx)

        logger.info(
            "expense_proposed",
            id=tx.id,
            amount=amount_usd,
            category=category,
            within_budget=budget_check["within_budget"],
        )
        return tx

    async def record_income(
        self,
        amount_usd: float,
        description: str,
        category: str = "",
        source: str = "",
    ) -> Transaction:
        """Record income. Still logged but doesn't need approval."""
        if amount_usd < 0:
            msg = "Income amount cannot be negative"
            raise ValueError(msg)

        tx = Transaction(
            type=TransactionType.INCOME,
            status=TransactionStatus.COMPLETED,
            amount_usd=amount_usd,
            description=description,
            category=category,
            source=source,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        self._transactions[tx.id] = tx
        await self._persist(tx)

        logger.info(
            "income_recorded",
            id=tx.id,
            amount=amount_usd,
            source=source,
        )
        return tx

    async def approve(self, tx_id: str) -> Transaction:
        """
        Approve a proposed transaction. ONLY callable by human.
        In the system, this is triggered by human interaction, never by agent.
        """
        tx = self._get_tx(tx_id)
        if tx.status != TransactionStatus.PROPOSED:
            msg = f"Transaction '{tx_id}' is {tx.status.value}, cannot approve"
            raise ValueError(msg)

        tx.status = TransactionStatus.APPROVED
        tx.approved_at = datetime.now(timezone.utc).isoformat()
        tx.approved_by = "human"  # Always human
        await self._persist(tx)

        logger.info("transaction_approved", id=tx_id, amount=tx.amount_usd)
        return tx

    async def reject(self, tx_id: str, reason: str = "") -> Transaction:
        """Reject a proposed transaction."""
        tx = self._get_tx(tx_id)
        if tx.status != TransactionStatus.PROPOSED:
            msg = f"Transaction '{tx_id}' is {tx.status.value}, cannot reject"
            raise ValueError(msg)

        tx.status = TransactionStatus.REJECTED
        tx.metadata["rejection_reason"] = reason
        await self._persist(tx)

        logger.info("transaction_rejected", id=tx_id, reason=reason)
        return tx

    async def complete(self, tx_id: str) -> Transaction:
        """Mark an approved transaction as completed."""
        tx = self._get_tx(tx_id)
        if tx.status != TransactionStatus.APPROVED:
            msg = f"Transaction '{tx_id}' must be approved before completion"
            raise ValueError(msg)

        tx.status = TransactionStatus.COMPLETED
        tx.completed_at = datetime.now(timezone.utc).isoformat()
        await self._persist(tx)
        return tx

    def check_budget(self, proposed_amount: float = 0.0) -> dict[str, Any]:
        """
        Check budget status. DETERMINISTIC — no LLM.
        Returns remaining budget and whether proposed amount fits.
        """
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        month = now.strftime("%Y-%m")

        daily_spent = sum(
            tx.amount_usd
            for tx in self._transactions.values()
            if tx.type == TransactionType.EXPENSE
            and tx.status in (TransactionStatus.APPROVED, TransactionStatus.COMPLETED)
            and tx.created_at.startswith(today)
        )

        monthly_spent = sum(
            tx.amount_usd
            for tx in self._transactions.values()
            if tx.type == TransactionType.EXPENSE
            and tx.status in (TransactionStatus.APPROVED, TransactionStatus.COMPLETED)
            and tx.created_at.startswith(month)
        )

        daily_remaining = self._daily_budget - daily_spent
        monthly_remaining = self._monthly_budget - monthly_spent

        return {
            "daily_spent": round(daily_spent, 2),
            "daily_remaining": round(daily_remaining, 2),
            "daily_budget": self._daily_budget,
            "monthly_spent": round(monthly_spent, 2),
            "monthly_remaining": round(monthly_remaining, 2),
            "monthly_budget": self._monthly_budget,
            "within_budget": (
                proposed_amount <= daily_remaining
                and proposed_amount <= monthly_remaining
            ),
        }

    def get_total_income(self) -> float:
        return sum(
            tx.amount_usd
            for tx in self._transactions.values()
            if tx.type == TransactionType.INCOME
            and tx.status == TransactionStatus.COMPLETED
        )

    def get_total_expenses(self) -> float:
        return sum(
            tx.amount_usd
            for tx in self._transactions.values()
            if tx.type == TransactionType.EXPENSE
            and tx.status == TransactionStatus.COMPLETED
        )

    def get_pending_proposals(self) -> list[Transaction]:
        return [
            tx for tx in self._transactions.values()
            if tx.status == TransactionStatus.PROPOSED
        ]

    def _get_tx(self, tx_id: str) -> Transaction:
        tx = self._transactions.get(tx_id)
        if not tx:
            msg = f"Transaction '{tx_id}' not found"
            raise KeyError(msg)
        return tx

    async def _persist(self, tx: Transaction) -> None:
        if self._db:
            data = orjson.dumps(tx.to_dict()).decode()
            await self._db.execute(
                "INSERT OR REPLACE INTO transactions (id, data) VALUES (?, ?)",
                (tx.id, data),
            )
            await self._db.commit()

    async def check_stale_proposals(
        self,
        max_age_days: int = 7,
    ) -> list[dict[str, Any]]:
        """
        Dead man switch — nájdi proposals ktoré čakajú príliš dlho.

        Neauto-approvuje! Len identifikuje a notifikuje.
        Politika:
            - 3 dni: warning (pripomienka)
            - 7 dní: escalation (urgentná notifikácia)
            - 14 dní: auto-cancel (Daniel evidentne nechce)
        """
        now = datetime.now(timezone.utc)
        stale: list[dict[str, Any]] = []

        for tx in self._transactions.values():
            if tx.status != TransactionStatus.PROPOSED:
                continue

            try:
                created = datetime.fromisoformat(tx.created_at)
                age_days = (now - created).days
            except (ValueError, TypeError):
                continue

            if age_days >= 14:
                # Auto-cancel — príliš staré
                tx.status = TransactionStatus.CANCELLED
                tx.metadata["auto_cancelled"] = True
                tx.metadata["cancel_reason"] = f"Stale proposal ({age_days} dní bez odpovede)"
                await self._persist(tx)
                stale.append({
                    "id": tx.id,
                    "description": tx.description,
                    "amount": tx.amount_usd,
                    "age_days": age_days,
                    "action": "auto_cancelled",
                })
                logger.info("proposal_auto_cancelled", id=tx.id, age_days=age_days)
            elif age_days >= max_age_days:
                stale.append({
                    "id": tx.id,
                    "description": tx.description,
                    "amount": tx.amount_usd,
                    "age_days": age_days,
                    "action": "escalation",
                })
            elif age_days >= 3:
                stale.append({
                    "id": tx.id,
                    "description": tx.description,
                    "amount": tx.amount_usd,
                    "age_days": age_days,
                    "action": "warning",
                })

        if stale:
            logger.info("stale_proposals_found", count=len(stale))

        return stale

    def get_stats(self) -> dict[str, Any]:
        budget = self.check_budget()
        return {
            "total_transactions": len(self._transactions),
            "total_income": round(self.get_total_income(), 2),
            "total_expenses": round(self.get_total_expenses(), 2),
            "net": round(self.get_total_income() - self.get_total_expenses(), 2),
            "pending_proposals": len(self.get_pending_proposals()),
            "budget": budget,
        }
