"""Tests for shared control-plane job queries and builder entrypoints."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.build.models import AcceptanceCriterion, BuildIntake, BuildJob
from agent.control.job_queries import JobQueryService
from agent.control.models import JobKind, JobStatus
from agent.review.models import (
    ReviewIntake,
    ReviewJob,
    ReviewJobStatus,
    ReviewJobType,
    ReviewReport,
)


class TestJobQueryService:
    @pytest.fixture()
    def services(self):
        from agent.build.service import BuildService
        from agent.build.storage import BuildStorage
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as build_db, tempfile.NamedTemporaryFile(
            suffix=".db", delete=False
        ) as review_db:
            build_service = BuildService(
                storage=BuildStorage(db_path=build_db.name),
                workspace_manager=MagicMock(),
            )
            review_service = ReviewService(
                storage=ReviewStorage(db_path=review_db.name),
            )
            build_service.initialize()
            review_service.initialize()
            yield build_service, review_service

    def test_list_jobs_normalizes_build_and_review(self, services):
        build_service, review_service = services

        build_job = BuildJob(requester="builder")
        build_job.intake = BuildIntake(
            repo_path="/tmp/repo",
            description="Implement endpoint",
            acceptance_criteria=[AcceptanceCriterion(description="Tests pass")],
        )
        build_job.status = JobStatus.COMPLETED
        build_job.acceptance.accepted = True
        build_service._storage.save_job(build_job)

        review_job = ReviewJob(
            requester="reviewer",
            intake=ReviewIntake(
                repo_path="/tmp/repo",
                review_type=ReviewJobType.REPO_AUDIT,
                context="Audit release candidate",
            ),
        )
        review_job.report = ReviewReport(
            executive_summary="No critical findings.",
            verdict="pass_with_findings",
        )
        review_job.status = ReviewJobStatus.COMPLETED
        review_service._storage.save_job(review_job)

        query = JobQueryService(build_service=build_service, review_service=review_service)
        jobs = query.list_jobs(limit=10)

        assert len(jobs) == 2
        assert {job.job_kind for job in jobs} == {JobKind.BUILD, JobKind.REVIEW}
        assert any(job.title == "Implement endpoint" for job in jobs)
        assert any(job.title == "Audit release candidate" for job in jobs)

    def test_get_job_returns_normalized_detail(self, services):
        build_service, review_service = services

        build_job = BuildJob(requester="builder")
        build_job.intake = BuildIntake(
            repo_path="/tmp/repo",
            description="Implement endpoint",
            run_post_build_review=True,
        )
        build_job.status = JobStatus.BLOCKED
        build_job.error = "Post-build review blocked completion"
        build_job.post_build_review_job_id = "review-123"
        build_job.post_build_review_verdict = "fail"
        build_job.post_build_review_findings = {"critical": 1}
        build_service._storage.save_job(build_job)

        query = JobQueryService(build_service=build_service, review_service=review_service)
        detail = query.get_job(build_job.id)

        assert detail is not None
        assert detail.job_kind == JobKind.BUILD
        assert detail.status == "blocked"
        assert detail.metadata["post_build_review"]["verdict"] == "fail"
        assert detail.blocked_reason == "Post-build review blocked completion"


class TestBuilderCliAdapter:
    @pytest.mark.asyncio
    async def test_run_build_command_uses_orchestrator_entrypoint(
        self, monkeypatch, capsys
    ):
        from agent import __main__ as agent_main

        mock_agent = MagicMock()
        mock_agent.initialize = AsyncMock()
        mock_agent.run_build_job = AsyncMock(return_value=BuildJob(id="build-123"))
        mock_agent.get_product_job.return_value = {
            "job_id": "build-123",
            "job_kind": "build",
            "status": "completed",
        }
        mock_agent.stop = AsyncMock()

        monkeypatch.setattr(agent_main, "AgentOrchestrator", lambda data_dir: mock_agent)

        await agent_main.run_build_command(
            data_dir="agent",
            repo_path="/tmp/repo",
            description="Build via CLI",
            target_files=["app.py"],
            acceptance_criteria=["Tests pass"],
        )

        mock_agent.run_build_job.assert_awaited_once()
        intake = mock_agent.run_build_job.await_args.args[0]
        assert intake.repo_path == "/tmp/repo"
        assert intake.run_post_build_review is True
        assert intake.acceptance_criteria[0].description == "Tests pass"
        assert '"job_id": "build-123"' in capsys.readouterr().out


class TestOrchestratorEntryPoints:
    @pytest.mark.asyncio
    async def test_orchestrator_lists_cross_system_jobs(self, tmp_path, monkeypatch):
        from agent.core.agent import AgentOrchestrator

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_text("def main():\n    return 1\n")
        (repo_dir / "README.md").write_text("# Repo\n")
        (repo_dir / "tests").mkdir()
        (repo_dir / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")
        (repo_dir / ".github" / "workflows").mkdir(parents=True)
        (repo_dir / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")

        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [],
        )

        agent = AgentOrchestrator(data_dir=str(tmp_path / "agent"), watchdog_interval=60.0)
        await agent.initialize()

        build_job = await agent.run_build_job(
            BuildIntake(
                repo_path=str(repo_dir),
                description="Builder entrypoint",
                acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
                run_post_build_review=True,
            )
        )
        review_job = await agent.run_review_job(
            ReviewIntake(
                repo_path=str(repo_dir),
                review_type=ReviewJobType.REPO_AUDIT,
                context="Standalone review",
            )
        )

        jobs = agent.list_product_jobs(limit=10)
        loaded_build = agent.get_product_job(build_job.id)
        loaded_review = agent.get_product_job(review_job.id)

        assert len(jobs) >= 2
        assert any(job["job_kind"] == "build" for job in jobs)
        assert any(job["job_kind"] == "review" for job in jobs)
        assert loaded_build is not None
        assert loaded_build["metadata"]["post_build_review"]["requested"] is True
        assert loaded_review is not None
        assert loaded_review["job_kind"] == "review"

        await agent.stop()
