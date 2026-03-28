"""Tests for shared control-plane job queries and builder entrypoints."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.build.models import (
    AcceptanceCriterion,
    BuildArtifact,
    BuildIntake,
    BuildJob,
    BuildOperation,
    BuildOperationType,
)
from agent.control.artifact_queries import ArtifactQueryService
from agent.control.evidence_export import EvidenceExportService
from agent.control.intake import OperatorIntake, OperatorIntakeService
from agent.control.job_queries import JobQueryService
from agent.control.models import ArtifactKind, JobKind, JobStatus, TraceRecordKind
from agent.control.reporting import OperatorReportService
from agent.control.runtime_model import RuntimeModelService
from agent.control.state import ControlPlaneStateService
from agent.control.storage import ControlPlaneStorage
from agent.core.job_runner import JobRecord
from agent.core.job_runner import JobStatus as RunnerJobStatus
from agent.review.models import (
    ArtifactType,
    ReviewArtifact,
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

    def test_list_artifacts_normalizes_build_and_review(self, services):
        build_service, review_service = services

        build_job = BuildJob(requester="builder")
        build_service._storage.save_job(build_job)
        build_artifact = BuildArtifact(
            artifact_kind=ArtifactKind.VERIFICATION_REPORT,
            job_id=build_job.id,
            content_json={"all_passed": True},
            format="json",
        )
        build_service._storage.save_artifact(build_artifact)

        review_job = ReviewJob(
            requester="reviewer",
            intake=ReviewIntake(repo_path="/tmp/repo", context="Audit"),
        )
        review_service._storage.save_job(review_job)
        review_service._storage.save_artifact(
            ReviewArtifact(
                artifact_type=ArtifactType.REVIEW_REPORT,
                job_id=review_job.id,
                content="# Report",
                format="markdown",
            )
        )

        query = ArtifactQueryService(
            build_service=build_service,
            review_service=review_service,
        )
        artifacts = query.list_artifacts(limit=10)

        assert len(artifacts) == 2
        assert {artifact.job_kind for artifact in artifacts} == {
            JobKind.BUILD,
            JobKind.REVIEW,
        }
        assert {artifact.artifact_kind.value for artifact in artifacts} == {
            "verification_report",
            "review_report",
        }

    def test_get_artifact_returns_recoverable_payload(self, services):
        build_service, review_service = services

        build_job = BuildJob(requester="builder")
        build_service._storage.save_job(build_job)
        artifact = BuildArtifact(
            artifact_kind=ArtifactKind.ACCEPTANCE_REPORT,
            job_id=build_job.id,
            content_json={"accepted": True},
            format="json",
        )
        build_service._storage.save_artifact(artifact)

        query = ArtifactQueryService(
            build_service=build_service,
            review_service=review_service,
        )
        detail = query.get_artifact(artifact.id)

        assert detail is not None
        assert detail.job_kind == JobKind.BUILD
        assert detail.content_json["accepted"] is True
        assert detail.metadata["domain"] == "build"

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
                    "metadata": {
                        "denial": {
                            "summary": "Build completion blocked by review gate policy",
                            "detail": "Critical findings exceeded the configured threshold.",
                        }
                    },
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
        artifact_query_service = MagicMock()
        artifact_query_service.list_artifacts.return_value = [
            MagicMock(
                to_dict=lambda: {
                    "artifact_id": "art-1",
                    "artifact_kind": "review_report",
                    "job_id": "review-1",
                    "job_kind": "review",
                }
            )
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
        approval_queue.list_requests.return_value = [
            {
                "id": "apr-1",
                "status": "pending",
                "category": "external",
                "description": "Deliver report",
                "reason": "External delivery",
                "required_approvals": 1,
                "approvals_received": [],
            },
            {
                "id": "apr-2",
                "status": "partially_approved",
                "category": "external",
                "description": "Deliver critical report",
                "reason": "Needs dual approval",
                "required_approvals": 2,
                "approvals_received": ["owner-1"],
            },
            {
                "id": "apr-3",
                "status": "denied",
                "category": "tool",
                "description": "Risky action",
                "reason": "Policy denied",
                "denial_reason": "Policy denied",
                "required_approvals": 1,
                "approvals_received": [],
            },
        ]
        controls = MagicMock()
        controls.get_status.return_value = {"total_disabled": 1}

        report = OperatorReportService(
            job_queries=job_query_service,
            artifact_queries=artifact_query_service,
            approval_queue=approval_queue,
            operator_controls=controls,
            status_provider=lambda: {
                "running": False,
                "workspaces": {
                    "total": 2,
                    "by_status": {"active": 1, "failed": 1},
                    "recent": [{"id": "ws-1", "status": "active"}],
                },
                "worker_execution": {
                    "active_jobs": 1,
                    "recent_jobs": [{"id": "runner-1", "status": "running"}],
                    "circuit_breaker_open": False,
                },
            },
        ).get_report(limit=10)

        assert report["summary"]["blocked_jobs"] == 1
        assert report["summary"]["total_artifacts"] == 1
        assert report["summary"]["pending_approvals"] == 1
        assert report["summary"]["approval_requests_total"] == 3
        assert report["summary"]["partial_approvals"] == 1
        assert report["summary"]["blocked_approval_requests"] == 1
        assert report["summary"]["active_workspaces"] == 1
        assert report["summary"]["active_workers"] == 1
        assert report["approval_backlog"]["by_status"]["partially_approved"] == 1
        assert "awaiting additional approval" in report["approval_backlog"]["blocked_reasons"][0]
        assert any(
            item["kind"] == "job_attention"
            and "Build completion blocked by review gate policy" in item["detail"]
            for item in report["inbox"]
        )
        assert report["workspace_health"]["by_status"]["failed"] == 1
        assert report["worker_execution"]["active_jobs"] == 1
        assert {item["kind"] for item in report["inbox"]} == {
            "approval",
            "job_attention",
            "workspace_attention",
        }

    def test_operator_report_surfaces_budget_posture(self):
        job_query_service = MagicMock()
        job_query_service.list_jobs.return_value = []
        report = OperatorReportService(
            job_queries=job_query_service,
            approval_queue=MagicMock(
                get_pending=MagicMock(return_value=[]),
                list_requests=MagicMock(return_value=[]),
            ),
            operator_controls=MagicMock(
                get_status=MagicMock(return_value={"total_disabled": 0})
            ),
            status_provider=lambda: {
                "finance": {
                    "budget": {
                        "daily_spent": 32.0,
                        "daily_remaining": 18.0,
                        "daily_budget": 50.0,
                        "monthly_spent": 210.0,
                        "monthly_remaining": 290.0,
                        "monthly_budget": 500.0,
                        "within_budget": True,
                        "soft_cap_hit": True,
                        "hard_cap_hit": False,
                        "stop_loss_hit": False,
                        "warnings": ["Denný soft cap prekročený"],
                    }
                },
                "workspaces": {"by_status": {"active": 0}},
                "worker_execution": {
                    "active_jobs": 0,
                    "recent_jobs": [],
                    "circuit_breaker_open": False,
                },
            },
        ).get_report(limit=10)

        assert report["budget_posture"]["soft_cap_hit"] is True
        assert report["summary"]["daily_budget_remaining_usd"] == 18.0
        assert any(item["kind"] == "budget_attention" for item in report["inbox"])


class TestBuilderCliAdapter:
    def test_load_acceptance_criteria_file_supports_strings_and_objects(self, tmp_path):
        from agent import __main__ as agent_main

        criteria_file = tmp_path / "criteria.json"
        criteria_file.write_text(
            json.dumps(
                {
                    "criteria": [
                        {
                            "description": "Config version stamped",
                            "evaluator": "workspace",
                            "metadata": {
                                "path": "config.json",
                                "json_path": "release.version",
                                "expected_value": "1.11.0",
                            },
                        },
                        "optional: docs updated",
                    ]
                }
            ),
            encoding="utf-8",
        )

        criteria = agent_main._load_acceptance_criteria(str(criteria_file))

        assert len(criteria) == 2
        assert criteria[0].metadata["path"] == "config.json"
        assert criteria[1].required is False

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

    @pytest.mark.asyncio
    async def test_run_build_command_carries_structured_implementation_plan(
        self, monkeypatch
    ):
        from agent import __main__ as agent_main

        mock_agent = MagicMock()
        mock_agent.initialize = AsyncMock()
        mock_agent.run_build_job = AsyncMock(return_value=BuildJob(id="build-456"))
        mock_agent.get_product_job.return_value = {
            "job_id": "build-456",
            "job_kind": "build",
            "status": "completed",
        }
        mock_agent.stop = AsyncMock()

        monkeypatch.setattr(agent_main, "AgentOrchestrator", lambda data_dir: mock_agent)

        await agent_main.run_build_command(
            data_dir="agent",
            repo_path="/tmp/repo",
            description="Build via CLI with plan",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.REPLACE_TEXT,
                    path="app.py",
                    match_text="return 1",
                    replacement_text="return 2",
                )
            ],
        )

        intake = mock_agent.run_build_job.await_args.args[0]
        assert len(intake.implementation_plan) == 1
        assert intake.implementation_plan[0].operation_type == BuildOperationType.REPLACE_TEXT


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
        agent.workspaces._root = tmp_path / "workspaces"
        agent.workspaces._db_path = str(tmp_path / "workspaces" / "workspaces.db")
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

    @pytest.mark.asyncio
    async def test_orchestrator_lists_shared_artifacts_and_runtime_model(
        self, tmp_path, monkeypatch
    ):
        from agent.core.agent import AgentOrchestrator

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_text("def main():\n    return 1\n")
        (repo_dir / "tests").mkdir()
        (repo_dir / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")

        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [],
        )

        agent = AgentOrchestrator(data_dir=str(tmp_path / "agent"), watchdog_interval=60.0)
        agent.workspaces._root = tmp_path / "workspaces"
        agent.workspaces._db_path = str(tmp_path / "workspaces" / "workspaces.db")
        await agent.initialize()

        build_job = await agent.run_build_job(
            BuildIntake(
                repo_path=str(repo_dir),
                description="Builder artifact query",
                acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
            )
        )

        artifacts = agent.list_product_artifacts(kind="build", job_id=build_job.id, limit=10)
        detail = agent.get_product_artifact(artifacts[0]["artifact_id"], kind="build")
        runtime_model = agent.get_runtime_model()

        assert artifacts
        assert detail is not None
        assert detail["job_kind"] == "build"
        assert runtime_model["status"] == "explicit_for_current_phase"
        assert any(
            profile["id"] == "operator_controlled"
            for profile in runtime_model["operating_environment_profiles"]
        )
        assert any(
            policy["id"] == "workspace_local_mutation"
            for policy in runtime_model["build_execution_policies"]
        )

        await agent.stop()

    @pytest.mark.asyncio
    async def test_preview_persists_plan_record_and_traces(self, tmp_path):
        from agent.core.agent import AgentOrchestrator

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_text("def main():\n    return 1\n")

        agent = AgentOrchestrator(data_dir=str(tmp_path / "agent"), watchdog_interval=60.0)
        await agent.initialize()

        preview = agent.preview_operator_intake(
            OperatorIntake(
                repo_path=str(repo_dir),
                work_type="build",
                description="Implement endpoint",
                acceptance_criteria=["Tests pass"],
            )
        )

        assert preview["accepted"] is True
        assert preview["plan_record"]["status"] == "preview"
        assert preview["plan_traces"]
        persisted = agent.get_operator_plan(preview["plan_record"]["plan_id"])
        traces = agent.list_execution_traces(
            plan_id=preview["plan_record"]["plan_id"],
            limit=10,
        )

        assert persisted is not None
        assert persisted["plan"]["title"].startswith("Build plan:")
        assert {trace["trace_kind"] for trace in traces} >= {
            "qualification",
            "budget",
            "capability",
            "delivery",
        }

        await agent.stop()

    @pytest.mark.asyncio
    async def test_workspace_and_delivery_queries_link_runtime_records(
        self, tmp_path, monkeypatch
    ):
        from agent.core.agent import AgentOrchestrator

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_text("def main():\n    return 1\n")
        (repo_dir / "tests").mkdir()
        (repo_dir / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")

        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [],
        )

        agent = AgentOrchestrator(data_dir=str(tmp_path / "agent"), watchdog_interval=60.0)
        agent.workspaces._root = tmp_path / "workspaces"
        agent.workspaces._db_path = str(tmp_path / "workspaces" / "workspaces.db")
        await agent.initialize()

        build_job = await agent.run_build_job(
            BuildIntake(
                repo_path=str(repo_dir),
                description="Prepare delivery package",
                acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
            )
        )
        bundle = agent.get_build_delivery_bundle(build_job.id)
        approval = agent.request_build_delivery_approval(build_job.id)
        delivery = agent.get_build_delivery_record(build_job.id)
        workspace = agent.get_workspace_record(build_job.workspace_id)

        assert bundle is not None
        assert delivery is not None
        assert delivery["status"] == "awaiting_approval"
        assert workspace is not None
        assert build_job.id in workspace["job_ids"]
        assert approval["approval_request_id"] in workspace["approval_ids"]
        assert bundle["bundle_id"] in workspace["bundle_ids"]
        assert workspace["artifact_ids"]

        agent.approval_queue.approve(approval["approval_request_id"])
        approved = agent.get_build_delivery_record(build_job.id)
        handed_off = agent.mark_build_delivery_handed_off(
            build_job.id,
            note="Sent to operator",
        )
        report = agent.get_operator_report(limit=10)

        assert approved is not None
        assert approved["status"] == "approved"
        assert handed_off["status"] == "handed_off"
        assert report["recent_deliveries"]
        assert report["recent_workspace_records"]

        await agent.stop()

    @pytest.mark.asyncio
    async def test_orchestrator_persists_jobs_retention_and_cost_records(
        self, tmp_path, monkeypatch
    ):
        from agent.core.agent import AgentOrchestrator

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_text("def main():\n    return 1\n")
        (repo_dir / "tests").mkdir()
        (repo_dir / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")

        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [],
        )

        agent = AgentOrchestrator(data_dir=str(tmp_path / "agent"), watchdog_interval=60.0)
        agent.workspaces._root = tmp_path / "workspaces"
        agent.workspaces._db_path = str(tmp_path / "workspaces" / "workspaces.db")
        await agent.initialize()

        build_job = await agent.run_build_job(
            BuildIntake(
                repo_path=str(repo_dir),
                description="Persist control-plane build state",
                acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
            )
        )
        review_job = await agent.run_review_job(
            ReviewIntake(
                repo_path=str(repo_dir),
                review_type=ReviewJobType.REPO_AUDIT,
                context="Persist control-plane review state",
            )
        )

        persisted_jobs = agent.list_persisted_product_jobs(limit=10)
        persisted_build = agent.get_persisted_product_job(build_job.id)
        persisted_review = agent.get_persisted_product_job(review_job.id)
        build_artifacts = agent.list_product_artifacts(
            kind="build",
            job_id=build_job.id,
            limit=10,
        )
        artifact_detail = agent.get_product_artifact(
            build_artifacts[0]["artifact_id"],
            kind="build",
        )
        retained = agent.list_retained_artifacts(job_id=build_job.id, limit=20)
        bundle = agent.get_build_delivery_bundle(build_job.id)
        retained_bundle = agent.get_retained_artifact(bundle["bundle_id"]) if bundle else None
        costs = agent.list_cost_ledger(limit=20)
        report = agent.get_operator_report(limit=20)

        assert len(persisted_jobs) >= 2
        assert persisted_build is not None
        assert persisted_build["metadata"]["persistence_policy_id"] == "build_persistent"
        assert persisted_review is not None
        assert persisted_review["metadata"]["persistence_policy_id"] == "review_persistent"
        assert artifact_detail is not None
        assert artifact_detail["retention_policy_id"]
        assert retained
        assert any(item["retention_policy_id"] for item in retained)
        assert retained_bundle is not None
        assert retained_bundle["artifact_kind"] == "delivery_bundle"
        assert any(entry["job_id"] == build_job.id for entry in costs)
        assert report["recent_persisted_jobs"]
        assert report["recent_retained_artifacts"]
        assert report["recent_cost_entries"]
        assert report["summary"]["persisted_product_jobs"] >= 2

        await agent.stop()

    @pytest.mark.asyncio
    async def test_orchestrator_gateway_and_quality_signals_flow_into_report(
        self,
        tmp_path,
        monkeypatch,
    ):
        from agent.core.agent import AgentOrchestrator

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_text("def main():\n    return 1\n")
        (repo_dir / "README.md").write_text("# Repo\n")

        agent = AgentOrchestrator(data_dir=str(tmp_path / "agent"), watchdog_interval=60.0)
        agent.workspaces._root = tmp_path / "workspaces"
        agent.workspaces._db_path = str(tmp_path / "workspaces" / "workspaces.db")
        await agent.initialize()

        review_job = await agent.run_review_job(
            ReviewIntake(
                repo_path=str(repo_dir),
                review_type=ReviewJobType.REPO_AUDIT,
                context="Gateway report integration",
            )
        )
        approval = agent.request_review_delivery_approval(review_job.id)
        agent.approval_queue.approve(approval["approval_request_id"], decided_by="owner-1")

        async def request_executor(**_: object) -> dict[str, object]:
            return {
                "status_code": 202,
                "response_json": {
                    "accepted": True,
                    "delivery_id": "report-receipt-1",
                    "status": "accepted",
                },
                "response_text": "accepted",
            }

        agent.gateway._request_executor = request_executor
        monkeypatch.setenv(
            "AGENT_OBOLOS_REVIEW_WEBHOOK_URL",
            "https://obolos.example.test/review",
        )
        monkeypatch.setenv("AGENT_OBOLOS_AUTH_TOKEN", "secret-token")

        send_result = await agent.send_review_delivery_via_gateway(
            review_job.id,
            provider_id="obolos.tech",
            capability_id="review_handoff_v1",
        )
        quality = await agent.evaluate_review_quality(release_label="v1.13.0")
        report = agent.get_operator_report(limit=10)
        cost_entries = agent.list_cost_ledger(job_id=review_job.id, limit=10)
        delivery = agent.get_review_delivery_record(review_job.id)

        assert send_result["ok"] is True
        assert send_result["delivery_record"]["status"] == "handed_off"
        assert send_result["provider_id"] == "obolos.tech"
        assert send_result["route_id"] == "obolos_review_handoff_primary"
        assert send_result["provider_receipt"]["delivery_id"] == "report-receipt-1"
        assert delivery is not None
        assert delivery["status"] == "handed_off"
        assert report["summary"]["recent_gateway_traces"] >= 1
        assert report["summary"]["gateway_routes_configured"] >= 1
        assert report["recent_gateway_traces"]
        assert any(
            trace["title"] == "Gateway delivery succeeded"
            for trace in report["recent_gateway_traces"]
        )
        assert report["latest_review_quality"]["total_cases"] == quality["total_cases"]
        assert (
            report["latest_review_quality"]["exact_case_matches"]
            == quality["exact_case_matches"]
        )
        assert report["review_quality_trend"]["has_baseline"] is False
        assert report["gateway_catalog"]["summary"]["configured_routes"] >= 1
        assert cost_entries
        assert cost_entries[0]["source_type"] == "external_gateway_call"

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
        assert qualification.scope_size in {"small", "medium", "large"}
        assert qualification.risk_level in {"low", "medium", "high"}
        assert qualification.scope_signals

    def test_preview_returns_phase_aware_build_job_plan(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
            description="Implement endpoint",
            acceptance_criteria=["Tests pass"],
            target_files=["src/app.py"],
        )

        preview = service.preview(intake)

        assert preview["accepted"] is True
        assert preview["plan"]["resolved_work_type"] == "build"
        assert preview["plan"]["budget_envelope"] in {"small", "medium", "large"}
        assert preview["plan"]["budget"]["estimated_cost_usd"] > 0
        assert [phase["phase"] for phase in preview["plan"]["phases"]] == [
            "qualify",
            "build",
            "verify",
            "review",
            "deliver",
        ]
        assert any(
            assignment["capability_id"] == "impl_core"
            and assignment["source"] == "build_catalog"
            for assignment in preview["plan"]["capability_assignments"]
        )
        assert any(
            step["title"] == "Verify and evaluate acceptance"
            for step in preview["plan"]["steps"]
        )
        assert any(
            step["phase"] == "verify" for step in preview["plan"]["steps"]
        )

    def test_preview_surfaces_structured_implementation_plan(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
            description="Implement bounded local change",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.REPLACE_TEXT,
                    path="app.py",
                    match_text="return 1",
                    replacement_text="return 2",
                ),
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="docs/notes.md",
                    content="# Notes\n",
                ),
            ],
            acceptance_criteria=["Tests pass"],
            target_files=["app.py", "docs/notes.md"],
        )

        preview = service.preview(intake)

        assert "implementation_ops=2" in preview["plan"]["scope_summary"]
        assert any(
            assignment["metadata"].get("structured_operation_count") == 2
            for assignment in preview["plan"]["capability_assignments"]
            if assignment["phase"] == "build"
        )
        assert any(
            "structured workspace operation" in step["detail"]
            for step in preview["plan"]["steps"]
            if step["phase"] == "build"
        )

    def test_preview_surfaces_structured_acceptance_summary(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
            description="Implement bounded local change",
            acceptance_criteria=[
                {
                    "description": "Config version stamped",
                    "evaluator": "workspace",
                    "metadata": {
                        "path": "config.json",
                        "json_path": "release.version",
                        "expected_value": "1.11.0",
                    },
                },
                "optional: docs updated",
            ],
        )

        preview = service.preview(intake)

        assert preview["plan"]["acceptance_summary"]["total"] == 2
        assert preview["plan"]["acceptance_summary"]["required"] == 1
        assert preview["plan"]["acceptance_summary"]["optional"] == 1
        assert preview["plan"]["acceptance_summary"]["structured"] == 1
        assert preview["plan"]["acceptance_summary"]["by_evaluator"]["workspace"] == 1
        assert "structured=1" in preview["plan"]["scope_summary"]
        assert any(
            assignment["metadata"].get("acceptance_summary", {}).get("structured") == 1
            for assignment in preview["plan"]["capability_assignments"]
            if assignment["phase"] == "build"
        )

    def test_preview_surfaces_builder_operation_mix_and_limits(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
            build_type="implementation",
            description="Apply guarded builder plan",
            target_files=["app.py"],
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.INSERT_AFTER_TEXT,
                    path="app.py",
                    match_text="def main():\n",
                    content="    print('ok')\n",
                ),
                BuildOperation(
                    operation_type=BuildOperationType.DELETE_TEXT,
                    path="app.py",
                    match_text="pass\n",
                ),
            ],
        )

        preview = service.preview(intake)
        build_assignment = next(
            assignment
            for assignment in preview["plan"]["capability_assignments"]
            if assignment["phase"] == "build"
        )

        assert build_assignment["metadata"]["operation_mix"] == {
            "delete_text": 1,
            "insert_after_text": 1,
        }
        assert build_assignment["metadata"]["max_operation_count"] == 20
        assert "insert_after_text" in build_assignment["metadata"]["supported_operation_types"]

    def test_preview_uses_budget_policy_and_provider(self):
        service = OperatorIntakeService(
            budget_status_provider=lambda amount: {
                "daily_spent": 49.0,
                "daily_remaining": 1.0,
                "monthly_spent": 490.0,
                "monthly_remaining": 10.0,
                "within_budget": False,
                "proposed_amount": amount,
            }
        )
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
            build_type="integration",
            description="Implement integration flow",
            acceptance_criteria=[
                "Tests pass",
                "Typecheck passes",
                "Docs updated",
                "Review passes",
            ],
            target_files=[
                "agent/core/agent.py",
                "agent/control/intake.py",
                "agent/control/reporting.py",
                "agent/build/service.py",
                "tests/test_control_plane_jobs.py",
            ],
            focus_areas=["planner", "budget", "routing"],
        )

        preview = service.preview(intake)

        assert preview["plan"]["budget"]["within_budget"] is False
        assert preview["plan"]["budget"]["warnings"]
        assert "Budget hard cap blocks execution" in preview["plan"]["recommended_next_action"]

    def test_preview_returns_review_phases_and_handoff(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            diff_spec="main..feature",
            work_type="auto",
            context="Review scoped changes",
        )

        preview = service.preview(intake)

        assert preview["accepted"] is True
        assert [phase["phase"] for phase in preview["plan"]["phases"]] == [
            "qualify",
            "review",
            "verify",
            "deliver",
        ]
        assert any(
            assignment["capability_id"] == "pr_review_v1"
            for assignment in preview["plan"]["capability_assignments"]
        )

    def test_qualification_requires_description_for_build(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
        )

        qualification = service.qualify(intake)

        assert qualification.supported is False
        assert any("description" in item for item in qualification.blockers)

    def test_to_build_intake_assigns_selected_capability_id(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
            build_type="devops",
            description="Update deployment config",
        )

        build_intake = service.to_build_intake(intake)

        assert build_intake.capability_id == "devops_safe"

    def test_to_build_intake_parses_structured_acceptance_tags(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
            description="Implement endpoint",
            acceptance_criteria=[
                "optional: docs updated",
                "security: no critical findings",
            ],
        )

        build_intake = service.to_build_intake(intake)

        assert build_intake.acceptance_criteria[0].required is False
        assert build_intake.acceptance_criteria[0].description == "docs updated"
        assert build_intake.acceptance_criteria[1].kind.value == "security"

    def test_to_build_intake_preserves_structured_implementation_plan(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
            description="Implement endpoint",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.JSON_SET,
                    path="config.json",
                    json_path=["release", "version"],
                    value="1.10.0",
                )
            ],
        )

        build_intake = service.to_build_intake(intake)

        assert len(build_intake.implementation_plan) == 1
        assert build_intake.implementation_plan[0].operation_type == BuildOperationType.JSON_SET

    def test_to_build_intake_preserves_structured_acceptance_metadata(self):
        service = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path="/tmp/repo",
            work_type="build",
            description="Implement endpoint",
            acceptance_criteria=[
                {
                    "description": "Config version stamped",
                    "evaluator": "workspace",
                    "metadata": {
                        "path": "config.json",
                        "json_path": "release.version",
                        "expected_value": "1.11.0",
                    },
                }
            ],
        )

        build_intake = service.to_build_intake(intake)

        assert len(build_intake.acceptance_criteria) == 1
        assert build_intake.acceptance_criteria[0].evaluator.value == "workspace"
        assert build_intake.acceptance_criteria[0].metadata["path"] == "config.json"

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
        assert result["plan"]["resolved_work_type"] == "build"
        assert result["plan"]["phases"][1]["phase"] == "build"
        assert result["plan"]["capability_assignments"]

    @pytest.mark.asyncio
    async def test_orchestrator_submit_operator_intake_supports_file_git_url(
        self, monkeypatch
    ):
        from agent.core.agent import AgentOrchestrator

        with tempfile.TemporaryDirectory() as repo_dir:
            subprocess.run(  # noqa: S603 - fixed git argv for local test repository
                ["git", "init", repo_dir],
                check=True,
                capture_output=True,
                text=True,
            )
            agent = AgentOrchestrator(data_dir="agent-test", watchdog_interval=60.0)
            agent._initialized = True
            agent.initialize = AsyncMock()
            review_job = ReviewJob(id="review-321")
            review_job.status = ReviewJobStatus.COMPLETED
            agent.run_review_job = AsyncMock(return_value=review_job)
            agent.get_product_job = MagicMock(
                return_value={"job_id": "review-321", "job_kind": "review"}
            )
            result = await agent.submit_operator_intake(
                OperatorIntake(
                    git_url=Path(repo_dir).resolve().as_uri(),
                    work_type="auto",
                )
            )

        assert result["accepted"] is True
        assert result["status"] == "completed"
        assert result["acquisition"]["acquired"] is True
        assert result["acquisition"]["repo_path"]
        agent.run_review_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_orchestrator_submit_operator_intake_blocks_on_stop_loss(self):
        from agent.core.agent import AgentOrchestrator

        agent = AgentOrchestrator(data_dir="agent-test", watchdog_interval=60.0)
        agent._initialized = True
        agent.initialize = AsyncMock()
        agent.run_build_job = AsyncMock()
        agent.intake_router = OperatorIntakeService(
            budget_status_provider=lambda amount: {
                "daily_spent": 34.0,
                "daily_remaining": 16.0,
                "daily_budget": 50.0,
                "monthly_spent": 100.0,
                "monthly_remaining": 400.0,
                "monthly_budget": 500.0,
                "within_budget": True,
            }
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
        assert result["status"] == "blocked"
        assert "stop-loss" in result["error"]
        assert result["denial"]["code"] == "budget_blocked"
        agent.run_build_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_orchestrator_submit_operator_intake_requests_budget_approval(self):
        from agent.core.agent import AgentOrchestrator

        agent = AgentOrchestrator(data_dir="agent-test", watchdog_interval=60.0)
        agent._initialized = True
        agent.initialize = AsyncMock()
        agent.run_build_job = AsyncMock()
        agent.intake_router = OperatorIntakeService(
            budget_status_provider=lambda amount: {
                "daily_spent": 0.0,
                "daily_remaining": 50.0,
                "daily_budget": 50.0,
                "monthly_spent": 0.0,
                "monthly_remaining": 500.0,
                "monthly_budget": 500.0,
                "within_budget": True,
            }
        )

        result = await agent.submit_operator_intake(
            OperatorIntake(
                repo_path="/tmp/repo",
                work_type="build",
                build_type="integration",
                description="Implement integration workflow",
                acceptance_criteria=[
                    "Tests pass",
                    "Typecheck passes",
                    "Docs updated",
                    "Review passes",
                ],
                target_files=[
                    "agent/core/agent.py",
                    "agent/control/intake.py",
                    "agent/control/reporting.py",
                    "agent/build/service.py",
                    "tests/test_control_plane_jobs.py",
                ],
                focus_areas=["planner", "budget", "routing"],
            )
        )

        assert result["status"] == "awaiting_approval"
        assert result["approval_request"]["category"] == "finance"
        assert result["approval_request"]["required_approvals"] == 2
        assert result["denial"]["code"] == "approval_required"
        agent.run_build_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_orchestrator_submit_operator_intake_requests_high_risk_approval(self):
        from agent.core.agent import AgentOrchestrator

        agent = AgentOrchestrator(data_dir="agent-test", watchdog_interval=60.0)
        agent._initialized = True
        agent.initialize = AsyncMock()
        agent.run_build_job = AsyncMock()
        agent.intake_router = OperatorIntakeService(
            budget_status_provider=lambda amount: {
                "daily_spent": 0.0,
                "daily_remaining": 50.0,
                "daily_budget": 50.0,
                "monthly_spent": 0.0,
                "monthly_remaining": 500.0,
                "monthly_budget": 500.0,
                "within_budget": True,
            }
        )

        result = await agent.submit_operator_intake(
            OperatorIntake(
                repo_path="/tmp/repo",
                work_type="build",
                build_type="integration",
                description="Update integration pipeline",
            )
        )

        assert result["status"] == "awaiting_approval"
        assert result["approval_request"]["category"] == "tool"
        assert result["approval_request"]["required_approvals"] == 2
        assert result["denial"]["code"] == "approval_required"
        agent.run_build_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_orchestrator_submit_operator_intake_returns_structured_blocker(self):
        from agent.core.agent import AgentOrchestrator

        agent = AgentOrchestrator(data_dir="agent-test", watchdog_interval=60.0)
        agent._initialized = True
        agent.initialize = AsyncMock()

        result = await agent.submit_operator_intake(
            OperatorIntake(
                repo_path="",
                git_url="",
                work_type="review",
            )
        )

        assert result["accepted"] is False
        assert result["status"] == "blocked"
        assert result["denial"]["code"] == "operator_intake_blocked"


class TestRuntimeModel:
    def test_runtime_model_exposes_coexistence_rules(self):
        model = RuntimeModelService().get_model()

        assert model["status"] == "explicit_for_current_phase"
        surfaces = {item["surface"] for item in model["surfaces"]}
        assert surfaces == {
            "BuildJob",
            "ReviewJob",
            "Task",
            "JobRunner",
            "AgentLoop",
        }
        assert any("BuildJob" in rule or "ReviewJob" in rule for rule in model["convergence_plan"])
        profiles = {item["id"] for item in model["environment_profiles"]}
        assert "review_host_read_only" in profiles
        assert "build_workspace_local" in profiles
        operating_profiles = {item["id"] for item in model["operating_environment_profiles"]}
        assert operating_profiles == {
            "local_owner",
            "operator_controlled",
            "enterprise_hardened",
        }
        build_policies = {item["id"] for item in model["build_execution_policies"]}
        assert "workspace_local_mutation" in build_policies
        gateway_policies = {item["id"] for item in model["external_gateway_policies"]}
        assert gateway_policies == {
            "disabled_by_default",
            "approval_before_gateway",
        }
        approval_gateway_policy = next(
            item
            for item in model["external_gateway_policies"]
            if item["id"] == "approval_before_gateway"
        )
        assert approval_gateway_policy["auth_required"] is True
        assert approval_gateway_policy["allow_network"] is True
        assert approval_gateway_policy["timeout_seconds"] == 12
        assert approval_gateway_policy["max_retries"] == 2
        assert "webhook_json" in approval_gateway_policy["allowed_target_kinds"]
        assert "https" in approval_gateway_policy["allowed_url_schemes"]
        assert approval_gateway_policy["environment_profile_id"] == "external_gateway_send"
        gateway_contracts = {item["id"] for item in model["external_gateway_contracts"]}
        assert "external_capability_gateway_v1" in gateway_contracts
        gateway_contract = next(
            item
            for item in model["external_gateway_contracts"]
            if item["id"] == "external_capability_gateway_v1"
        )
        assert gateway_contract["allow_network"] is True
        assert "provider_context" in gateway_contract["request_fields"]
        assert "target" in gateway_contract["request_fields"]
        assert "delivery_bundle" in gateway_contract["request_fields"]
        assert gateway_contract["supported_target_kinds"] == ["webhook_json"]
        providers = {item["id"] for item in model["external_capability_providers"]}
        assert "obolos.tech" in providers
        routes = {item["route_id"] for item in model["external_capability_routes"]}
        assert {
            "obolos_review_handoff_primary",
            "obolos_build_delivery_primary",
        } <= routes
        review_route = next(
            item
            for item in model["external_capability_routes"]
            if item["route_id"] == "obolos_review_handoff_primary"
        )
        assert review_route["request_mode"] == "obolos_handoff_v1"
        assert review_route["response_mode"] == "obolos_receipt_v1"
        assert review_route["receipt_fields"] == ["delivery_id", "status"]
        data_rules = {item["id"] for item in model["data_handling_rules"]}
        assert {
            "internal_operator_evidence_v1",
            "client_safe_review_handoff_v1",
            "retained_operational_trace_v1",
        } <= data_rules


class _DictRecord:
    def __init__(self, **payload):
        self.__dict__.update(payload)

    def to_dict(self):
        return dict(self.__dict__)


class TestEvidenceExport:
    def test_export_job_links_artifacts_to_retention_approvals_and_workspaces(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            control = ControlPlaneStateService(ControlPlaneStorage(db_path=f.name))
        control.initialize()
        control.record_product_job(
            job_id="build-1",
            job_kind=JobKind.BUILD,
            title="Build delivery",
            status="completed",
            artifact_ids=["artifact-1"],
            duration_ms=123.0,
            retry_count=1,
            failure_count=0,
            metadata={"last_error": ""},
        )
        control.record_retained_artifact(
            record_id="artifact-1",
            artifact_id="artifact-1",
            job_id="build-1",
            job_kind=JobKind.BUILD,
            artifact_kind=ArtifactKind.PATCH,
            source_type="build_artifact",
            title="Patch",
            artifact_format="text",
            content="diff --git a/app.py b/app.py",
        )
        control.record_trace(
            trace_kind=TraceRecordKind.EXECUTION,
            title="Build finished",
            detail="job build-1 completed",
            job_id="build-1",
        )
        control.record_cost_entry(
            job_id="build-1",
            job_kind=JobKind.BUILD,
            title="Build delivery",
            metadata={"source_status": "completed"},
        )

        service = EvidenceExportService(
            job_queries=MagicMock(
                get_job=MagicMock(
                    return_value=_DictRecord(
                        job_id="build-1",
                        job_kind=JobKind.BUILD,
                        status="completed",
                        title="Build delivery",
                    )
                )
            ),
            artifact_queries=MagicMock(
                list_artifacts=MagicMock(
                    return_value=[
                        _DictRecord(
                            artifact_id="artifact-1",
                            artifact_kind="patch",
                            job_id="build-1",
                        )
                    ]
                )
            ),
            control_plane_state=control,
            workspace_queries=MagicMock(
                list_workspaces=MagicMock(
                    return_value=[
                        _DictRecord(
                            workspace_id="ws-1",
                            job_ids=["build-1"],
                            artifact_ids=["artifact-1"],
                            approval_ids=["approval-1"],
                            bundle_ids=["bundle-1"],
                        )
                    ]
                )
            ),
            approval_queue=MagicMock(
                list_requests=MagicMock(
                    return_value=[
                        {
                            "id": "approval-1",
                            "context": {
                                "job_id": "build-1",
                                "artifact_ids": ["artifact-1"],
                            },
                        }
                    ]
                )
            ),
            runtime_model=RuntimeModelService(),
        )

        package = service.export_job("build-1", kind="build")

        assert package["job_id"] == "build-1"
        assert package["summary"]["artifact_count"] == 1
        assert package["artifact_traceability"][0]["retention_record_id"] == "artifact-1"
        assert package["artifact_traceability"][0]["approval_ids"] == ["approval-1"]
        assert package["artifact_traceability"][0]["workspace_ids"] == ["ws-1"]

    def test_client_safe_review_export_redacts_sensitive_content(self):
        service = EvidenceExportService(
            job_queries=MagicMock(
                get_job=MagicMock(
                    return_value=_DictRecord(
                        job_id="review-1",
                        job_kind=JobKind.REVIEW,
                        status="completed",
                        title="Review delivery",
                    )
                )
            ),
            artifact_queries=MagicMock(list_artifacts=MagicMock(return_value=[])),
            control_plane_state=MagicMock(
                get_product_job=MagicMock(return_value=None),
                list_retained_artifacts=MagicMock(return_value=[]),
                list_traces=MagicMock(return_value=[]),
                list_deliveries=MagicMock(return_value=[]),
                list_cost_entries=MagicMock(return_value=[]),
            ),
            review_service=MagicMock(
                get_client_safe_bundle=MagicMock(
                    return_value={
                        "job_id": "review-1",
                        "markdown_report": "Path [PATH_REDACTED]",
                        "findings_only": [],
                        "json_report": {},
                        "export_mode": "client_safe",
                    }
                )
            ),
            approval_queue=MagicMock(
                list_requests=MagicMock(
                    return_value=[
                        {
                            "id": "approval-1",
                            "category": "external",
                            "status": "pending",
                            "description": "Deliver /Users/daniel/report",
                            "reason": "Share with b2jk-client",
                            "required_approvals": 1,
                            "approvals_received": [],
                        }
                    ]
                )
            ),
        )

        package = service.export_job(
            "review-1",
            kind="review",
            export_mode="client_safe",
        )

        assert package["export_mode"] == "client_safe"
        assert package["client_safe_bundle"]["export_mode"] == "client_safe"
        assert "/Users/" not in package["approvals"][0]["description"]
        assert "b2jk" not in package["approvals"][0]["reason"]
