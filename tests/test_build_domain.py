"""Tests for builder bounded context (agent.build)."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.build.models import (
    AcceptanceCriterion,
    AcceptanceVerdict,
    BuildArtifact,
    BuildIntake,
    BuildJob,
    BuildJobType,
    CriterionKind,
    CriterionStatus,
    VerificationKind,
    VerificationResult,
)
from agent.control.models import (
    ArtifactKind,
    ExecutionMode,
    JobKind,
    JobStatus,
)
from agent.review.models import (
    ReviewFinding,
    ReviewJob,
    ReviewReport,
    Severity,
)

# ─────────────────────────────────────────────
# Acceptance Criteria
# ─────────────────────────────────────────────

class TestAcceptanceCriteria:
    def test_criterion_creation(self):
        c = AcceptanceCriterion(description="Tests pass", kind=CriterionKind.QUALITY)
        assert c.status == CriterionStatus.PENDING
        assert c.description == "Tests pass"

    def test_criterion_meet(self):
        c = AcceptanceCriterion(description="Tests pass")
        c.meet("All 42 tests passed")
        assert c.status == CriterionStatus.MET
        assert "42" in c.evidence

    def test_criterion_fail(self):
        c = AcceptanceCriterion(description="Tests pass")
        c.fail("3 tests failed")
        assert c.status == CriterionStatus.UNMET

    def test_criterion_skip(self):
        c = AcceptanceCriterion(description="Deploy")
        c.skip("Not applicable in this context")
        assert c.status == CriterionStatus.SKIPPED

    def test_criterion_roundtrip(self):
        c = AcceptanceCriterion(description="Tests pass", kind=CriterionKind.SECURITY)
        c.meet("passed")
        d = c.to_dict()
        c2 = AcceptanceCriterion.from_dict(d)
        assert c2.description == "Tests pass"
        assert c2.kind == CriterionKind.SECURITY
        assert c2.status == CriterionStatus.MET


class TestAcceptanceVerdict:
    def test_empty_verdict_not_accepted(self):
        v = AcceptanceVerdict()
        v.evaluate()
        assert v.accepted is False

    def test_all_met_accepted(self):
        v = AcceptanceVerdict(criteria=[
            AcceptanceCriterion(description="A"),
            AcceptanceCriterion(description="B"),
        ])
        for c in v.criteria:
            c.meet("ok")
        v.evaluate()
        assert v.accepted is True
        assert v.met_count == 2
        assert v.unmet_count == 0

    def test_one_unmet_rejected(self):
        v = AcceptanceVerdict(criteria=[
            AcceptanceCriterion(description="A"),
            AcceptanceCriterion(description="B"),
        ])
        v.criteria[0].meet("ok")
        v.criteria[1].fail("nope")
        v.evaluate()
        assert v.accepted is False
        assert v.unmet_count == 1

    def test_skipped_ignored(self):
        v = AcceptanceVerdict(criteria=[
            AcceptanceCriterion(description="A"),
            AcceptanceCriterion(description="B"),
        ])
        v.criteria[0].meet("ok")
        v.criteria[1].skip("not applicable")
        v.evaluate()
        assert v.accepted is True

    def test_all_skipped_not_accepted(self):
        v = AcceptanceVerdict(criteria=[
            AcceptanceCriterion(description="A"),
        ])
        v.criteria[0].skip("skip")
        v.evaluate()
        assert v.accepted is False

    def test_verdict_roundtrip(self):
        v = AcceptanceVerdict(criteria=[
            AcceptanceCriterion(description="Tests pass"),
        ])
        v.criteria[0].meet("passed")
        v.evaluate()
        d = v.to_dict()
        v2 = AcceptanceVerdict.from_dict(d)
        assert v2.accepted is True
        assert v2.criteria[0].description == "Tests pass"
        assert v2.evaluated_at != ""


# ─────────────────────────────────────────────
# Build Intake
# ─────────────────────────────────────────────

class TestBuildIntake:
    def test_valid_intake(self):
        intake = BuildIntake(
            repo_path="/tmp/test",
            description="Add feature X",
        )
        assert intake.validate() == []

    def test_missing_repo_path(self):
        intake = BuildIntake(description="Add X")
        errors = intake.validate()
        assert any("repo_path" in e for e in errors)

    def test_missing_description(self):
        intake = BuildIntake(repo_path="/tmp/test")
        errors = intake.validate()
        assert any("description" in e for e in errors)

    def test_path_traversal_blocked(self):
        intake = BuildIntake(repo_path="/tmp/../etc/passwd", description="X")
        errors = intake.validate()
        assert any(".." in e for e in errors)

    def test_intake_with_criteria(self):
        intake = BuildIntake(
            repo_path="/tmp/test",
            description="Add feature",
            acceptance_criteria=[
                AcceptanceCriterion(description="Tests pass"),
                AcceptanceCriterion(description="Lint clean"),
            ],
        )
        assert len(intake.acceptance_criteria) == 2

    def test_intake_roundtrip(self):
        intake = BuildIntake(
            repo_path="/tmp/test",
            build_type=BuildJobType.INTEGRATION,
            description="Add API endpoint",
            target_files=["src/api.py"],
            acceptance_criteria=[AcceptanceCriterion(description="Tests pass")],
            run_post_build_review=True,
            requester="daniel",
        )
        d = intake.to_dict()
        intake2 = BuildIntake.from_dict(d)
        assert intake2.repo_path == "/tmp/test"
        assert intake2.build_type == BuildJobType.INTEGRATION
        assert len(intake2.acceptance_criteria) == 1
        assert intake2.run_post_build_review is True


# ─────────────────────────────────────────────
# Verification Result
# ─────────────────────────────────────────────

class TestVerificationResult:
    def test_result_creation(self):
        r = VerificationResult(
            kind=VerificationKind.TEST,
            passed=True,
            command="pytest",
            exit_code=0,
        )
        assert r.passed is True

    def test_result_roundtrip(self):
        r = VerificationResult(
            kind=VerificationKind.LINT,
            passed=False,
            command="ruff check .",
            exit_code=1,
            duration_ms=500.0,
        )
        d = r.to_dict()
        r2 = VerificationResult.from_dict(d)
        assert r2.kind == VerificationKind.LINT
        assert r2.passed is False


# ─────────────────────────────────────────────
# Build Job
# ─────────────────────────────────────────────

class TestBuildJob:
    def test_job_creation(self):
        job = BuildJob(requester="daniel")
        assert job.job_kind == JobKind.BUILD
        assert job.status == JobStatus.CREATED
        assert job.execution_mode == ExecutionMode.WORKSPACE_BOUND
        assert job.id != ""

    def test_job_trace(self):
        job = BuildJob()
        t = job.trace("validate")
        t.complete("ok")
        assert len(job.execution_trace) == 1
        assert job.execution_trace[0].step == "validate"

    def test_verification_passed_true(self):
        job = BuildJob()
        job.verification_results = [
            VerificationResult(kind=VerificationKind.TEST, passed=True),
            VerificationResult(kind=VerificationKind.LINT, passed=True),
        ]
        assert job.verification_passed is True

    def test_verification_passed_false(self):
        job = BuildJob()
        job.verification_results = [
            VerificationResult(kind=VerificationKind.TEST, passed=True),
            VerificationResult(kind=VerificationKind.LINT, passed=False),
        ]
        assert job.verification_passed is False

    def test_verification_empty_false(self):
        job = BuildJob()
        assert job.verification_passed is False

    def test_job_roundtrip(self):
        job = BuildJob(
            build_type=BuildJobType.IMPLEMENTATION,
            requester="daniel",
        )
        job.trace("validate").complete("ok")
        job.verification_results.append(
            VerificationResult(kind=VerificationKind.TEST, passed=True)
        )
        job.acceptance.criteria.append(
            AcceptanceCriterion(description="Tests pass")
        )
        job.artifacts.append(
            BuildArtifact(artifact_kind=ArtifactKind.PATCH, job_id=job.id)
        )
        d = job.to_dict()
        job2 = BuildJob.from_dict(d)
        assert job2.id == job.id
        assert job2.build_type == BuildJobType.IMPLEMENTATION
        assert job2.requester == "daniel"
        assert len(job2.execution_trace) == 1
        assert len(job2.verification_results) == 1
        assert len(job2.acceptance.criteria) == 1
        assert len(job2.artifacts) == 1

    def test_job_default_workspace_bound(self):
        """Builder jobs default to WORKSPACE_BOUND, unlike reviewer."""
        job = BuildJob()
        assert job.execution_mode == ExecutionMode.WORKSPACE_BOUND


# ─────────────────────────────────────────────
# Build Storage
# ─────────────────────────────────────────────

class TestBuildStorage:
    @pytest.fixture()
    def storage(self):
        from agent.build.storage import BuildStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        s = BuildStorage(db_path=db_path)
        s.initialize()
        yield s
        s.close()

    def test_save_and_load_job(self, storage):
        job = BuildJob(requester="daniel")
        storage.save_job(job)
        loaded = storage.load_job(job.id)
        assert loaded is not None
        assert loaded["id"] == job.id
        assert loaded["requester"] == "daniel"

    def test_load_nonexistent_job(self, storage):
        assert storage.load_job("nonexistent") is None

    def test_list_jobs(self, storage):
        job1 = BuildJob(requester="a")
        job2 = BuildJob(requester="b")
        job2.status = JobStatus.COMPLETED
        storage.save_job(job1)
        storage.save_job(job2)
        all_jobs = storage.list_jobs()
        assert len(all_jobs) == 2
        completed = storage.list_jobs(status="completed")
        assert len(completed) == 1

    def test_save_and_get_artifacts(self, storage):
        artifact = BuildArtifact(
            artifact_kind=ArtifactKind.PATCH,
            job_id="job-123",
            content="diff content here",
        )
        storage.save_artifact(artifact)
        arts = storage.get_artifacts("job-123")
        assert len(arts) == 1
        assert arts[0]["artifact_kind"] == "patch"
        assert arts[0]["content"] == "diff content here"

    def test_initialize_creates_parent_directory(self):
        from pathlib import Path

        from agent.build.storage import BuildStorage

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nested" / "builds.db"
            storage = BuildStorage(db_path=str(db_path))
            storage.initialize()
            assert db_path.parent.exists()
            assert db_path.exists()
            storage.close()

    def test_recovery_roundtrip(self, storage):
        """Save a BuildJob, reload via from_dict — recovery-safe."""
        job = BuildJob(requester="daniel")
        job.trace("validate").complete("ok")
        job.artifacts.append(
            BuildArtifact(artifact_kind=ArtifactKind.PATCH, job_id=job.id)
        )
        storage.save_job(job)
        data = storage.load_job(job.id)
        recovered = BuildJob.from_dict(data)
        assert recovered.id == job.id
        assert len(recovered.execution_trace) == 1
        assert len(recovered.artifacts) == 1


# ─────────────────────────────────────────────
# Build Verification
# ─────────────────────────────────────────────

class TestBuildVerification:
    def test_verification_nonexistent_path(self):
        from agent.build.verification import run_verification_step
        result = run_verification_step(
            VerificationKind.TEST,
            workspace_path="/nonexistent/path",
        )
        assert result.passed is False
        assert "does not exist" in result.stderr

    def test_verification_suite_with_custom_command(self):
        from agent.build.verification import run_verification_suite
        with tempfile.TemporaryDirectory() as tmpdir:
            results = run_verification_suite(
                workspace_path=tmpdir,
                steps=[VerificationKind.CUSTOM],
                custom_commands={
                    VerificationKind.CUSTOM: ["echo", "hello"],
                },
            )
            assert len(results) == 1
            assert results[0].passed is True
            assert "hello" in results[0].stdout

    def test_verification_failing_command(self):
        from agent.build.verification import run_verification_step
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_verification_step(
                VerificationKind.CUSTOM,
                workspace_path=tmpdir,
                command=["false"],
            )
            assert result.passed is False
            assert result.exit_code != 0


# ─────────────────────────────────────────────
# Build Service
# ─────────────────────────────────────────────

class TestBuildService:
    class _FakeReviewService:
        def __init__(self, review_job: ReviewJob) -> None:
            self._review_job = review_job
            self.calls: list = []

        async def run_review(self, intake):
            self.calls.append(intake)
            return self._review_job

    @pytest.fixture()
    def workspace_manager(self):
        """Mock workspace manager that creates real temp dirs."""
        mgr = MagicMock()
        self._workspace_dir = tempfile.mkdtemp()
        self._repo_dir = tempfile.mkdtemp()
        (Path(self._repo_dir) / "app.py").write_text("def main():\n    return 1\n")
        ws = MagicMock()
        ws.id = "ws-test-123"
        ws.path = self._workspace_dir
        mgr.create.return_value = ws
        return mgr

    @pytest.fixture()
    def service(self, workspace_manager):
        from agent.build.service import BuildService
        from agent.build.storage import BuildStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        return BuildService(
            storage=BuildStorage(db_path=db_path),
            workspace_manager=workspace_manager,
        )

    async def test_validation_failure(self, service):
        intake = BuildIntake(repo_path="", description="")
        job = await service.run_build(intake)
        assert job.status == JobStatus.FAILED
        assert "Validation failed" in job.error

    async def test_no_workspace_manager_fails(self):
        from agent.build.service import BuildService
        from agent.build.storage import BuildStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        svc = BuildService(
            storage=BuildStorage(db_path=db_path),
            workspace_manager=None,
        )
        intake = BuildIntake(repo_path="/tmp/test", description="X")
        job = await svc.run_build(intake)
        assert job.status == JobStatus.FAILED
        assert "workspace manager" in job.error

    async def test_successful_build_with_acceptance(self, service):
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature",
            acceptance_criteria=[
                AcceptanceCriterion(description="Build completes"),
            ],
        )
        job = await service.run_build(intake)
        # Repo is materialized into workspace and the placeholder build runs.
        assert job.workspace_id == "ws-test-123"
        assert job.execution_mode == ExecutionMode.WORKSPACE_BOUND
        assert len(job.execution_trace) >= 4  # validate, workspace, build, verify...
        assert len(job.artifacts) >= 2  # verification + acceptance + trace
        assert job.acceptance.evaluated_at != ""
        assert (Path(self._workspace_dir) / "app.py").exists()
        assert (Path(self._workspace_dir) / ".build_job").exists()

    async def test_build_creates_artifacts(self, service):
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature",
        )
        job = await service.run_build(intake)
        artifact_kinds = [a.artifact_kind.value for a in job.artifacts]
        assert "verification_report" in artifact_kinds
        assert "acceptance_report" in artifact_kinds
        assert "execution_trace" in artifact_kinds

    async def test_build_job_recovery(self, service):
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature",
        )
        job = await service.run_build(intake)
        recovered = service.load_job(job.id)
        assert recovered is not None
        assert recovered.id == job.id
        assert recovered.workspace_id == "ws-test-123"

    async def test_execution_trace_has_analysis_steps(self, service):
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature",
        )
        job = await service.run_build(intake)
        steps = [t.step for t in job.execution_trace]
        assert "validate" in steps
        assert "workspace" in steps
        assert "build" in steps
        assert "verify" in steps
        assert "acceptance" in steps

    async def test_unknown_acceptance_criterion_is_not_auto_met(
        self, service, monkeypatch
    ):
        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [
                VerificationResult(
                    kind=VerificationKind.TEST,
                    passed=True,
                    command="pytest",
                    exit_code=0,
                ),
                VerificationResult(
                    kind=VerificationKind.LINT,
                    passed=True,
                    command="ruff check .",
                    exit_code=0,
                ),
            ],
        )
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature",
            acceptance_criteria=[
                AcceptanceCriterion(description="Ship to production"),
            ],
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.FAILED
        assert job.acceptance.criteria[0].status == CriterionStatus.UNMET
        assert "No evaluator available" in job.acceptance.criteria[0].evidence

    async def test_verify_acceptance_runs_inside_workspace(
        self, service, monkeypatch
    ):
        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [
                VerificationResult(
                    kind=VerificationKind.TEST,
                    passed=True,
                    command="pytest",
                    exit_code=0,
                ),
                VerificationResult(
                    kind=VerificationKind.LINT,
                    passed=True,
                    command="ruff check .",
                    exit_code=0,
                ),
            ],
        )
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature",
            acceptance_criteria=[
                AcceptanceCriterion(
                    description=(
                        'verify: python3 -c "from pathlib import Path; '
                        "raise SystemExit(0 if Path('app.py').exists() else 1)\""
                    )
                ),
            ],
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.COMPLETED
        assert job.acceptance.criteria[0].status == CriterionStatus.MET
        assert "exit=0" in job.acceptance.criteria[0].evidence

    def test_get_verification_steps_adds_typecheck_when_config_present(self, service):
        temp_repo = tempfile.mkdtemp()
        (Path(temp_repo) / "pyproject.toml").write_text("[tool.mypy]\npython_version='3.12'\n")

        steps = service._get_verification_steps(temp_repo)

        assert VerificationKind.TYPECHECK in steps

    async def test_post_build_review_passes_through_review_service(
        self, workspace_manager, monkeypatch
    ):
        from agent.build.service import BuildService
        from agent.build.storage import BuildStorage

        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [
                VerificationResult(
                    kind=VerificationKind.TEST,
                    passed=True,
                    command="pytest",
                    exit_code=0,
                ),
                VerificationResult(
                    kind=VerificationKind.LINT,
                    passed=True,
                    command="ruff check .",
                    exit_code=0,
                ),
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        clean_review = ReviewJob()
        clean_review.report = ReviewReport(
            executive_summary="No findings.",
            verdict="pass",
        )

        review_service = self._FakeReviewService(clean_review)
        service = BuildService(
            storage=BuildStorage(db_path=db_path),
            workspace_manager=workspace_manager,
            review_service=review_service,
        )
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Ship clean change",
            acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
            run_post_build_review=True,
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.COMPLETED
        assert job.post_build_review_verdict == "pass"
        assert job.post_build_review_job_id == clean_review.id
        assert review_service.calls[0].repo_path == self._workspace_dir
        assert "review_report" in [artifact.artifact_kind.value for artifact in job.artifacts]

    async def test_post_build_review_blocks_on_critical_findings(
        self, workspace_manager, monkeypatch
    ):
        from agent.build.service import BuildService
        from agent.build.storage import BuildStorage

        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [
                VerificationResult(
                    kind=VerificationKind.TEST,
                    passed=True,
                    command="pytest",
                    exit_code=0,
                ),
                VerificationResult(
                    kind=VerificationKind.LINT,
                    passed=True,
                    command="ruff check .",
                    exit_code=0,
                ),
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        blocked_review = ReviewJob()
        blocked_review.report = ReviewReport(
            executive_summary="Critical finding present.",
            verdict="fail",
            findings=[
                ReviewFinding(
                    severity=Severity.CRITICAL,
                    title="Critical issue",
                )
            ],
        )

        review_service = self._FakeReviewService(blocked_review)
        service = BuildService(
            storage=BuildStorage(db_path=db_path),
            workspace_manager=workspace_manager,
            review_service=review_service,
        )
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Ship risky change",
            acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
            run_post_build_review=True,
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.BLOCKED
        assert job.post_build_review_verdict == "fail"
        assert "critical findings" in job.error.lower()
        assert "finding_list" in [artifact.artifact_kind.value for artifact in job.artifacts]
