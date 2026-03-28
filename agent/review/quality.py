"""
Agent Life Space — Review Quality Service

Runs deterministic golden cases and turns them into explicit quality signals.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from agent import __version__ as agent_version
from agent.control.models import TraceRecordKind
from agent.review.golden_cases import list_golden_review_cases, seed_golden_review_repo
from agent.review.models import ReviewIntake, ReviewJobType
from agent.review.service import ReviewService
from agent.review.storage import ReviewStorage


class ReviewQualityService:
    """Evaluate deterministic reviewer quality against golden cases."""

    def __init__(self, *, control_plane_state: Any = None) -> None:
        self._control_plane_state = control_plane_state

    async def evaluate_goldens(self, *, release_label: str = "") -> dict[str, Any]:
        """Run the canonical golden cases and return quality telemetry."""
        started = time.perf_counter()
        previous_summary = self._latest_quality_summary()
        cases = []
        exact_matches = 0
        verdict_matches = 0
        count_matches = 0
        title_matches = 0
        false_positive_cases = 0
        false_negative_cases = 0

        for golden in list_golden_review_cases():
            result = await self._run_case(
                golden.case_id,
                expected_title_contains=golden.expected_title_contains,
            )
            case_result = {
                "case_id": golden.case_id,
                "expected_verdict": golden.expected_verdict,
                "actual_verdict": result["actual_verdict"],
                "expected_counts": golden.expected_counts,
                "actual_counts": result["actual_counts"],
                "expected_title_contains": golden.expected_title_contains,
                "title_match": result["title_match"],
            }
            verdict_match = result["actual_verdict"] == golden.expected_verdict
            counts_match = result["actual_counts"] == golden.expected_counts
            title_match = result["title_match"]
            exact_match = verdict_match and counts_match and title_match
            case_result["verdict_match"] = verdict_match
            case_result["counts_match"] = counts_match
            case_result["title_match"] = title_match
            case_result["exact_match"] = exact_match
            cases.append(case_result)

            if verdict_match:
                verdict_matches += 1
            if counts_match:
                count_matches += 1
            if title_match:
                title_matches += 1
            if exact_match:
                exact_matches += 1

            actual_total = sum(result["actual_counts"].values())
            expected_total = sum(golden.expected_counts.values())
            if actual_total > expected_total:
                false_positive_cases += 1
            elif actual_total < expected_total:
                false_negative_cases += 1

        total = len(cases)
        summary = {
            "total_cases": total,
            "exact_case_matches": exact_matches,
            "verdict_matches": verdict_matches,
            "count_matches": count_matches,
            "title_matches": title_matches,
            "false_positive_cases": false_positive_cases,
            "false_negative_cases": false_negative_cases,
            "verdict_accuracy": round(verdict_matches / total, 3) if total else 0.0,
            "count_accuracy": round(count_matches / total, 3) if total else 0.0,
            "title_accuracy": round(title_matches / total, 3) if total else 0.0,
            "exact_match_rate": round(exact_matches / total, 3) if total else 0.0,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "release_label": release_label or self._default_release_label(),
            "cases": cases,
        }
        summary["trend"] = self._build_trend(summary, previous_summary)
        self._record_quality_trace(summary)
        return summary

    async def _run_case(
        self,
        case_id: str,
        *,
        expected_title_contains: str = "",
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix=f"review-quality-{case_id}-") as repo_dir:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
                db_path = handle.name
            repo_path = seed_golden_review_repo(Path(repo_dir), case_id)
            service = ReviewService(storage=ReviewStorage(db_path=db_path))
            try:
                job = await service.run_review(
                    ReviewIntake(
                        repo_path=repo_path,
                        review_type=ReviewJobType.REPO_AUDIT,
                        requester="quality_eval",
                        source="operator",
                    )
                )
            finally:
                Path(db_path).unlink(missing_ok=True)
            return {
                "actual_verdict": job.report.verdict,
                "actual_counts": dict(job.report.finding_counts),
                "title_match": (
                    any(
                        expected_title_contains in finding.title
                        for finding in job.report.findings
                    )
                    if expected_title_contains
                    else True
                ),
            }

    def _record_quality_trace(self, summary: dict[str, Any]) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_trace(
            trace_kind=TraceRecordKind.QUALITY,
            title="Review quality golden evaluation",
            detail=(
                f"exact_matches={summary['exact_case_matches']}/{summary['total_cases']}; "
                f"false_positive_cases={summary['false_positive_cases']}; "
                f"false_negative_cases={summary['false_negative_cases']}"
            ),
            metadata=summary,
        )

    def _latest_quality_summary(self) -> dict[str, Any]:
        if self._control_plane_state is None:
            return {}
        traces = self._control_plane_state.list_traces(
            trace_kind=TraceRecordKind.QUALITY.value,
            limit=1,
        )
        if not traces:
            return {}
        return dict(traces[0].metadata)

    def _default_release_label(self) -> str:
        normalized = (agent_version or "").strip()
        if not normalized:
            return "unknown"
        return normalized if normalized.startswith("v") else f"v{normalized}"

    def _build_trend(
        self,
        current: dict[str, Any],
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        if not previous:
            return {
                "has_baseline": False,
                "previous_release_label": "",
                "regression_detected": False,
                "summary": "No previous quality baseline available.",
            }

        exact_delta = round(
            float(current.get("exact_match_rate", 0.0))
            - float(previous.get("exact_match_rate", 0.0)),
            3,
        )
        verdict_delta = round(
            float(current.get("verdict_accuracy", 0.0))
            - float(previous.get("verdict_accuracy", 0.0)),
            3,
        )
        count_delta = round(
            float(current.get("count_accuracy", 0.0))
            - float(previous.get("count_accuracy", 0.0)),
            3,
        )
        title_delta = round(
            float(current.get("title_accuracy", 0.0))
            - float(previous.get("title_accuracy", 0.0)),
            3,
        )
        false_positive_delta = int(current.get("false_positive_cases", 0)) - int(
            previous.get("false_positive_cases", 0)
        )
        false_negative_delta = int(current.get("false_negative_cases", 0)) - int(
            previous.get("false_negative_cases", 0)
        )
        duration_delta_ms = round(
            float(current.get("duration_ms", 0.0))
            - float(previous.get("duration_ms", 0.0)),
            1,
        )
        regression_detected = any(
            delta < 0
            for delta in (exact_delta, verdict_delta, count_delta, title_delta)
        ) or false_positive_delta > 0 or false_negative_delta > 0
        summary = (
            "Golden review quality regressed versus the previous baseline."
            if regression_detected
            else "Golden review quality is stable or improved versus the previous baseline."
        )
        return {
            "has_baseline": True,
            "previous_release_label": previous.get("release_label", ""),
            "exact_match_rate_delta": exact_delta,
            "verdict_accuracy_delta": verdict_delta,
            "count_accuracy_delta": count_delta,
            "title_accuracy_delta": title_delta,
            "false_positive_cases_delta": false_positive_delta,
            "false_negative_cases_delta": false_negative_delta,
            "duration_ms_delta": duration_delta_ms,
            "regression_detected": regression_detected,
            "summary": summary,
        }
