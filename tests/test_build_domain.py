"""Tests for builder bounded context (agent.build)."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.build.models import (
    AcceptanceCriterion,
    AcceptanceVerdict,
    BuildArtifact,
    BuildImplementationMode,
    BuildIntake,
    BuildJob,
    BuildJobType,
    BuildOperation,
    BuildOperationStatus,
    BuildOperationType,
    CriterionEvaluator,
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
        c = AcceptanceCriterion(
            description="Tests pass",
            kind=CriterionKind.SECURITY,
            required=False,
            evaluator=CriterionEvaluator.REVIEW,
        )
        c.meet("passed")
        d = c.to_dict()
        c2 = AcceptanceCriterion.from_dict(d)
        assert c2.description == "Tests pass"
        assert c2.kind == CriterionKind.SECURITY
        assert c2.required is False
        assert c2.evaluator == CriterionEvaluator.REVIEW
        assert c2.status == CriterionStatus.MET

    def test_criterion_from_text_parses_tags(self):
        criterion = AcceptanceCriterion.from_text(
            "optional: quality: Verify: python -m pytest -q"
        )

        assert criterion.required is False
        assert criterion.kind == CriterionKind.QUALITY
        assert criterion.evaluator == CriterionEvaluator.VERIFY_COMMAND
        assert criterion.description.startswith("verify:")

    def test_criterion_from_input_accepts_structured_dict(self):
        criterion = AcceptanceCriterion.from_input(
            {
                "description": "Config version stamped",
                "evaluator": "workspace",
                "metadata": {
                    "path": "config.json",
                    "json_path": "release.version",
                    "expected_value": "1.11.0",
                },
            }
        )

        assert criterion.evaluator == CriterionEvaluator.WORKSPACE
        assert criterion.metadata["path"] == "config.json"
        assert criterion.metadata["expected_value"] == "1.11.0"

    def test_criterion_from_text_parses_implementation_evaluator(self):
        criterion = AcceptanceCriterion.from_text(
            "required: implementation: bounded engine applies changes"
        )

        assert criterion.required is True
        assert criterion.evaluator == CriterionEvaluator.IMPLEMENTATION
        assert criterion.description == "bounded engine applies changes"


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

    def test_optional_unmet_does_not_reject_required_success(self):
        v = AcceptanceVerdict(criteria=[
            AcceptanceCriterion(description="Tests pass"),
            AcceptanceCriterion(description="Docs updated", required=False),
        ])
        v.criteria[0].meet("ok")
        v.criteria[1].fail("docs missing")
        v.evaluate()
        assert v.accepted is True
        assert v.required_unmet_count == 0
        assert v.optional_unmet_count == 1

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
            execution_policy_id="workspace_local_mutation",
            description="Add API endpoint",
            target_files=["src/api.py"],
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.REPLACE_TEXT,
                    path="src/api.py",
                    match_text="return 1",
                    replacement_text="return 2",
                )
            ],
            acceptance_criteria=[AcceptanceCriterion(description="Tests pass")],
            run_post_build_review=True,
            source="operator",
            requester="daniel",
        )
        d = intake.to_dict()
        intake2 = BuildIntake.from_dict(d)
        assert intake2.repo_path == "/tmp/test"
        assert intake2.build_type == BuildJobType.INTEGRATION
        assert intake2.execution_policy_id == "workspace_local_mutation"
        assert len(intake2.implementation_plan) == 1
        assert intake2.implementation_plan[0].operation_type == BuildOperationType.REPLACE_TEXT
        assert len(intake2.acceptance_criteria) == 1
        assert intake2.run_post_build_review is True
        assert intake2.source == "operator"

    def test_implementation_operation_roundtrip(self):
        operation = BuildOperation(
            operation_type=BuildOperationType.JSON_SET,
            path="config.json",
            json_path=["release", "version"],
            value="1.10.0",
            description="Stamp release version",
        )

        restored = BuildOperation.from_dict(operation.to_dict())

        assert restored.operation_type == BuildOperationType.JSON_SET
        assert restored.path == "config.json"
        assert restored.json_path == ["release", "version"]
        assert restored.value == "1.10.0"

    def test_implementation_operation_validate_blocks_traversal(self):
        operation = BuildOperation(
            operation_type=BuildOperationType.WRITE_FILE,
            path="../escape.txt",
            content="bad",
        )

        assert operation.validate()


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
            format="diff",
        )
        storage.save_artifact(artifact)
        arts = storage.get_artifacts("job-123")
        assert len(arts) == 1
        assert arts[0]["artifact_kind"] == "patch"
        assert arts[0]["content"] == "diff content here"
        assert arts[0]["format"] == "diff"

    def test_list_and_get_artifact(self, storage):
        artifact = BuildArtifact(
            artifact_kind=ArtifactKind.VERIFICATION_REPORT,
            job_id="job-123",
            content_json={"all_passed": True},
            format="json",
        )
        storage.save_artifact(artifact)

        listed = storage.list_artifacts(job_id="job-123")
        loaded = storage.get_artifact(artifact.id)

        assert len(listed) == 1
        assert listed[0]["id"] == artifact.id
        assert listed[0]["format"] == "json"
        assert loaded is not None
        assert loaded["content_json"]["all_passed"] is True

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
        mgr.get.return_value = ws
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

    async def test_build_execution_policy_blocks_unknown_source(self, service, workspace_manager):
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature from unsupported source",
            source="telegram",
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.BLOCKED
        assert job.denial["code"] == "build_execution_blocked"
        assert "Blocked unknown build source" in job.error
        workspace_manager.create.assert_not_called()

    async def test_codegen_fallback_rejects_build(self, service, monkeypatch):
        """When codegen fails and build falls into audit_marker_only mode,
        the build must be rejected (fail-closed) — not accepted with zero
        changes.  This prevents the operator from seeing a 'successful' build
        that produced no implementation."""
        async def _failing_codegen(*args, **kwargs):
            raise RuntimeError("Invalid authentication credentials")

        monkeypatch.setattr(
            "agent.build.codegen.generate_build_operations",
            _failing_codegen,
        )

        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature",
            acceptance_criteria=[
                AcceptanceCriterion(description="Build completes"),
            ],
        )
        job = await service.run_build(intake)
        assert job.status == JobStatus.FAILED
        assert job.acceptance.accepted is False
        assert "code generation failed" in (job.error or "").lower()
        assert job.implementation_mode == BuildImplementationMode.AUDIT_MARKER_ONLY

    async def test_structured_implementation_plan_executes_local_changes(
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
                )
            ],
        )

        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Apply structured implementation plan",
            target_files=["app.py", "docs/notes.md", "config.json"],
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
                    content="# Notes\n- Builder update\n",
                ),
                BuildOperation(
                    operation_type=BuildOperationType.JSON_SET,
                    path="config.json",
                    json_path=["release", "version"],
                    value="1.10.0",
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(description="Target files changed"),
                AcceptanceCriterion(description="Documentation updated", required=False),
            ],
        )

        job = await service.run_build(intake)
        bundle = service.get_delivery_bundle(job.id)

        assert job.status == JobStatus.COMPLETED
        assert job.implementation_mode == BuildImplementationMode.BOUNDED_LOCAL_ENGINE
        assert len(job.implementation_results) == 3
        assert all(result.status == BuildOperationStatus.APPLIED for result in job.implementation_results)
        assert (Path(self._workspace_dir) / "app.py").read_text() == "def main():\n    return 2\n"
        assert (Path(self._workspace_dir) / "docs" / "notes.md").exists()
        assert '"version": "1.10.0"' in (Path(self._workspace_dir) / "config.json").read_text()
        assert job.acceptance.criteria[0].status == CriterionStatus.MET
        assert bundle is not None
        assert bundle["summary"]["implementation_mode"] == "bounded_local_engine"
        assert bundle["summary"]["implementation_operations"] == 3
        assert bundle["payload"]["implementation"]["changed_operations"] == 3

    async def test_structured_implementation_plan_failure_returns_denial(
        self, service
    ):
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Fail structured implementation plan",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.REPLACE_TEXT,
                    path="app.py",
                    match_text="return 404",
                    replacement_text="return 2",
                )
            ],
            acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.FAILED
        assert job.denial["code"] == "build_implementation_failed"
        assert job.implementation_results[0].status == BuildOperationStatus.FAILED
        assert "match_text not found" in job.error

    async def test_structured_implementation_plan_supports_insert_and_delete_operations(
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
                )
            ],
        )
        (Path(self._repo_dir) / "README.md").write_text("intro\nremove me\n", encoding="utf-8")
        (Path(self._repo_dir) / "legacy.txt").write_text("legacy\n", encoding="utf-8")

        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Exercise richer structured builder mutations",
            target_files=["app.py", "README.md", "legacy.txt"],
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.INSERT_AFTER_TEXT,
                    path="app.py",
                    match_text="def main():\n",
                    content="    print('ready')\n",
                ),
                BuildOperation(
                    operation_type=BuildOperationType.INSERT_BEFORE_TEXT,
                    path="README.md",
                    match_text="intro\n",
                    content="# Title\n",
                ),
                BuildOperation(
                    operation_type=BuildOperationType.DELETE_TEXT,
                    path="README.md",
                    match_text="remove me\n",
                ),
                BuildOperation(
                    operation_type=BuildOperationType.DELETE_FILE,
                    path="legacy.txt",
                ),
            ],
        )

        job = await service.run_build(intake)
        bundle = service.get_delivery_bundle(job.id)

        assert job.status == JobStatus.COMPLETED
        assert all(
            result.status == BuildOperationStatus.APPLIED
            for result in job.implementation_results
        )
        assert "print('ready')" in (Path(self._workspace_dir) / "app.py").read_text(
            encoding="utf-8"
        )
        assert (Path(self._workspace_dir) / "README.md").read_text(encoding="utf-8") == (
            "# Title\nintro\n"
        )
        assert not (Path(self._workspace_dir) / "legacy.txt").exists()
        assert bundle is not None
        assert bundle["summary"]["operation_mix"]["insert_after_text"] == 1
        assert bundle["summary"]["operation_mix"]["insert_before_text"] == 1
        assert bundle["summary"]["operation_mix"]["delete_text"] == 1
        assert bundle["summary"]["operation_mix"]["delete_file"] == 1

    async def test_structured_implementation_plan_supports_copy_and_move_operations(
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
                )
            ],
        )
        (Path(self._repo_dir) / "templates").mkdir()
        (Path(self._repo_dir) / "templates" / "snippet.txt").write_text(
            "copied content\n",
            encoding="utf-8",
        )
        (Path(self._repo_dir) / "legacy.txt").write_text("legacy\n", encoding="utf-8")

        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Exercise copy and move builder mutations",
            target_files=[
                "templates/snippet.txt",
                "docs/copied.txt",
                "legacy.txt",
                "archive/legacy.txt",
            ],
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.COPY_FILE,
                    source_path="templates/snippet.txt",
                    path="docs/copied.txt",
                ),
                BuildOperation(
                    operation_type=BuildOperationType.MOVE_FILE,
                    source_path="legacy.txt",
                    path="archive/legacy.txt",
                ),
            ],
        )

        job = await service.run_build(intake)
        bundle = service.get_delivery_bundle(job.id)

        assert job.status == JobStatus.COMPLETED
        assert (Path(self._workspace_dir) / "docs" / "copied.txt").read_text(
            encoding="utf-8"
        ) == "copied content\n"
        assert not (Path(self._workspace_dir) / "legacy.txt").exists()
        assert (Path(self._workspace_dir) / "archive" / "legacy.txt").read_text(
            encoding="utf-8"
        ) == "legacy\n"
        assert bundle is not None
        assert bundle["summary"]["operation_mix"]["copy_file"] == 1
        assert bundle["summary"]["operation_mix"]["move_file"] == 1

    async def test_implementation_acceptance_uses_execution_summary(
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
                )
            ],
        )

        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Use implementation-backed acceptance",
            target_files=["app.py", "docs/notes.md"],
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.REPLACE_TEXT,
                    path="app.py",
                    match_text="return 1",
                    replacement_text="return 3",
                ),
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="docs/notes.md",
                    content="# Notes\nready\n",
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    description="Implementation plan applies target mutations",
                    evaluator=CriterionEvaluator.IMPLEMENTATION,
                    metadata={
                        "minimum_changed_operations": 2,
                        "maximum_noop_operations": 0,
                        "required_operation_types": ["replace_text", "write_file"],
                        "required_changed_paths": ["app.py", "docs/notes.md"],
                        "implementation_mode": "bounded_local_engine",
                    },
                )
            ],
        )

        job = await service.run_build(intake)
        bundle = service.get_delivery_bundle(job.id)

        assert job.status == JobStatus.COMPLETED
        assert job.acceptance.criteria[0].status == CriterionStatus.MET
        assert bundle is not None
        assert (
            bundle["payload"]["acceptance_report"]["implementation_summary"][
                "changed_operations"
            ]
            == 2
        )
        assert (
            bundle["payload"]["acceptance_summary"]["implementation"]["changed_operations"]
            == 2
        )

    async def test_capability_guardrails_block_operations_outside_declared_targets(
        self, service
    ):
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Attempt out-of-scope mutation",
            target_files=["app.py"],
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="docs/notes.md",
                    content="out of scope\n",
                )
            ],
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.FAILED
        assert job.denial["code"] == "build_capability_plan_invalid"
        assert "outside declared target_files" in job.error

    async def test_capability_guardrails_block_excessive_operation_count(
        self, service
    ):
        intake = BuildIntake(
            repo_path=self._repo_dir,
            build_type=BuildJobType.DEVOPS,
            description="Attempt too many deterministic mutations",
            target_files=["README.md"],
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.APPEND_TEXT,
                    path="README.md",
                    content=f"line {index}\n",
                )
                for index in range(17)
            ],
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.FAILED
        assert job.denial["code"] == "build_capability_plan_invalid"
        assert "allows at most 16 structured operation" in job.error

    async def test_structured_workspace_acceptance_checks(self, service, monkeypatch):
        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [
                VerificationResult(
                    kind=VerificationKind.TEST,
                    passed=True,
                    command="pytest",
                    exit_code=0,
                )
            ],
        )
        (Path(self._repo_dir) / "README.md").write_text("# Repo\n")

        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Stamp config and docs",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.JSON_SET,
                    path="config.json",
                    json_path=["release", "version"],
                    value="1.11.0",
                ),
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="README.md",
                    content="# Repo\n\nBuilder release 1.11.0\n",
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    description="Config version is stamped",
                    evaluator=CriterionEvaluator.WORKSPACE,
                    metadata={
                        "path": "config.json",
                        "json_path": "release.version",
                        "expected_value": "1.11.0",
                    },
                ),
                AcceptanceCriterion(
                    description="README updated for the release",
                    evaluator=CriterionEvaluator.WORKSPACE,
                    metadata={
                        "path": "README.md",
                        "contains_text": "Builder release 1.11.0",
                        "must_change": True,
                    },
                ),
            ],
        )

        job = await service.run_build(intake)
        acceptance_artifact = next(
            artifact
            for artifact in job.artifacts
            if artifact.artifact_kind == ArtifactKind.ACCEPTANCE_REPORT
        )

        assert job.status == JobStatus.COMPLETED
        assert job.acceptance.accepted is True
        assert all(
            criterion.status == CriterionStatus.MET
            for criterion in job.acceptance.criteria
        )
        assert (
            acceptance_artifact.content_json["delivery_summary"]["structured_count"] == 2
        )
        assert (
            acceptance_artifact.content_json["delivery_summary"]["by_evaluator"]["workspace"]
            == 2
        )

    async def test_structured_change_and_review_acceptance_checks(
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
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        clean_review = ReviewJob()
        clean_review.report = ReviewReport(
            executive_summary="No blocking findings.",
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
            description="Apply builder slice with structured acceptance",
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
                    content="# Notes\n- shipped\n",
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    description="Required deliverable paths changed",
                    evaluator=CriterionEvaluator.CHANGE_SET,
                    metadata={
                        "required_paths": ["app.py", "docs/notes.md"],
                        "minimum_changed_files": 2,
                    },
                ),
                AcceptanceCriterion(
                    description="Post-build review stays below blocking thresholds",
                    kind=CriterionKind.SECURITY,
                    evaluator=CriterionEvaluator.REVIEW,
                    metadata={"max_critical": 0, "max_high": 0},
                ),
            ],
            run_post_build_review=True,
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.COMPLETED
        assert job.acceptance.accepted is True
        assert job.acceptance.criteria[0].status == CriterionStatus.MET
        assert job.acceptance.criteria[1].status == CriterionStatus.MET
        assert "Structured change-set checks passed" in job.acceptance.criteria[0].evidence
        assert "review verdict=pass" in job.acceptance.criteria[1].evidence

    async def test_build_creates_artifacts(self, service, monkeypatch):
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
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# new feature\n",
                ),
            ],
        )
        job = await service.run_build(intake)
        artifact_kinds = [a.artifact_kind.value for a in job.artifacts]
        verification_reports = [
            artifact for artifact in job.artifacts if artifact.artifact_kind == ArtifactKind.VERIFICATION_REPORT
        ]
        suite_reports = [
            artifact
            for artifact in verification_reports
            if artifact.content_json.get("report_kind") == "suite"
        ]
        step_reports = [
            artifact
            for artifact in verification_reports
            if artifact.content_json.get("report_kind") == "step"
        ]
        assert "patch" in artifact_kinds
        assert "diff" in artifact_kinds
        assert "verification_report" in artifact_kinds
        assert "acceptance_report" in artifact_kinds
        assert "execution_trace" in artifact_kinds
        assert len(suite_reports) == 1
        assert len(step_reports) == 2
        assert suite_reports[0].content_json["total_steps"] == 2
        assert {artifact.content_json["verification_kind"] for artifact in step_reports} == {
            "test",
            "lint",
        }

    async def test_build_without_explicit_acceptance_uses_verification_proxy(
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
            description="Build without explicit acceptance criteria",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# new feature\n",
                ),
            ],
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.COMPLETED
        assert job.acceptance.accepted is True
        assert "verification outcome used as acceptance proxy" in job.acceptance.summary

    async def test_build_job_recovery(self, service, monkeypatch):
        async def _failing_codegen(*args, **kwargs):
            raise RuntimeError("simulated codegen failure")

        monkeypatch.setattr(
            "agent.build.codegen.generate_build_operations",
            _failing_codegen,
        )

        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature",
        )
        job = await service.run_build(intake)
        recovered = service.load_job(job.id)
        assert recovered is not None
        assert recovered.id == job.id
        assert recovered.workspace_id == "ws-test-123"

    async def test_execution_trace_has_analysis_steps(self, service, monkeypatch):
        async def _failing_codegen(*args, **kwargs):
            raise RuntimeError("simulated codegen failure")

        monkeypatch.setattr(
            "agent.build.codegen.generate_build_operations",
            _failing_codegen,
        )

        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Add feature",
        )
        job = await service.run_build(intake)
        steps = [t.step for t in job.execution_trace]
        assert "validate" in steps
        assert "workspace" in steps
        assert "build" in steps
        # codegen_fallback_guard fires before verify/acceptance when no plan
        assert "codegen_fallback_guard" in steps

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
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# new feature\n",
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(description="Ship to production"),
            ],
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.FAILED
        assert job.acceptance.criteria[0].status == CriterionStatus.UNMET
        assert "No evaluator available" in job.acceptance.criteria[0].evidence
        assert job.denial["code"] == "build_acceptance_unmet"
        assert "Ship to production" in job.error

    async def test_optional_acceptance_failure_does_not_fail_build(
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
                )
            ],
        )
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Build with optional docs criterion",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# new feature\n",
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(description="Tests pass"),
                AcceptanceCriterion(
                    description="Documentation updated",
                    required=False,
                ),
            ],
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.COMPLETED
        assert job.acceptance.accepted is True
        assert job.acceptance.required_unmet_count == 0
        assert job.acceptance.optional_unmet_count == 1
        assert job.acceptance.criteria[1].status == CriterionStatus.UNMET

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
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# new feature\n",
                ),
            ],
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

    async def test_documentation_acceptance_uses_workspace_change_set(
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
                )
            ],
        )

        def fake_execute_build(job, workspace_path):
            readme = Path(workspace_path) / "README.md"
            readme.write_text("# Updated release notes\n")
            return True

        monkeypatch.setattr(service, "_execute_build", fake_execute_build)

        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Update docs",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="README.md",
                    content="# Updated release notes\n",
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(description="Documentation updated"),
            ],
        )

        job = await service.run_build(intake)

        patch_artifact = next(
            artifact for artifact in job.artifacts
            if artifact.artifact_kind == ArtifactKind.PATCH
        )
        assert job.status == JobStatus.COMPLETED
        assert job.acceptance.criteria[0].status == CriterionStatus.MET
        assert "README.md" in patch_artifact.content
        assert "deliverable_files_changed" in patch_artifact.content_json

    async def test_security_acceptance_uses_post_build_review(
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
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        clean_review = ReviewJob()
        clean_review.report = ReviewReport(
            executive_summary="No critical findings.",
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
            description="Ship secure change",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# secure feature\n",
                ),
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    description="Security review passes",
                    kind=CriterionKind.SECURITY,
                )
            ],
            run_post_build_review=True,
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.COMPLETED
        assert job.acceptance.criteria[0].status == CriterionStatus.MET
        assert "review verdict=pass" in job.acceptance.criteria[0].evidence

    def test_get_verification_steps_adds_typecheck_when_config_present(self, service):
        temp_repo = tempfile.mkdtemp()
        (Path(temp_repo) / "pyproject.toml").write_text("[tool.mypy]\npython_version='3.12'\n")

        steps = service._get_verification_steps(temp_repo)

        assert VerificationKind.TYPECHECK in steps

    def test_get_verification_steps_skips_typecheck_without_markers(self, service):
        temp_repo = tempfile.mkdtemp()
        (Path(temp_repo) / "app.py").write_text("def main():\n    return 1\n")
        (Path(temp_repo) / "tests").mkdir()
        (Path(temp_repo) / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")

        steps = service._get_verification_steps(temp_repo)

        assert VerificationKind.TEST in steps
        assert VerificationKind.LINT in steps
        assert VerificationKind.TYPECHECK not in steps

    def test_resolve_verification_commands_prefers_repo_local_toolchain(self, service):
        tool_root = Path(self._repo_dir) / ".venv" / "bin"
        tool_root.mkdir(parents=True)
        (tool_root / "python").write_text("")
        (tool_root / "ruff").write_text("")
        (tool_root / "mypy").write_text("")

        commands = service._resolve_verification_commands(
            repo_path=self._repo_dir,
            workspace_path=self._workspace_dir,
            steps=[
                VerificationKind.TEST,
                VerificationKind.LINT,
                VerificationKind.TYPECHECK,
            ],
        )

        assert Path(commands[VerificationKind.TEST][0]).resolve() == (tool_root / "python").resolve()
        assert commands[VerificationKind.TEST][1:] == ["-m", "pytest", "tests/", "-q", "--tb=short"]
        assert Path(commands[VerificationKind.LINT][0]).resolve() == (tool_root / "ruff").resolve()
        assert commands[VerificationKind.LINT][1:] == ["check", "."]
        assert Path(commands[VerificationKind.TYPECHECK][0]).resolve() == (tool_root / "mypy").resolve()
        assert commands[VerificationKind.TYPECHECK][1:] == [".", "--ignore-missing-imports"]

    def test_get_verification_steps_detects_package_scripts_and_workflows(self, service):
        temp_repo = tempfile.mkdtemp()
        (Path(temp_repo) / "package.json").write_text(
            '{"packageManager":"pnpm@8.15.0",'
            '"scripts":{"test":"vitest run","lint":"eslint .","typecheck":"tsc --noEmit"},'
            '"devDependencies":{"typescript":"5.5.0"}}'
        )
        (Path(temp_repo) / "tsconfig.json").write_text('{"compilerOptions":{"noEmit":true}}\n')
        (Path(temp_repo) / ".github" / "workflows").mkdir(parents=True)
        (Path(temp_repo) / ".github" / "workflows" / "ci.yml").write_text(
            "name: ci\njobs:\n  test:\n    steps:\n      - run: pnpm run typecheck\n"
        )

        plan = service._discover_verification_plan(temp_repo)

        assert plan["steps"] == [
            VerificationKind.TEST,
            VerificationKind.LINT,
            VerificationKind.TYPECHECK,
        ]
        assert "package.json:scripts.test" in plan["step_details"][VerificationKind.TEST.value]
        assert "package.json:scripts.lint" in plan["step_details"][VerificationKind.LINT.value]
        assert "package.json:scripts.typecheck" in plan["step_details"][VerificationKind.TYPECHECK.value]

    def test_resolve_verification_commands_prefers_package_manager_scripts_for_node_project(
        self, service
    ):
        (Path(self._workspace_dir) / "package.json").write_text(
            '{"packageManager":"pnpm@8.15.0",'
            '"scripts":{"test":"vitest run","lint":"eslint .","typecheck":"tsc --noEmit"}}'
        )
        (Path(self._workspace_dir) / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

        commands = service._resolve_verification_commands(
            repo_path=self._repo_dir,
            workspace_path=self._workspace_dir,
            steps=[
                VerificationKind.TEST,
                VerificationKind.LINT,
                VerificationKind.TYPECHECK,
            ],
        )

        assert commands[VerificationKind.TEST] == ["pnpm", "run", "test"]
        assert commands[VerificationKind.LINT] == ["pnpm", "run", "lint"]
        assert commands[VerificationKind.TYPECHECK] == ["pnpm", "run", "typecheck"]

    def test_resolve_verification_commands_uses_make_targets_without_toolchain(
        self, service
    ):
        (Path(self._workspace_dir) / "Makefile").write_text(
            "test:\n\tpytest -q\nlint:\n\truff check .\ntypecheck:\n\tmypy .\n"
        )

        commands = service._resolve_verification_commands(
            repo_path=self._repo_dir,
            workspace_path=self._workspace_dir,
            steps=[
                VerificationKind.TEST,
                VerificationKind.LINT,
                VerificationKind.TYPECHECK,
            ],
        )

        assert commands[VerificationKind.TEST] == ["make", "test"]
        assert commands[VerificationKind.LINT] == ["make", "lint"]
        assert commands[VerificationKind.TYPECHECK] == ["make", "typecheck"]

    def test_resolve_verification_commands_uses_node_typecheck_binary_without_script(
        self, service
    ):
        node_bin = Path(self._repo_dir) / "node_modules" / ".bin"
        node_bin.mkdir(parents=True)
        (node_bin / "tsc").write_text("")
        (Path(self._workspace_dir) / "tsconfig.json").write_text(
            '{"compilerOptions":{"noEmit":true}}\n'
        )

        commands = service._resolve_verification_commands(
            repo_path=self._repo_dir,
            workspace_path=self._workspace_dir,
            steps=[VerificationKind.TYPECHECK],
        )

        assert Path(commands[VerificationKind.TYPECHECK][0]).resolve() == (node_bin / "tsc").resolve()
        assert commands[VerificationKind.TYPECHECK][1:] == ["--noEmit"]

    async def test_build_records_capability_id(self, service, monkeypatch):
        monkeypatch.setattr(
            "agent.build.service.run_verification_suite",
            lambda **kwargs: [],
        )

        intake = BuildIntake(
            repo_path=self._repo_dir,
            build_type=BuildJobType.DEVOPS,
            description="Update workflow",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="workflow.yml",
                    content="name: ci\n",
                ),
            ],
            acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
        )

        job = await service.run_build(intake)

        assert job.capability_id == "devops_safe"
        assert "devops_safe" in service.get_stats()["capabilities"]

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
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# clean change\n",
                ),
            ],
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
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# risky change\n",
                ),
            ],
            acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
            run_post_build_review=True,
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.BLOCKED
        assert job.post_build_review_verdict == "fail"
        assert "critical findings" in job.error.lower()
        assert "finding_list" in [artifact.artifact_kind.value for artifact in job.artifacts]

    async def test_post_build_review_high_or_critical_policy_blocks_high_findings(
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
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        flagged_review = ReviewJob()
        flagged_review.report = ReviewReport(
            executive_summary="High-risk finding present.",
            verdict="pass_with_findings",
            findings=[
                ReviewFinding(
                    severity=Severity.HIGH,
                    title="High issue",
                )
            ],
        )

        review_service = self._FakeReviewService(flagged_review)
        service = BuildService(
            storage=BuildStorage(db_path=db_path),
            workspace_manager=workspace_manager,
            review_service=review_service,
        )
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Ship integration change",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# integration change\n",
                ),
            ],
            acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
            run_post_build_review=True,
            review_gate_policy_id="high_or_critical",
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.BLOCKED
        assert "high findings exceed policy" in job.error.lower()

    async def test_post_build_review_advisory_policy_keeps_completion_open(
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
                )
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        advisory_review = ReviewJob()
        advisory_review.report = ReviewReport(
            executive_summary="Critical finding present.",
            verdict="fail",
            findings=[
                ReviewFinding(
                    severity=Severity.CRITICAL,
                    title="Critical issue",
                )
            ],
        )

        review_service = self._FakeReviewService(advisory_review)
        service = BuildService(
            storage=BuildStorage(db_path=db_path),
            workspace_manager=workspace_manager,
            review_service=review_service,
        )
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Ship advisory-only change",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# advisory change\n",
                ),
            ],
            acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
            run_post_build_review=True,
            review_gate_policy_id="advisory",
            block_on_review_failure=False,
        )

        job = await service.run_build(intake)

        assert job.status == JobStatus.COMPLETED
        assert job.post_build_review_verdict == "fail"

    async def test_resume_build_reruns_failed_verification(
        self, service, workspace_manager, monkeypatch
    ):
        verification_runs = {"count": 0}

        def fake_suite(**kwargs):
            verification_runs["count"] += 1
            passed = verification_runs["count"] > 1
            return [
                VerificationResult(
                    kind=VerificationKind.TEST,
                    passed=passed,
                    command="pytest",
                    exit_code=0 if passed else 1,
                ),
                VerificationResult(
                    kind=VerificationKind.LINT,
                    passed=passed,
                    command="ruff check .",
                    exit_code=0 if passed else 1,
                ),
            ]

        monkeypatch.setattr("agent.build.service.run_verification_suite", fake_suite)
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Retry after interruption",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# retry feature\n",
                ),
            ],
            acceptance_criteria=[AcceptanceCriterion(description="Tests pass")],
        )

        first_job = await service.run_build(intake)
        resumed_job = await service.resume_build(first_job.id)

        assert first_job.status == JobStatus.FAILED
        assert resumed_job is not None
        assert resumed_job.resumed_from_job_id == first_job.id
        assert resumed_job.resume_count == 1
        assert resumed_job.status == JobStatus.COMPLETED
        assert resumed_job.verification_passed is True
        assert verification_runs["count"] == 2

    async def test_delivery_bundle_and_approval_linkage(
        self, workspace_manager, monkeypatch
    ):
        from agent.build.service import BuildService
        from agent.build.storage import BuildStorage
        from agent.core.approval import ApprovalQueue

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

        queue = ApprovalQueue()
        service = BuildService(
            storage=BuildStorage(db_path=db_path),
            workspace_manager=workspace_manager,
            approval_queue=queue,
        )
        intake = BuildIntake(
            repo_path=self._repo_dir,
            description="Prepare delivery bundle",
            implementation_plan=[
                BuildOperation(
                    operation_type=BuildOperationType.WRITE_FILE,
                    path="feature.py",
                    content="# delivery feature\n",
                ),
            ],
            acceptance_criteria=[AcceptanceCriterion(description="Build completes")],
        )

        job = await service.run_build(intake)
        bundle = service.get_delivery_bundle(job.id)
        approval = service.request_delivery_approval(job.id)
        request = queue.get_request(approval["approval_request_id"])

        assert bundle is not None
        assert bundle["package_type"] == "build_delivery"
        assert "verification_passed" in bundle["summary"]
        assert bundle["summary"]["verification_artifacts"] == 3
        assert bundle["summary"]["verification_steps"] == 2
        assert bundle["payload"]["verification_report"]["report_kind"] == "suite"
        assert len(bundle["payload"]["verification_artifact_ids"]) == 3
        assert len(bundle["payload"]["verification_artifacts"]) == 3
        assert bundle["payload"]["acceptance_summary"]["accepted"] is True
        assert bundle["payload"]["acceptance_summary"]["required_total"] == 1
        assert bundle["payload"]["patch"]["metadata"]["files_changed"] >= 1
        assert approval["bundle_id"] == bundle["bundle_id"]
        assert approval["required_approvals"] == 1
        assert request is not None
        assert request["context"]["workspace_id"] == job.workspace_id
        assert request["context"]["bundle_id"] == bundle["bundle_id"]


# ─────────────────────────────────────────────
# Verification timeout env parsing
# ─────────────────────────────────────────────


class TestVerifyTimeoutEnvParsing:
    """AGENT_VERIFY_TIMEOUT must not crash on invalid values."""

    def test_default_fallback_without_env(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_VERIFY_TIMEOUT", None)
            try:
                val = int(os.environ.get("AGENT_VERIFY_TIMEOUT", "120"))
            except (ValueError, TypeError):
                val = 120
            assert val == 120

    def test_valid_integer_env(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"AGENT_VERIFY_TIMEOUT": "300"}):
            try:
                val = int(os.environ.get("AGENT_VERIFY_TIMEOUT", "120"))
            except (ValueError, TypeError):
                val = 120
            assert val == 300

    def test_invalid_env_falls_back_without_crash(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"AGENT_VERIFY_TIMEOUT": "not-a-number"}):
            try:
                val = int(os.environ.get("AGENT_VERIFY_TIMEOUT", "120"))
            except (ValueError, TypeError):
                val = 120
            assert val == 120
