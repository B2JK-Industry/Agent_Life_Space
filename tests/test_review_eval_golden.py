from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

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


def _seed_repo(tmp_path: Path, variant: str) -> str:
    (tmp_path / "README.md").write_text("# Golden Repo\n")
    (tmp_path / "app.py").write_text("def run():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\njobs:\n  test:\n    steps:\n      - run: pytest -q\n"
    )

    if variant == "secret":
        (tmp_path / "config.py").write_text('API_KEY = "sk-abc123456789abcdef"\n')
    elif variant == "eval":
        (tmp_path / "danger.py").write_text("result = eval(user_input)\n")

    return str(tmp_path)


@pytest.mark.parametrize(
    ("variant", "expected_verdict", "expected_counts", "expected_title"),
    [
        ("clean", "pass", {"critical": 0, "high": 0, "medium": 0, "low": 0}, ""),
        (
            "secret",
            "fail",
            {"critical": 1, "high": 0, "medium": 0, "low": 0},
            "Potential secret:",
        ),
        (
            "eval",
            "pass_with_findings",
            {"critical": 0, "high": 1, "medium": 0, "low": 0},
            "eval() usage",
        ),
    ],
)
@pytest.mark.asyncio
async def test_review_eval_goldens(
    review_service,
    tmp_path,
    variant,
    expected_verdict,
    expected_counts,
    expected_title,
):
    repo_path = _seed_repo(tmp_path, variant)

    job = await review_service.run_review(
        ReviewIntake(
            repo_path=repo_path,
            review_type=ReviewJobType.REPO_AUDIT,
            requester="ci-golden",
            source="operator",
        )
    )

    assert job.status == ReviewJobStatus.COMPLETED
    assert job.report.verdict == expected_verdict
    assert job.report.finding_counts == expected_counts

    bundle = review_service.get_delivery_bundle(job.id)
    assert bundle is not None
    assert bundle["summary_pack"]["verdict"] == expected_verdict
    assert bundle["operator_summary_markdown"].startswith("# Review Handoff Summary")

    if expected_title:
        assert any(expected_title in finding.title for finding in job.report.findings)
    if variant == "secret":
        assert "sk-abc123456789abcdef" not in job.report.findings[0].evidence
        assert "[REDACTED]" in job.report.findings[0].evidence
