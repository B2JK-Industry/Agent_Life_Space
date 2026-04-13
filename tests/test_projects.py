"""
Tests pre agent/projects/manager.py a agent/work/workspace.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.projects.manager import Project, ProjectManager, ProjectStatus
from agent.work.workspace import Workspace, WorkspaceManager, WorkspaceStatus

# --- Project ---


class TestProject:
    def test_defaults(self):
        p = Project(name="test")
        assert p.status == ProjectStatus.IDEA
        assert p.task_ids == []
        assert p.priority == 0.5

    def test_to_from_dict(self):
        p = Project(name="test", tags=["a", "b"], priority=0.8)
        d = p.to_dict()
        p2 = Project.from_dict(d)
        assert p2.name == "test"
        assert p2.tags == ["a", "b"]
        assert p2.priority == 0.8


class TestProjectManager:
    @pytest.mark.asyncio
    async def test_create_and_get(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        p = await pm.create(name="Test Project", tags=["test"])
        assert p.name == "Test Project"
        assert p.status == ProjectStatus.IDEA

        fetched = await pm.get(p.id)
        assert fetched is not None
        assert fetched.name == "Test Project"

        await pm.close()

    @pytest.mark.asyncio
    async def test_start_and_complete(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        p = await pm.create(name="P1")
        await pm.start(p.id)
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.ACTIVE
        assert p.started_at is not None

        await pm.complete(p.id, result="done")
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.COMPLETED
        assert p.result == "done"

        await pm.close()

    @pytest.mark.asyncio
    async def test_add_task(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        p = await pm.create(name="P1")
        await pm.add_task(p.id, "task_123")
        await pm.add_task(p.id, "task_456")
        await pm.add_task(p.id, "task_123")  # Duplicit — ignorovať

        p = await pm.get(p.id)
        assert p.task_ids == ["task_123", "task_456"]

        await pm.close()

    @pytest.mark.asyncio
    async def test_list_by_status(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        await pm.create(name="P1")
        p2 = await pm.create(name="P2")
        await pm.start(p2.id)

        ideas = await pm.list_projects(status=ProjectStatus.IDEA)
        active = await pm.list_projects(status=ProjectStatus.ACTIVE)
        assert len(ideas) == 1
        assert len(active) == 1

        await pm.close()

    @pytest.mark.asyncio
    async def test_abandon(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        p = await pm.create(name="P1")
        await pm.abandon(p.id, reason="too complex")
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.ABANDONED
        assert p.notes == "too complex"

        await pm.close()

    @pytest.mark.asyncio
    async def test_stats(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        await pm.create(name="P1")
        p2 = await pm.create(name="P2")
        await pm.start(p2.id)

        stats = await pm.get_stats()
        assert stats["total_projects"] == 2
        assert "P2" in stats["active"]

        await pm.close()


# --- Workspace ---


class TestWorkspace:
    def test_defaults(self):
        ws = Workspace(name="test")
        assert ws.status == WorkspaceStatus.CREATED
        assert ws.commands_run == []
        assert ws.files_created == []

    def test_to_from_dict(self):
        ws = Workspace(name="test", project_id="p1", task_id="t1")
        d = ws.to_dict()
        ws2 = Workspace.from_dict(d)
        assert ws2.name == "test"
        assert ws2.project_id == "p1"


class TestWorkspaceManager:
    def test_create(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        ws = wm.create(name="test-job")
        assert ws.status == WorkspaceStatus.CREATED
        assert Path(ws.path).exists()

    def test_activate_and_complete(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        ws = wm.create(name="job1")
        wm.activate(ws.id)
        assert wm.get(ws.id).status == WorkspaceStatus.ACTIVE

        wm.complete(ws.id, output="result")
        assert wm.get(ws.id).status == WorkspaceStatus.COMPLETED
        assert wm.get(ws.id).output == "result"

    def test_record_command_and_file(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        ws = wm.create(name="job1")
        wm.record_command(ws.id, "pytest tests/")
        wm.record_command(ws.id, "git status")
        wm.record_file(ws.id, "output.txt")

        ws = wm.get(ws.id)
        assert len(ws.commands_run) == 2
        assert ws.files_created == ["output.txt"]


class TestProjectJobLinkagePersistence:
    """Regression: add_task must persist through reload."""

    @pytest.mark.asyncio
    async def test_add_task_persists_after_reload(self, tmp_path: Path):
        db = str(tmp_path / "projects.db")
        pm = ProjectManager(db_path=db)
        await pm.initialize()

        p = await pm.create(name="Monitoring Project")
        await pm.add_task(p.id, "build-job-001")
        await pm.add_task(p.id, "review-job-002")
        await pm.close()

        # Reload from disk
        pm2 = ProjectManager(db_path=db)
        await pm2.initialize()
        reloaded = await pm2.get(p.id)
        assert reloaded is not None
        assert "build-job-001" in reloaded.task_ids
        assert "review-job-002" in reloaded.task_ids
        await pm2.close()

    @pytest.mark.asyncio
    async def test_add_task_deduplicates(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()
        p = await pm.create(name="Test")
        await pm.add_task(p.id, "job-1")
        await pm.add_task(p.id, "job-1")  # duplicate
        p = await pm.get(p.id)
        assert p.task_ids == ["job-1"]
        await pm.close()


class TestWorkspaceProjectLinkage:
    """Workspace created with project_id can be filtered."""

    def test_create_with_project_id(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "ws"))
        wm.initialize()
        ws = wm.create(name="build-abc", project_id="proj-123")
        assert ws.project_id == "proj-123"

    def test_list_workspaces_by_project(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "ws"))
        wm.initialize()
        wm.create(name="a", project_id="proj-1")
        wm.create(name="b", project_id="proj-2")
        wm.create(name="c", project_id="proj-1")

        filtered = wm.list_workspaces(project_id="proj-1")
        assert len(filtered) == 2
        assert all(w.project_id == "proj-1" for w in filtered)


class TestWorkspaceTTLCleanup:
    """TTL cleanup removes old workspaces without breaking project records."""

    def test_cleanup_expired_removes_old(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "ws"), ttl_hours=0)
        wm.initialize()
        ws = wm.create(name="old-job", project_id="proj-1")
        wm.activate(ws.id)
        wm.complete(ws.id, output="done")

        cleaned = wm.cleanup_expired()
        assert cleaned == 1
        assert wm.get(ws.id).status == WorkspaceStatus.CLEANED

    def test_cleanup_does_not_touch_active(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "ws"), ttl_hours=0)
        wm.initialize()
        ws = wm.create(name="active-job")
        wm.activate(ws.id)

        cleaned = wm.cleanup_expired()
        assert cleaned == 0
        assert wm.get(ws.id).status == WorkspaceStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_cleanup_preserves_project_record(self, tmp_path: Path):
        """After workspace cleanup, project still has the job linked."""
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()
        p = await pm.create(name="Test Project")
        await pm.add_task(p.id, "job-xyz")

        wm = WorkspaceManager(root=str(tmp_path / "ws"), ttl_hours=0)
        wm.initialize()
        ws = wm.create(name="build-xyz", project_id=p.id, task_id="job-xyz")
        wm.activate(ws.id)
        wm.complete(ws.id)
        wm.cleanup_expired()

        # Project record must survive workspace cleanup
        p2 = await pm.get(p.id)
        assert "job-xyz" in p2.task_ids
        await pm.close()


class TestProjectLifecycleCommands:
    """Regression: all /projects subcommands work."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, tmp_path: Path):
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        p = await pm.create(name="Lifecycle Test")
        assert p.status == ProjectStatus.IDEA

        await pm.start(p.id)
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.ACTIVE

        await pm.pause(p.id)
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.PAUSED

        await pm.start(p.id)  # resume
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.ACTIVE

        await pm.complete(p.id, result="All done")
        p = await pm.get(p.id)
        assert p.status == ProjectStatus.COMPLETED
        assert p.result == "All done"

        await pm.close()

    def test_cleanup(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        ws = wm.create(name="job1")
        ws_path = Path(ws.path)
        assert ws_path.exists()

        # Can't cleanup before complete
        assert wm.cleanup(ws.id) is False

        wm.complete(ws.id)
        assert wm.cleanup(ws.id) is True
        assert not ws_path.exists()
        assert wm.get(ws.id).status == WorkspaceStatus.CLEANED

    def test_get_active(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        assert wm.get_active() is None

        ws = wm.create(name="job1")
        wm.activate(ws.id)
        assert wm.get_active().name == "job1"

    def test_stats(self, tmp_path: Path):
        wm = WorkspaceManager(root=str(tmp_path / "workspaces"))
        wm.initialize()

        wm.create(name="j1")
        ws2 = wm.create(name="j2")
        wm.activate(ws2.id)

        stats = wm.get_stats()
        assert stats["total"] == 2
        assert stats["active"] == "j2"


# --- E2E: Intake → Service → Workspace → Project Linkage ---


class TestIntakeProjectIdPropagation:
    """OperatorIntake.project_id flows through to Build/ReviewIntake."""

    def test_to_build_intake_preserves_project_id(self):
        from agent.control.intake import OperatorIntake, OperatorIntakeService

        router = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path=".",
            description="test build",
            project_id="proj-abc",
        )
        build_intake = router.to_build_intake(intake)
        assert build_intake.project_id == "proj-abc"

    def test_to_review_intake_preserves_project_id(self):
        from agent.control.intake import OperatorIntake, OperatorIntakeService

        router = OperatorIntakeService()
        intake = OperatorIntake(
            repo_path=".",
            description="test review",
            project_id="proj-xyz",
        )
        review_intake = router.to_review_intake(intake)
        assert review_intake.project_id == "proj-xyz"

    def test_to_build_intake_empty_project_id(self):
        from agent.control.intake import OperatorIntake, OperatorIntakeService

        router = OperatorIntakeService()
        intake = OperatorIntake(repo_path=".", description="no project")
        build_intake = router.to_build_intake(intake)
        assert build_intake.project_id == ""

    def test_to_review_intake_empty_project_id(self):
        from agent.control.intake import OperatorIntake, OperatorIntakeService

        router = OperatorIntakeService()
        intake = OperatorIntake(repo_path=".", description="no project")
        review_intake = router.to_review_intake(intake)
        assert review_intake.project_id == ""


class TestReviewFlowProjectLinkage:
    """Review service creates workspace with project_id; project gets job linked."""

    @pytest.mark.asyncio
    async def test_review_workspace_carries_project_id(self, tmp_path: Path):
        """ReviewService.run_review creates workspace with correct project_id."""
        from agent.review.models import ReviewIntake, ReviewJobType
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage

        # Seed minimal repo
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "main.py").write_text("x = 1\n")

        wm = WorkspaceManager(root=str(tmp_path / "ws"))
        wm.initialize()

        service = ReviewService(
            storage=ReviewStorage(db_path=str(tmp_path / "reviews.db")),
            workspace_manager=wm,
        )
        intake = ReviewIntake(
            repo_path=str(repo),
            review_type=ReviewJobType.REPO_AUDIT,
            requester="test",
            project_id="proj-review-e2e",
        )
        job = await service.run_review(intake)

        # Workspace must carry project_id
        assert job.workspace_id != ""
        ws = wm.get(job.workspace_id)
        assert ws is not None
        assert ws.project_id == "proj-review-e2e"

    @pytest.mark.asyncio
    async def test_review_flow_links_job_to_project(self, tmp_path: Path):
        """After review completes, project.task_ids contains the job ID."""
        from agent.review.models import ReviewIntake, ReviewJobType
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage

        # Setup project
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()
        project = await pm.create(name="Review E2E")
        await pm.start(project.id)

        # Setup review service
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "hello.py").write_text("print('hello')\n")

        wm = WorkspaceManager(root=str(tmp_path / "ws"))
        wm.initialize()

        service = ReviewService(
            storage=ReviewStorage(db_path=str(tmp_path / "reviews.db")),
            workspace_manager=wm,
        )
        intake = ReviewIntake(
            repo_path=str(repo),
            review_type=ReviewJobType.REPO_AUDIT,
            requester="test",
            project_id=project.id,
        )
        job = await service.run_review(intake)

        # Simulate what agent.py does after job completion
        _proj_id = getattr(intake, "project_id", "")
        if _proj_id:
            await pm.add_task(_proj_id, job.id)

        # Verify linkage
        p = await pm.get(project.id)
        assert job.id in p.task_ids

        # Verify workspace
        ws = wm.get(job.workspace_id)
        assert ws.project_id == project.id
        assert ws.task_id == job.id

        await pm.close()

    @pytest.mark.asyncio
    async def test_review_linkage_persists_after_reload(self, tmp_path: Path):
        """Project-job link survives ProjectManager reload."""
        from agent.review.models import ReviewIntake, ReviewJobType
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage

        db_path = str(tmp_path / "projects.db")

        pm = ProjectManager(db_path=db_path)
        await pm.initialize()
        project = await pm.create(name="Persist E2E")

        # Run review
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("y = 2\n")

        service = ReviewService(
            storage=ReviewStorage(db_path=str(tmp_path / "reviews.db")),
            workspace_manager=WorkspaceManager(root=str(tmp_path / "ws")),
        )
        service._workspace_manager.initialize()
        intake = ReviewIntake(
            repo_path=str(repo),
            review_type=ReviewJobType.REPO_AUDIT,
            requester="test",
            project_id=project.id,
        )
        job = await service.run_review(intake)
        await pm.add_task(project.id, job.id)
        await pm.close()

        # Reload from disk
        pm2 = ProjectManager(db_path=db_path)
        await pm2.initialize()
        reloaded = await pm2.get(project.id)
        assert reloaded is not None
        assert job.id in reloaded.task_ids
        await pm2.close()


class TestBuildIntakeProjectLinkage:
    """Build intake carries project_id to workspace."""

    def test_build_workspace_carries_project_id(self, tmp_path: Path):
        """Workspace created by build-style flow has correct project_id."""
        from agent.build.models import BuildIntake

        wm = WorkspaceManager(root=str(tmp_path / "ws"))
        wm.initialize()

        intake = BuildIntake(
            repo_path=".",
            description="test build",
            project_id="proj-build-e2e",
        )
        # Simulate what BuildService does
        ws = wm.create(
            name="build-test1234",
            task_id="job-test-001",
            project_id=getattr(intake, "project_id", ""),
        )
        wm.activate(ws.id)

        assert ws.project_id == "proj-build-e2e"
        assert ws.task_id == "job-test-001"

    @pytest.mark.asyncio
    async def test_build_flow_links_job_to_project(self, tmp_path: Path):
        """Build intake → workspace → project.add_task simulates full flow."""
        from agent.build.models import BuildIntake

        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()
        project = await pm.create(name="Build E2E")
        await pm.start(project.id)

        intake = BuildIntake(
            repo_path=".",
            description="implement feature",
            project_id=project.id,
        )

        wm = WorkspaceManager(root=str(tmp_path / "ws"))
        wm.initialize()
        ws = wm.create(
            name="build-abc12345",
            task_id="build-job-001",
            project_id=getattr(intake, "project_id", ""),
        )
        wm.activate(ws.id)

        # Simulate agent.py completion handler
        _proj_id = getattr(intake, "project_id", "")
        if _proj_id:
            await pm.add_task(_proj_id, "build-job-001")

        p = await pm.get(project.id)
        assert "build-job-001" in p.task_ids
        assert ws.project_id == project.id

        await pm.close()


class TestPostBuildReviewProjectIdPropagation:
    """Post-build review must carry project_id from the original build job."""

    def test_post_build_review_intake_has_project_id(self):
        """ReviewIntake created for post-build review includes project_id."""
        from agent.build.models import BuildIntake
        from agent.review.models import ReviewIntake, ReviewJobType

        build_intake = BuildIntake(
            repo_path="/tmp/workspace",
            description="test",
            project_id="proj-post-build",
        )

        # Replicate the post-build review creation from build/service.py
        review_intake = ReviewIntake(
            repo_path="/tmp/workspace",
            review_type=ReviewJobType.REPO_AUDIT,
            include_patterns=build_intake.target_files,
            requester=build_intake.requester,
            context=f"Post-build review for build test: {build_intake.description}",
            project_id=getattr(build_intake, "project_id", ""),
        )
        assert review_intake.project_id == "proj-post-build"

    @pytest.mark.asyncio
    async def test_link_jobs_to_project_with_real_build_job(self, tmp_path: Path):
        """_link_jobs_to_project links build + post-build review using real BuildJob."""
        import os
        import tempfile

        from agent.build.models import BuildIntake, BuildJob
        from agent.core.agent import AgentOrchestrator

        with tempfile.TemporaryDirectory() as data_dir:
            for sub in ("memory", "tasks", "logs", "work"):
                os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
            agent = AgentOrchestrator(data_dir=data_dir, watchdog_interval=60.0)
            await agent.initialize()

            project = await agent.projects.create(name="PBR Integration")
            await agent.projects.start(project.id)

            # Real BuildJob with post_build_review_job_id set
            job = BuildJob(
                id="build-real-001",
                intake=BuildIntake(
                    repo_path=".", description="test", project_id=project.id,
                ),
            )
            job.post_build_review_job_id = "review-pbr-real-001"

            await agent._link_jobs_to_project(job.intake, job)

            p = await agent.projects.get(project.id)
            assert "build-real-001" in p.task_ids
            assert "review-pbr-real-001" in p.task_ids
            assert len(p.task_ids) == 2

            await agent.stop()

    @pytest.mark.asyncio
    async def test_link_jobs_no_pbr_only_links_build(self, tmp_path: Path):
        """When post_build_review_job_id is empty, only build job is linked."""
        import os
        import tempfile

        from agent.build.models import BuildIntake, BuildJob
        from agent.core.agent import AgentOrchestrator

        with tempfile.TemporaryDirectory() as data_dir:
            for sub in ("memory", "tasks", "logs", "work"):
                os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
            agent = AgentOrchestrator(data_dir=data_dir, watchdog_interval=60.0)
            await agent.initialize()

            project = await agent.projects.create(name="No PBR")

            job = BuildJob(
                id="build-only-002",
                intake=BuildIntake(
                    repo_path=".", description="test", project_id=project.id,
                ),
            )
            # post_build_review_job_id defaults to ""

            await agent._link_jobs_to_project(job.intake, job)

            p = await agent.projects.get(project.id)
            assert p.task_ids == ["build-only-002"]

            await agent.stop()

    @pytest.mark.asyncio
    async def test_link_jobs_no_project_id_is_noop(self, tmp_path: Path):
        """When intake has no project_id, nothing is linked."""
        import os
        import tempfile

        from agent.build.models import BuildIntake, BuildJob
        from agent.core.agent import AgentOrchestrator

        with tempfile.TemporaryDirectory() as data_dir:
            for sub in ("memory", "tasks", "logs", "work"):
                os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
            agent = AgentOrchestrator(data_dir=data_dir, watchdog_interval=60.0)
            await agent.initialize()

            # No project_id in intake
            job = BuildJob(
                id="build-orphan",
                intake=BuildIntake(repo_path=".", description="test"),
            )
            job.post_build_review_job_id = "review-orphan"

            # Should not raise, should not create anything
            await agent._link_jobs_to_project(job.intake, job)

            # Verify no projects were modified
            all_projects = await agent.projects.list_projects()
            for p in all_projects:
                assert "build-orphan" not in p.task_ids

            await agent.stop()

    @pytest.mark.asyncio
    async def test_first_add_task_exception_does_not_block_pbr_linkage(
        self, tmp_path: Path,
    ):
        """If first add_task raises, post-build review still gets linked."""
        import os
        import tempfile
        from unittest.mock import patch

        from agent.build.models import BuildIntake, BuildJob
        from agent.core.agent import AgentOrchestrator

        with tempfile.TemporaryDirectory() as data_dir:
            for sub in ("memory", "tasks", "logs", "work"):
                os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
            agent = AgentOrchestrator(data_dir=data_dir, watchdog_interval=60.0)
            await agent.initialize()

            project = await agent.projects.create(name="Exception Test")

            job = BuildJob(
                id="build-err-001",
                intake=BuildIntake(
                    repo_path=".", description="test", project_id=project.id,
                ),
            )
            job.post_build_review_job_id = "review-pbr-err-001"

            call_count = 0
            original_add_task = agent.projects.add_task

            async def flaky_add_task(project_id: str, task_id: str):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("Simulated DB failure")
                return await original_add_task(project_id, task_id)

            with patch.object(agent.projects, "add_task", side_effect=flaky_add_task):
                await agent._link_jobs_to_project(job.intake, job)

            assert call_count == 2  # both calls were attempted

            p = await agent.projects.get(project.id)
            # First call raised → build job NOT linked
            assert "build-err-001" not in p.task_ids
            # Second call succeeded → PBR job IS linked
            assert "review-pbr-err-001" in p.task_ids

            await agent.stop()

    @pytest.mark.asyncio
    async def test_post_build_review_linkage_persists_after_reload(self, tmp_path: Path):
        """Both build + post-build review job IDs survive DB reload."""
        db_path = str(tmp_path / "projects.db")

        pm = ProjectManager(db_path=db_path)
        await pm.initialize()
        project = await pm.create(name="PBR Persistence")

        await pm.add_task(project.id, "build-001")
        await pm.add_task(project.id, "review-pbr-001")
        await pm.close()

        pm2 = ProjectManager(db_path=db_path)
        await pm2.initialize()
        reloaded = await pm2.get(project.id)
        assert "build-001" in reloaded.task_ids
        assert "review-pbr-001" in reloaded.task_ids
        await pm2.close()

    @pytest.mark.asyncio
    async def test_post_build_review_workspace_has_project_id(self, tmp_path: Path):
        """Workspace created for post-build review carries project_id."""
        from agent.review.models import ReviewIntake, ReviewJobType
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "main.py").write_text("x = 1\n")

        wm = WorkspaceManager(root=str(tmp_path / "ws"))
        wm.initialize()

        service = ReviewService(
            storage=ReviewStorage(db_path=str(tmp_path / "reviews.db")),
            workspace_manager=wm,
        )
        # Simulate what BuildService._run_post_build_review creates
        review_intake = ReviewIntake(
            repo_path=str(repo),
            review_type=ReviewJobType.REPO_AUDIT,
            requester="test",
            context="Post-build review for build xyz",
            project_id="proj-pbr-ws",
        )
        job = await service.run_review(review_intake)

        ws = wm.get(job.workspace_id)
        assert ws is not None
        assert ws.project_id == "proj-pbr-ws"

    @pytest.mark.asyncio
    async def test_detail_shows_both_build_and_review_jobs(self, tmp_path: Path):
        """Project detail output includes both build and post-build review jobs."""
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        project = await pm.create(name="Both Jobs Detail")
        await pm.add_task(project.id, "build-job-x1y2")
        await pm.add_task(project.id, "review-pbr-a3b4")

        p = await pm.get(project.id)
        lines = []
        if p.task_ids:
            lines.append(f"Linked jobs: {len(p.task_ids)}")
            for tid in p.task_ids[-5:]:
                lines.append(f"  • `{tid[:12]}`")

        detail = "\n".join(lines)
        assert "Linked jobs: 2" in detail
        assert "build-job-x1" in detail
        assert "review-pbr-a" in detail

        await pm.close()


class TestProjectsDetailOutput:
    """Telegram /projects <id> detail reads persisted linkage."""

    @pytest.mark.asyncio
    async def test_detail_shows_linked_jobs(self, tmp_path: Path):
        """Project detail includes linked job count and IDs from persistence."""
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        project = await pm.create(name="Detail Test")
        await pm.add_task(project.id, "build-job-abc1")
        await pm.add_task(project.id, "review-job-xyz2")
        await pm.add_task(project.id, "build-job-def3")

        # Simulate what _cmd_projects does for detail
        p = await pm.get(project.id)
        assert p is not None
        assert len(p.task_ids) == 3

        # Replicate the detail output logic from telegram_handler.py
        lines = []
        if p.task_ids:
            lines.append(f"Linked jobs: {len(p.task_ids)}")
            for tid in p.task_ids[-5:]:
                lines.append(f"  • `{tid[:12]}`")

        detail = "\n".join(lines)
        assert "Linked jobs: 3" in detail
        assert "build-job-ab" in detail  # truncated to 12 chars
        assert "review-job-x" in detail
        assert "build-job-de" in detail

        await pm.close()

    @pytest.mark.asyncio
    async def test_detail_truncates_to_last_5(self, tmp_path: Path):
        """When more than 5 jobs linked, only last 5 are shown."""
        pm = ProjectManager(db_path=str(tmp_path / "projects.db"))
        await pm.initialize()

        project = await pm.create(name="Many Jobs")
        for i in range(8):
            await pm.add_task(project.id, f"job-{i:03d}")

        p = await pm.get(project.id)
        assert len(p.task_ids) == 8

        shown = p.task_ids[-5:]
        assert shown == ["job-003", "job-004", "job-005", "job-006", "job-007"]

        await pm.close()
