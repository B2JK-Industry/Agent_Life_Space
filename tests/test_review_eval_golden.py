from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from agent.review.golden_cases import list_golden_review_cases, seed_golden_review_repo
from agent.review.models import ReviewIntake, ReviewJobStatus, ReviewJobType
from agent.review.service import ReviewService
from agent.review.storage import ReviewStorage


@pytest.fixture()
def review_service():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    service = ReviewService(storage=ReviewStorage(db_path=db_path))
    yield service
    os.unlink(db_path)

@pytest.mark.parametrize("golden", list_golden_review_cases(), ids=lambda case: case.case_id)
@pytest.mark.asyncio
async def test_review_eval_goldens(
    review_service,
    tmp_path,
    golden,
):
    repo_path = seed_golden_review_repo(Path(tmp_path), golden.case_id)

    job = await review_service.run_review(
        ReviewIntake(
            repo_path=repo_path,
            review_type=ReviewJobType.REPO_AUDIT,
            requester="ci-golden",
            source="operator",
        )
    )

    assert job.status == ReviewJobStatus.COMPLETED
    assert job.report.verdict == golden.expected_verdict
    assert job.report.finding_counts == golden.expected_counts

    bundle = review_service.get_delivery_bundle(job.id)
    assert bundle is not None
    assert bundle["summary_pack"]["verdict"] == golden.expected_verdict
    assert bundle["operator_summary_markdown"].startswith("# Review Handoff Summary")

    if golden.expected_title_contains:
        assert any(
            golden.expected_title_contains in finding.title
            for finding in job.report.findings
        )
    if golden.case_id == "secret":
        assert "sk-abc123456789abcdef" not in job.report.findings[0].evidence
        assert "[REDACTED]" in job.report.findings[0].evidence
