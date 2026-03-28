from __future__ import annotations

import os
import subprocess
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


@pytest.fixture()
def git_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(
            ["git", "init"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            text=True,
        )

        (Path(tmpdir) / "app.py").write_text("def run():\n    return 1\n")
        subprocess.run(
            ["git", "add", "."],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            text=True,
        )

        (Path(tmpdir) / "app.py").write_text(
            "def run():\n    return 42\n\n\ndef helper():\n    return 'ok'\n"
        )
        (Path(tmpdir) / "README.md").write_text("# Smoke Repo\n")
        subprocess.run(
            ["git", "add", "."],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "update"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            text=True,
        )
        yield tmpdir


@pytest.mark.asyncio
async def test_review_eval_smoke_generates_handoff_artifacts(review_service, git_repo):
    job = await review_service.run_review(
        ReviewIntake(
            repo_path=git_repo,
            review_type=ReviewJobType.PR_REVIEW,
            diff_spec="HEAD~1..HEAD",
            requester="ci-smoke",
            source="operator",
        )
    )

    assert job.status == ReviewJobStatus.COMPLETED
    bundle = review_service.get_delivery_bundle(job.id)
    assert bundle is not None
    assert bundle["operator_summary_markdown"].startswith("# Review Handoff Summary")
    assert bundle["pr_comment_markdown"].startswith("## ALS Review Summary")
    assert bundle["summary_pack"]["verdict"] in {"pass", "pass_with_findings", "fail"}
    assert bundle["payload"]["summary_artifact_ids"]

    summary_artifacts = [
        artifact for artifact in job.artifacts if artifact.artifact_type.value == "executive_summary"
    ]
    assert len(summary_artifacts) == 2


@pytest.mark.asyncio
async def test_review_eval_smoke_client_safe_redacts_handoff_fields(review_service):
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "app.py").write_text("def run():\n    return 1\n")
        job = await review_service.run_review(
            ReviewIntake(
                repo_path=tmpdir,
                requester="ci-smoke",
                source="operator",
            )
        )

        bundle = review_service.get_client_safe_bundle(job.id)

    assert bundle is not None
    assert bundle["export_mode"] == "client_safe"
    assert "/tmp/" not in bundle["operator_summary_markdown"]
    assert "/tmp/" not in bundle["pr_comment_markdown"]
    assert "/tmp/" not in bundle["summary_pack"]["scope_description"]
