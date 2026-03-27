"""
Tests for budget policy — multi-level budget enforcement.
"""

from __future__ import annotations

from agent.finance.budget_policy import BudgetCheckResult, BudgetLimits, BudgetPolicy


class TestBudgetPolicy:
    """Budget policy enforces hard cap, soft cap, and approval cap."""

    def test_within_all_caps(self):
        policy = BudgetPolicy(BudgetLimits(daily_hard_cap=50, daily_soft_cap=30, monthly_hard_cap=500))
        result = policy.check(amount=5.0, daily_spent=10.0, monthly_spent=100.0)
        assert result.allowed
        assert not result.hard_cap_hit
        assert not result.soft_cap_hit
        assert len(result.warnings) == 0

    def test_hard_cap_daily_blocks(self):
        policy = BudgetPolicy(BudgetLimits(daily_hard_cap=50))
        result = policy.check(amount=20.0, daily_spent=40.0, monthly_spent=100.0)
        assert not result.allowed
        assert result.hard_cap_hit

    def test_hard_cap_monthly_blocks(self):
        policy = BudgetPolicy(BudgetLimits(monthly_hard_cap=500))
        result = policy.check(amount=50.0, daily_spent=0, monthly_spent=480.0)
        assert not result.allowed
        assert result.hard_cap_hit

    def test_soft_cap_warns_but_allows(self):
        policy = BudgetPolicy(BudgetLimits(daily_soft_cap=30, daily_hard_cap=50))
        result = policy.check(amount=10.0, daily_spent=25.0, monthly_spent=0)
        assert result.allowed
        assert result.soft_cap_hit
        assert len(result.warnings) > 0

    def test_stop_loss_blocks_before_hard_cap(self):
        policy = BudgetPolicy(BudgetLimits(daily_hard_cap=50, daily_stop_loss_buffer=5))
        result = policy.check(amount=6.0, daily_spent=40.0, monthly_spent=0)
        assert not result.allowed
        assert result.stop_loss_hit
        assert not result.hard_cap_hit

    def test_single_tx_approval_cap(self):
        policy = BudgetPolicy(BudgetLimits(single_tx_approval_cap=20))
        result = policy.check(amount=25.0, daily_spent=0, monthly_spent=0)
        assert result.allowed  # Not blocked, but requires approval
        assert result.requires_approval
        assert any("extra approval" in w for w in result.warnings)

    def test_small_tx_no_approval(self):
        policy = BudgetPolicy(BudgetLimits(single_tx_approval_cap=20))
        result = policy.check(amount=5.0, daily_spent=0, monthly_spent=0)
        assert not result.requires_approval

    def test_multiple_warnings(self):
        policy = BudgetPolicy(BudgetLimits(
            daily_soft_cap=10, monthly_soft_cap=50, single_tx_approval_cap=5,
            daily_hard_cap=100, monthly_hard_cap=1000,
        ))
        result = policy.check(amount=15.0, daily_spent=5.0, monthly_spent=45.0)
        assert result.allowed
        assert len(result.warnings) >= 2  # soft cap + approval cap

    def test_to_dict(self):
        result = BudgetCheckResult(allowed=True, warnings=["test"])
        d = result.to_dict()
        assert d["allowed"] is True
        assert "test" in d["warnings"]


class TestBudgetForecast:
    """Budget forecast shows remaining at each level."""

    def test_forecast(self):
        policy = BudgetPolicy(BudgetLimits(
            daily_soft_cap=30, daily_hard_cap=50,
            monthly_soft_cap=300, monthly_hard_cap=500,
            single_tx_approval_cap=20,
        ))
        forecast = policy.get_forecast(daily_spent=15.0, monthly_spent=150.0)
        assert forecast["daily"]["soft_remaining"] == 15.0
        assert forecast["daily"]["hard_remaining"] == 35.0
        assert forecast["daily"]["stop_loss_remaining"] == 30.0
        assert forecast["monthly"]["soft_remaining"] == 150.0
        assert forecast["monthly"]["hard_remaining"] == 350.0
        assert forecast["monthly"]["stop_loss_remaining"] == 300.0
        assert forecast["single_tx_approval_cap"] == 20.0

    def test_forecast_over_soft(self):
        policy = BudgetPolicy(BudgetLimits(daily_soft_cap=30))
        forecast = policy.get_forecast(daily_spent=40.0, monthly_spent=0)
        assert forecast["daily"]["soft_remaining"] < 0  # Over soft cap
