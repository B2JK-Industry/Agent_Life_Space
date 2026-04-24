"""Tests for per-kind turn budget + max_turns retry escalation."""

from __future__ import annotations

from agent.initiative.executor import (
    _MAX_TURNS_MARKERS,
    _is_max_turns_error,
    StepExecutor,
)
from agent.initiative.schemas import StepKind


def _executor() -> StepExecutor:
    return StepExecutor(
        provider=None,
        agent_name="t",
        project_root="/tmp",
        data_root="/tmp",
        executor_max_turns=6,
    )


def test_turns_for_analyze_default_attempt1():
    e = _executor()
    assert e._turns_for(StepKind.ANALYZE, attempt=1) == 20


def test_turns_for_analyze_attempt2_boost():
    e = _executor()
    # 20 * 1.5 = 30
    assert e._turns_for(StepKind.ANALYZE, attempt=2) == 30


def test_turns_for_analyze_attempt3_capped():
    e = _executor()
    # 20 * 1.5^2 = 45 → capped at 40
    assert e._turns_for(StepKind.ANALYZE, attempt=3) == 40


def test_turns_for_notify_minimal():
    e = _executor()
    assert e._turns_for(StepKind.NOTIFY, attempt=1) == 1


def test_turns_for_code_default():
    e = _executor()
    assert e._turns_for(StepKind.CODE, attempt=1) == 18


def test_max_turns_error_detection_explicit():
    assert _is_max_turns_error("error_max_turns reached")


def test_max_turns_error_detection_substring():
    assert _is_max_turns_error("LLM hit MAX TURNS limit")


def test_max_turns_error_detection_negative():
    assert not _is_max_turns_error("connection refused")
    assert not _is_max_turns_error("")
    assert not _is_max_turns_error(None or "")


def test_max_turns_markers_list_complete():
    """Sanity: error_max_turns string ktorý prišiel zo serveru je detected."""
    server_error = (
        '{"type":"result","subtype":"error_max_turns","duration_ms":88785,'
        '"is_error":true,"num_turns":7,"stop_reason":"tool_use"}'
    )
    assert _is_max_turns_error(server_error)
