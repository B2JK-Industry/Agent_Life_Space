"""Tests for shared control-plane job queries and builder entrypoints."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.build.models import AcceptanceCriterion, BuildIntake, BuildJob
from agent.control.intake import OperatorIntake, OperatorIntakeService
from agent.control.job_queries import JobQueryService
from agent.control.models import JobKind, JobStatus
from agent.control.reporting import OperatorReportService
from agent.core.job_runner import JobRecord
from agent.core.job_runner import JobStatus as RunnerJobStatus
from agent.review.models import (
    ReviewIntake,
    ReviewJob,
    ReviewJobStatus,
    ReviewJobType,
    ReviewReport,
)
from agent.tasks.manager import Task, TaskStatus


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

    def test_list_jobs_includes_operate_runtime_surfaces(self, services):
        build_service, review_service = services

        task = Task(
            id="task-123",
            name="Follow up approval",
            status=TaskStatus.BLOCKED,
            tags=["ops", "approval"],
            requires_approval=True,
            error="Waiting on owner",
        )
        task_manager = MagicMock()
        task_manager.list_tasks.return_value = [task]
        task_manager.get_task.side_effect = (
            lambda job_id: task if job_id == task.id else None
        )

        job_record = JobRecord(
            id="runner-123",
            name="health_check",
            status=RunnerJobStatus.FAILED,
            error="timeout",
            retry_count=1,
        )
        job_runner = MagicMock()
        job_runner.get_recent_jobs.return_value = [job_record]
        job_runner.get_job_status.side_effect = (
            lambda job_id: job_record if job_id == job_record.id else None
        )

        loop = MagicMock()
        loop.get_status.return_value = {"running": True, "queue_size": 2}
        loop.get_queue_snapshot.return_value = [
            {"action_id": "act-1", "status": "queued"}
        ]

        query = JobQueryService(
            build_service=build_service,
            review_service=review_service,
            task_manager=task_manager,
            job_runner=job_runner,
            agent_loop_provider=lambda: loop,
        )

        jobs = query.list_jobs(kind=JobKind.OPERATE, limit=10)
        assert {job.subkind for job in jobs} == {"task", "job_runner", "agent_loop"}

        task_detail = query.get_job("task-123", kind=JobKind.OPERATE)
        runner_detail = query.get_job("runner-123", kind=JobKind.OPERATE)
        loop_detail = query.get_job("agent_loop", kind=JobKind.OPERATE)

        assert task_detail is not None
        assert task_detail.metadata["requires_approval"] is True
        assert runner_detail is not None
        assert runner_detail.metadata["retry_count"] == 1
        assert loop_detail is not None
        assert loop_detail.metadata["status"]["queue_size"] == 2

    def test_operator_report_builds_inbox_from_jobs_and_approvals(self):
        job_query_service = MagicMock()
        job_query_service.list_jobs.return_value = [
            MagicMock(
                to_dict=lambda: {
                    "job_id": "build-1",
                    "job_kind": "build",
                    "status": "blocked",
                    "title": "Ship build",
                    "blocked_reason": "review failed",
                    "outcome": "review=fail",
                }
            ),
            MagicMock(
                to_dict=lambda: {
                    "job_id": "review-1",
                    "job_kind": "review",
                    "status": "completed",
                    "title": "Audit release",
                    "blocked_reason": "",
                    "outcome": "pass",
                }
            ),
        ]
        approval_queue = MagicMock()
        approval_queue.get_pending.return_value = [
            {
                "id": "apr-1",
                "status": "pending",
                "description": "Deliver report",
                "reason": "External delivery",
            }
        ]
        controls = MagicMock()
        controls.get_status.return_value = {"total_disabled": 1}

        report = OperatorReportService(
            job_queries=job_query_service,
            approval_queue=approval_queue,
            operator_controls=controls,
            status_provider=lambda: {"running": False},
        ).get_report(limit=10)

        assert report["summary"]["blocked_jobs"] == 1
        assert report["summary"]["pending_approvals"] == 1
        assert {item["kind"] for item in report["inbox"]} == {
            "approval",
            "job_attention",
        }


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


class TestUnifiedOperatorIntake:
    def test_qualification_routes_diff_to_review(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            diff_spec="main..feature",
            work_type="auto",
            requester="daniel",
        )

        qualification = service.qualify(intake)

        assert qualification.supported is True
        assert qualification.resolved_work_type.value == "review"

    def test_qualification_requires_description_for_build(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
        )

        qualification = service.qualify(intake)

        assert qualification.supported is False
        assert any("description" in item for item in qualification.blockers)

    @pytest.mark.asyncio
    async def test_orchestrator_submit_operator_intake_routes_to_build(
        self, monkeypatch
    ):
        from agent.core.agent import AgentOrchestrator

        agent = AgentOrchestrator(data_dir="agent-test", watchdog_interval=60.0)
        agent._initialized = True
        agent.initialize = AsyncMock()
        agent.run_build_job = AsyncMock(return_value=BuildJob(id="build-789"))
        agent.get_product_job = MagicMock(
            return_value={"job_id": "build-789", "job_kind": "build"}
        )

        result = await agent.submit_operator_intake(
            OperatorIntake(
                repo_path="/tmp/repo",
                work_type="build",
                description="Implement endpoint",
                acceptance_criteria=["Tests pass"],
            )
        )

        assert result["accepted"] is True
        assert result["job_kind"] == "build"
        assert result["job"]["job_id"] == "build-789"

    @pytest.mark.asyncio
    async def test_orchestrator_submit_operator_intake_rejects_git_only(
        self, monkeypatch
    ):
        from agent.core.agent import AgentOrchestrator

        agent = AgentOrchestrator(data_dir="agent-test", watchdog_interval=60.0)
        agent._initialized = True
        agent.initialize = AsyncMock()

        result = await agent.submit_operator_intake(
            OperatorIntake(
                git_url="https://github.com/example/repo.git",
                work_type="auto",
            )
        )

        assert result["accepted"] is False
        assert "git_url intake" in result["error"]
