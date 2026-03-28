from __future__ import annotations

import os
import tempfile

import pytest

from agent.control.models import TraceRecordKind
from agent.control.state import ControlPlaneStateService
from agent.control.storage import ControlPlaneStorage
from agent.review.quality import ReviewQualityService


@pytest.fixture()
def control_plane():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    state = ControlPlaneStateService(ControlPlaneStorage(db_path=db_path))
    state.initialize()
    try:
        yield state
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_review_quality_runs_goldens_and_records_trace(control_plane):
    service = ReviewQualityService(control_plane_state=control_plane)

    summary = await service.evaluate_goldens()

    assert summary["total_cases"] == 3
    assert summary["verdict_matches"] == 3
    assert summary["count_matches"] == 3
    assert summary["title_matches"] == 3
    assert summary["exact_case_matches"] == 3
    assert summary["false_positive_cases"] == 0
    assert summary["false_negative_cases"] == 0
    assert summary["exact_match_rate"] == 1.0
    assert summary["release_label"].startswith("v")
    assert summary["trend"]["has_baseline"] is False
    assert all(case["exact_match"] for case in summary["cases"])

    traces = control_plane.list_traces(trace_kind="quality", limit=10)
    assert len(traces) == 1
    assert traces[0].title == "Review quality golden evaluation"
    assert traces[0].metadata["exact_case_matches"] == 3
    assert traces[0].metadata["title_matches"] == 3
    assert traces[0].metadata["trend"]["has_baseline"] is False


@pytest.mark.asyncio
async def test_review_quality_tracks_trend_against_previous_release(control_plane):
    control_plane.record_trace(
        trace_kind=TraceRecordKind.QUALITY,
        title="Previous review quality golden evaluation",
        detail="previous baseline",
        metadata={
            "release_label": "v1.12.0",
            "exact_match_rate": 0.667,
            "verdict_accuracy": 1.0,
            "count_accuracy": 0.667,
            "title_accuracy": 0.667,
            "false_positive_cases": 1,
            "false_negative_cases": 0,
            "duration_ms": 50.0,
        },
    )
    service = ReviewQualityService(control_plane_state=control_plane)

    summary = await service.evaluate_goldens(release_label="v1.13.0")

    assert summary["trend"]["has_baseline"] is True
    assert summary["trend"]["previous_release_label"] == "v1.12.0"
    assert summary["trend"]["exact_match_rate_delta"] > 0
    assert summary["trend"]["count_accuracy_delta"] > 0
    assert summary["trend"]["title_accuracy_delta"] > 0
    assert summary["trend"]["false_positive_cases_delta"] < 0
    assert summary["trend"]["regression_detected"] is False


@pytest.mark.asyncio
async def test_review_quality_flags_regression(control_plane, monkeypatch):
    control_plane.record_trace(
        trace_kind=TraceRecordKind.QUALITY,
        title="Previous review quality golden evaluation",
        detail="previous baseline",
        metadata={
            "release_label": "v1.12.0",
            "exact_match_rate": 1.0,
            "verdict_accuracy": 1.0,
            "count_accuracy": 1.0,
            "title_accuracy": 1.0,
            "false_positive_cases": 0,
            "false_negative_cases": 0,
            "duration_ms": 50.0,
        },
    )
    service = ReviewQualityService(control_plane_state=control_plane)

    async def fake_run_case(case_id: str, *, expected_title_contains: str = "") -> dict[str, object]:
        if case_id == "clean":
            return {
                "actual_verdict": "fail",
                "actual_counts": {"critical": 1, "high": 0, "medium": 0, "low": 0},
                "title_match": False,
            }
        if case_id == "secret":
            return {
                "actual_verdict": "fail",
                "actual_counts": {"critical": 1, "high": 0, "medium": 0, "low": 0},
                "title_match": True,
            }
        return {
            "actual_verdict": "pass_with_findings",
            "actual_counts": {"critical": 0, "high": 1, "medium": 0, "low": 0},
            "title_match": True,
        }

    monkeypatch.setattr(service, "_run_case", fake_run_case)

    summary = await service.evaluate_goldens(release_label="v1.13.0")

    assert summary["trend"]["has_baseline"] is True
    assert summary["trend"]["regression_detected"] is True
    assert summary["trend"]["exact_match_rate_delta"] < 0
    assert summary["trend"]["title_accuracy_delta"] < 0
