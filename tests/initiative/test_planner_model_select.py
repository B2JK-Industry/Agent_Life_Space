"""Tests for planner UltraPlan-style model selection."""

from __future__ import annotations

from agent.initiative.planner import InitiativePlanner


def _planner() -> InitiativePlanner:
    return InitiativePlanner(
        provider=None,
        agent_name="test",
        owner_name="test",
        project_root="/tmp",
        data_root="/tmp",
        planner_model_id="claude-sonnet-4-6",
    )


def test_short_goal_uses_default():
    p = _planner()
    model, turns, timeout = p._select_model("urob mi malú appku")
    assert model == "claude-sonnet-4-6"
    assert turns == 1


def test_long_goal_uses_opus():
    p = _planner()
    long_goal = "x" * 500
    model, turns, timeout = p._select_model(long_goal)
    assert "opus" in model
    assert turns == 2
    assert timeout == 300


def test_explicit_ultraplan_keyword():
    p = _planner()
    model, _, _ = p._select_model("toto je veľký projekt potrebujem ultraplan")
    assert "opus" in model


def test_long_running_keyword_triggers_opus():
    p = _planner()
    model, _, _ = p._select_model("monitoring scraper long running over weeks")
    assert "opus" in model
