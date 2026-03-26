"""
Agent Life Space — Budget Policy

Multi-level budget enforcement:
    - HARD CAP: absolute maximum, cannot be exceeded
    - SOFT CAP: warning threshold, proposals still allowed
    - APPROVAL CAP: individual transaction limit requiring approval

All caps are deterministic, no LLM involvement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BudgetLimits:
    """Budget limits configuration."""

    daily_hard_cap: float = 50.0      # Absolute max per day
    daily_soft_cap: float = 30.0      # Warning threshold per day
    monthly_hard_cap: float = 500.0   # Absolute max per month
    monthly_soft_cap: float = 300.0   # Warning threshold per month
    single_tx_approval_cap: float = 20.0  # Single transaction requiring extra approval


class BudgetCheckResult:
    """Result of a budget policy check."""

    def __init__(
        self,
        allowed: bool,
        warnings: list[str] | None = None,
        hard_cap_hit: bool = False,
        soft_cap_hit: bool = False,
        requires_approval: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.allowed = allowed
        self.warnings = warnings or []
        self.hard_cap_hit = hard_cap_hit
        self.soft_cap_hit = soft_cap_hit
        self.requires_approval = requires_approval
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "warnings": self.warnings,
            "hard_cap_hit": self.hard_cap_hit,
            "soft_cap_hit": self.soft_cap_hit,
            "requires_approval": self.requires_approval,
            "details": self.details,
        }


class BudgetPolicy:
    """
    Multi-level budget enforcement.

    Checks proposed expenses against hard caps, soft caps,
    and single-transaction approval thresholds.
    """

    def __init__(self, limits: BudgetLimits | None = None) -> None:
        self._limits = limits or BudgetLimits()

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    def check(
        self,
        amount: float,
        daily_spent: float,
        monthly_spent: float,
    ) -> BudgetCheckResult:
        """
        Check if proposed expense is within budget policy.

        Returns:
            BudgetCheckResult with allowed, warnings, cap hits
        """
        warnings: list[str] = []
        hard_cap_hit = False
        soft_cap_hit = False
        requires_approval = False

        new_daily = daily_spent + amount
        new_monthly = monthly_spent + amount

        # Hard cap checks — block
        if new_daily > self._limits.daily_hard_cap:
            hard_cap_hit = True
            warnings.append(
                f"Denný hard cap prekročený: ${new_daily:.2f} > ${self._limits.daily_hard_cap:.2f}"
            )
        if new_monthly > self._limits.monthly_hard_cap:
            hard_cap_hit = True
            warnings.append(
                f"Mesačný hard cap prekročený: ${new_monthly:.2f} > ${self._limits.monthly_hard_cap:.2f}"
            )

        # Soft cap checks — warn but allow
        if not hard_cap_hit:
            if new_daily > self._limits.daily_soft_cap:
                soft_cap_hit = True
                warnings.append(
                    f"Denný soft cap prekročený: ${new_daily:.2f} > ${self._limits.daily_soft_cap:.2f}"
                )
            if new_monthly > self._limits.monthly_soft_cap:
                soft_cap_hit = True
                warnings.append(
                    f"Mesačný soft cap prekročený: ${new_monthly:.2f} > ${self._limits.monthly_soft_cap:.2f}"
                )

        # Single transaction approval cap
        if amount > self._limits.single_tx_approval_cap:
            requires_approval = True
            warnings.append(
                f"Suma ${amount:.2f} > ${self._limits.single_tx_approval_cap:.2f} — vyžaduje extra approval"
            )

        allowed = not hard_cap_hit

        if warnings:
            logger.info("budget_policy_check",
                        amount=amount, allowed=allowed,
                        hard_cap=hard_cap_hit, soft_cap=soft_cap_hit)

        return BudgetCheckResult(
            allowed=allowed,
            warnings=warnings,
            hard_cap_hit=hard_cap_hit,
            soft_cap_hit=soft_cap_hit,
            requires_approval=requires_approval,
            details={
                "amount": amount,
                "daily_spent": daily_spent,
                "monthly_spent": monthly_spent,
                "new_daily_total": round(new_daily, 2),
                "new_monthly_total": round(new_monthly, 2),
            },
        )

    def get_forecast(
        self,
        daily_spent: float,
        monthly_spent: float,
    ) -> dict[str, Any]:
        """
        Budget forecast — how much is left at each level.
        """
        return {
            "daily": {
                "spent": round(daily_spent, 2),
                "soft_remaining": round(self._limits.daily_soft_cap - daily_spent, 2),
                "hard_remaining": round(self._limits.daily_hard_cap - daily_spent, 2),
                "soft_cap": self._limits.daily_soft_cap,
                "hard_cap": self._limits.daily_hard_cap,
            },
            "monthly": {
                "spent": round(monthly_spent, 2),
                "soft_remaining": round(self._limits.monthly_soft_cap - monthly_spent, 2),
                "hard_remaining": round(self._limits.monthly_hard_cap - monthly_spent, 2),
                "soft_cap": self._limits.monthly_soft_cap,
                "hard_cap": self._limits.monthly_hard_cap,
            },
            "single_tx_approval_cap": self._limits.single_tx_approval_cap,
        }
