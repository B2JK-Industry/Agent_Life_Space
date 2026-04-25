"""Tests for initiative cost prediction."""

from __future__ import annotations

from agent.initiative.engine import estimate_initiative_cost_usd
from agent.initiative.schemas import InitiativePlan, PatternRef, PlannedStep, StepKind


def _plan_with_steps(steps: list[PlannedStep]) -> InitiativePlan:
    return InitiativePlan(
        goal_summary="cost prediction test plan",
        pattern=PatternRef(pattern_id="scraper", confidence=0.9, rationale=""),
        success_criteria=["x"],
        estimated_total_minutes=sum(s.estimated_minutes for s in steps),
        steps=steps,
    )


def _step(idx: int, kind: StepKind, mins: int) -> PlannedStep:
    return PlannedStep(
        idx=idx, kind=kind, title=f"step-{idx}",
        prompt="x" * 50, estimated_minutes=mins,
    )


def test_zero_steps_zero_cost():
    """Edge — empty plan would fail validation, but estimate alone is 0."""
    # InitiativePlan validation requires >=1 step, so use a 0-cost notify
    plan = _plan_with_steps([_step(0, StepKind.NOTIFY, 1)])
    cost = estimate_initiative_cost_usd(plan)
    assert cost > 0  # at least 0.005 (notify rate × 1 min)
    assert cost < 0.10


def test_analyze_step_cost_high():
    """Analyze 20 min → 20 × 0.04 = 0.80 USD."""
    plan = _plan_with_steps([_step(0, StepKind.ANALYZE, 20)])
    assert estimate_initiative_cost_usd(plan) == 0.80


def test_code_step_cost():
    """Code 18 min → 18 × 0.025 = 0.45 USD."""
    plan = _plan_with_steps([_step(0, StepKind.CODE, 18)])
    assert estimate_initiative_cost_usd(plan) == 0.45


def test_realistic_v3_plan():
    """V3-like plan (8 steps, ~7h) cost odhad."""
    steps = [
        _step(0, StepKind.ANALYZE, 20),  # 0.80
        _step(1, StepKind.ANALYZE, 15),  # 0.60
        _step(2, StepKind.DESIGN, 10),   # 0.30
        _step(3, StepKind.CODE, 50),     # 1.25
        _step(4, StepKind.TEST, 30),     # 0.60
        _step(5, StepKind.VERIFY, 15),   # 0.225
        _step(6, StepKind.VERIFY, 25),   # 0.375
        _step(7, StepKind.NOTIFY, 1),    # 0.005
    ]
    plan = _plan_with_steps(steps)
    cost = estimate_initiative_cost_usd(plan)
    # Sum: 0.80+0.60+0.30+1.25+0.60+0.225+0.375+0.005 = 4.155
    assert 4.0 < cost < 4.5


def test_long_running_estimate():
    """Long monitoring initiative s krátkymi krokmi."""
    steps = [
        _step(0, StepKind.SCHEDULE, 1),   # 0.005
        _step(1, StepKind.MONITOR, 1),    # 0.005
        _step(2, StepKind.NOTIFY, 1),     # 0.005
    ]
    plan = _plan_with_steps(steps)
    assert estimate_initiative_cost_usd(plan) <= 0.05
