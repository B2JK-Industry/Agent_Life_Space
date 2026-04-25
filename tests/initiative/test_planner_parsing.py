"""Tests for planner JSON extraction (no LLM call)."""

from __future__ import annotations

import pytest

from agent.initiative.planner import _extract_json


def test_extract_raw_json():
    txt = '{"goal_summary": "x", "pattern": {"pattern_id": "scraper"}}'
    out = _extract_json(txt)
    assert out["goal_summary"] == "x"


def test_extract_fenced_json():
    txt = "thinking out loud\n```json\n{\"a\": 1}\n```\nfinal"
    out = _extract_json(txt)
    assert out["a"] == 1


def test_extract_fenced_no_lang():
    txt = "```\n{\"a\": 2}\n```"
    out = _extract_json(txt)
    assert out["a"] == 2


def test_extract_embedded_json():
    txt = "Here is the plan: {\"a\": 3, \"b\": [1,2]} done"
    out = _extract_json(txt)
    assert out["a"] == 3
    assert out["b"] == [1, 2]


def test_extract_raises_when_no_json():
    with pytest.raises(ValueError):
        _extract_json("no json here at all")
