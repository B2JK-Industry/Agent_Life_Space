"""Tests for Initiative Engine schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.initiative.schemas import (
    InitiativePlan,
    PatternRef,
    PlannedStep,
    StepKind,
)


def _step(idx: int, kind: StepKind = StepKind.ANALYZE, deps=None) -> dict:
    return {
        "idx": idx,
        "kind": kind.value,
        "title": f"step-{idx}",
        "prompt": "Do something useful with enough characters." * 2,
        "depends_on_idx": deps or [],
        "estimated_minutes": 5,
        "requires_approval": False,
        "metadata": {},
    }


def _valid_plan_dict() -> dict:
    return {
        "goal_summary": "Test plan that does X.",
        "pattern": {"pattern_id": "scraper", "confidence": 0.9, "rationale": "fits"},
        "success_criteria": ["produces output", "no crash"],
        "estimated_total_minutes": 30,
        "is_long_running": False,
        "risk_notes": [],
        "steps": [
            _step(0, StepKind.ANALYZE),
            _step(1, StepKind.DESIGN, [0]),
            _step(2, StepKind.CODE, [1]),
            _step(3, StepKind.TEST, [2]),
        ],
    }


def test_valid_plan_validates():
    plan = InitiativePlan.model_validate(_valid_plan_dict())
    assert plan.pattern.pattern_id == "scraper"
    assert len(plan.steps) == 4
    assert plan.steps[2].kind == StepKind.CODE


def test_step_cannot_self_depend():
    bad = _valid_plan_dict()
    bad["steps"][1]["depends_on_idx"] = [1]  # self
    with pytest.raises(ValidationError):
        InitiativePlan.model_validate(bad)


def test_step_cannot_forward_depend():
    bad = _valid_plan_dict()
    bad["steps"][0]["depends_on_idx"] = [3]  # forward
    with pytest.raises(ValidationError):
        InitiativePlan.model_validate(bad)


def test_unknown_dep_idx_rejected():
    bad = _valid_plan_dict()
    bad["steps"][1]["depends_on_idx"] = [99]
    with pytest.raises(ValidationError):
        InitiativePlan.model_validate(bad)


def test_duplicate_step_idx_rejected():
    bad = _valid_plan_dict()
    bad["steps"][1]["idx"] = 0  # duplicate
    with pytest.raises(ValidationError):
        InitiativePlan.model_validate(bad)


def test_empty_steps_rejected():
    bad = _valid_plan_dict()
    bad["steps"] = []
    with pytest.raises(ValidationError):
        InitiativePlan.model_validate(bad)


def test_too_many_steps_rejected():
    bad = _valid_plan_dict()
    bad["steps"] = [_step(i) for i in range(50)]
    with pytest.raises(ValidationError):
        InitiativePlan.model_validate(bad)


def test_pattern_ref_confidence_bounds():
    with pytest.raises(ValidationError):
        PatternRef.model_validate(
            {"pattern_id": "x", "confidence": 1.5, "rationale": ""}
        )


def test_planned_step_short_title_rejected():
    with pytest.raises(ValidationError):
        PlannedStep.model_validate(
            {
                "idx": 0,
                "kind": "code",
                "title": "ab",
                "prompt": "x" * 50,
            }
        )
