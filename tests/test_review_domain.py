"""
Tests for the Review bounded context.

Covers:
1. Domain model — ReviewJob, ReviewFinding, ReviewReport, ReviewIntake
2. Analyzers — repo structure, security, diff
3. Verifier — false positive reduction, severity adjustment
4. Service — end-to-end review flow
5. Storage — SQLite persistence and recovery
6. Report export — Markdown + JSON
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from agent.control.models import JobKind
from agent.review.models import (
    ArtifactType,
    Confidence,
    ReviewArtifact,
    ReviewFinding,
    ReviewIntake,
    ReviewJob,
    ReviewJobStatus,
    ReviewJobType,
    ReviewPhase,
    ReviewReport,
    Severity,
)

# ─────────────────────────────────────────────
# Domain Model Tests
# ─────────────────────────────────────────────

class TestReviewIntake:
    def test_valid_repo_audit(self):
        intake = ReviewIntake(repo_path="/tmp/test-repo", review_type=ReviewJobType.REPO_AUDIT)
        assert intake.validate() == []

    def test_missing_repo_path(self):
        intake = ReviewIntake(repo_path="")
        errors = intake.validate()
        assert any("repo_path" in e for e in errors)

    def test_pr_review_requires_diff_spec(self):
        intake = ReviewIntake(repo_path="/tmp/repo", review_type=ReviewJobType.PR_REVIEW)
        errors = intake.validate()
        assert any("diff_spec" in e for e in errors)

    def test_pr_review_with_diff_spec_valid(self):
        intake = ReviewIntake(
            repo_path="/tmp/repo",
            review_type=ReviewJobType.PR_REVIEW,
            diff_spec="main..feature",
        )
        assert intake.validate() == []

    def test_max_files_validation(self):
        intake = ReviewIntake(repo_path="/tmp/repo", max_files=0)
        errors = intake.validate()
        assert any("max_files" in e for e in errors)

    def test_path_traversal_blocked(self):
        intake = ReviewIntake(repo_path="../../etc/passwd")
        errors = intake.validate()
        assert any(".." in e for e in errors)

    def test_system_dir_blocked(self):
        intake = ReviewIntake(repo_path="/etc/nginx")
        errors = intake.validate()
        assert any("system directory" in e for e in errors)

    def test_diff_spec_injection_blocked(self):
        intake = ReviewIntake(
            repo_path="/tmp/repo",
            review_type=ReviewJobType.PR_REVIEW,
            diff_spec="HEAD; rm -rf /",
        )
        errors = intake.validate()
        assert any("invalid characters" in e for e in errors)

    def test_valid_diff_spec_allowed(self):
        intake = ReviewIntake(
            repo_path="/tmp/repo",
            review_type=ReviewJobType.PR_REVIEW,
            diff_spec="main..feature/my-branch",
        )
        errors = intake.validate()
        assert not any("invalid" in e for e in errors)


class TestReviewFinding:
    def test_location_with_range(self):
        f = ReviewFinding(file_path="src/main.py", line_start=10, line_end=20)
        assert f.location == "src/main.py:10-20"

    def test_location_single_line(self):
        f = ReviewFinding(file_path="src/main.py", line_start=42)
        assert f.location == "src/main.py:42"

    def test_location_file_only(self):
        f = ReviewFinding(file_path="src/main.py")
        assert f.location == "src/main.py"

    def test_location_empty(self):
        f = ReviewFinding()
        assert f.location == ""

    def test_to_dict_round_trip(self):
        f = ReviewFinding(
            severity=Severity.HIGH,
            title="Test finding",
            file_path="app.py",
            line_start=5,
            category="security",
            evidence="eval(input)",
        )
        d = f.to_dict()
        assert d["severity"] == "high"
        assert d["title"] == "Test finding"
        assert d["file_path"] == "app.py"


class TestReviewReport:
    def test_finding_counts(self):
        report = ReviewReport(findings=[
            ReviewFinding(severity=Severity.CRITICAL),
            ReviewFinding(severity=Severity.HIGH),
            ReviewFinding(severity=Severity.HIGH),
            ReviewFinding(severity=Severity.LOW),
        ])
        counts = report.finding_counts
        assert counts["critical"] == 1
        assert counts["high"] == 2
        assert counts["medium"] == 0
        assert counts["low"] == 1

    def test_has_critical(self):
        report = ReviewReport(findings=[ReviewFinding(severity=Severity.CRITICAL)])
        assert report.has_critical

    def test_no_critical(self):
        report = ReviewReport(findings=[ReviewFinding(severity=Severity.LOW)])
        assert not report.has_critical

    def test_empty_report(self):
        report = ReviewReport()
        assert report.finding_counts == {"critical": 0, "high": 0, "medium": 0, "low": 0}
        assert not report.has_critical

    def test_to_markdown_has_sections(self):
        report = ReviewReport(
            executive_summary="Test summary",
            findings=[ReviewFinding(
                severity=Severity.HIGH,
                title="Test issue",
                file_path="main.py",
                line_start=10,
                description="Something wrong",
                evidence="bad code here",
                recommendation="Fix it",
            )],
            open_questions=["Is this really bad?"],
            assumptions=["Static analysis only"],
            verdict="pass_with_findings",
        )
        md = report.to_markdown()
        assert "# Review Report" in md
        assert "## Executive Summary" in md
        assert "Test summary" in md
        assert "## Findings" in md
        assert "[HIGH]" in md
        assert "Test issue" in md
        assert "main.py:10" in md
        assert "## Open Questions" in md
        assert "## Assumptions" in md

    def test_to_markdown_no_findings(self):
        report = ReviewReport(executive_summary="All clean", verdict="pass")
        md = report.to_markdown()
        assert "_No findings._" in md

    def test_to_dict_has_all_fields(self):
        report = ReviewReport(
            executive_summary="sum",
            findings=[ReviewFinding(severity=Severity.LOW, title="x")],
            verdict="pass_with_findings",
        )
        d = report.to_dict()
        assert "executive_summary" in d
        assert "findings" in d
        assert "finding_counts" in d
        assert "verdict" in d


class TestReviewJob:
    def test_job_creation(self):
        job = ReviewJob(job_type=ReviewJobType.REPO_AUDIT, requester="daniel")
        assert job.status == ReviewJobStatus.CREATED
        assert job.id
        assert job.requester == "daniel"

    def test_trace(self):
        job = ReviewJob()
        t = job.trace("analyze")
        assert t.step == "analyze"
        assert t.status == "started"
        t.complete("done")
        assert t.status == "completed"
        assert t.duration_ms >= 0
        assert len(job.execution_trace) == 1

    def test_to_dict(self):
        job = ReviewJob(
            job_type=ReviewJobType.PR_REVIEW,
            requester="test",
        )
        d = job.to_dict()
        assert d["job_type"] == "pr_review"
        assert d["status"] == "created"
        assert "findings" in d["report"]

    def test_shared_control_plane_fields_roundtrip(self):
        job = ReviewJob(
            job_type=ReviewJobType.REPO_AUDIT,
            requester="daniel",
        )
        job.phase = ReviewPhase.REPORTING
        job.timing.mark_started()
        job.timing.mark_completed()
        job.total_tokens = 321
        job.total_cost_usd = 0.123
        job.model_used = "gpt-test"

        recovered = ReviewJob.from_dict(job.to_dict())

        assert recovered.job_kind == JobKind.REVIEW
        assert recovered.phase.value == "reporting"
        assert recovered.started_at != ""
        assert recovered.completed_at != ""
        assert recovered.total_tokens == 321
        assert recovered.total_cost_usd == pytest.approx(0.123)
        assert recovered.model_used == "gpt-test"


# ─────────────────────────────────────────────
# Analyzer Tests
# ─────────────────────────────────────────────

class TestRepoStructureAnalyzer:
    @pytest.fixture()
    def sample_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create sample repo structure
            (Path(tmpdir) / "src").mkdir()
            (Path(tmpdir) / "tests").mkdir()
            (Path(tmpdir) / "src" / "main.py").write_text("def main():\n    pass\n")
            (Path(tmpdir) / "src" / "utils.py").write_text("x = 1\ny = 2\n")
            (Path(tmpdir) / "tests" / "test_main.py").write_text("def test_main():\n    pass\n")
            (Path(tmpdir) / "README.md").write_text("# Test\n")
            (Path(tmpdir) / ".gitignore").write_text("*.pyc\n")
            yield tmpdir

    def test_basic_metrics(self, sample_repo):
        from agent.review.analyzers import analyze_repo_structure
        metrics, findings = analyze_repo_structure(sample_repo)
        assert metrics.total_files >= 4
        assert metrics.python_files >= 2
        assert metrics.test_files >= 1
        assert metrics.has_readme
        assert metrics.has_gitignore
        assert metrics.has_tests

    def test_nonexistent_path(self):
        from agent.review.analyzers import analyze_repo_structure
        metrics, findings = analyze_repo_structure("/nonexistent/path")
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL


class TestSecurityAnalyzer:
    @pytest.fixture()
    def repo_with_secrets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "config.py").write_text(
                'API_KEY = "sk-abc123456789abcdef"\n'
                'password = "hunter2secret"\n'
            )
            (Path(tmpdir) / "safe.py").write_text("x = 1\n")
            yield tmpdir

    def test_finds_secrets(self, repo_with_secrets):
        from agent.review.analyzers import analyze_security
        findings = analyze_security(repo_with_secrets)
        assert len(findings) >= 1
        assert any(f.category == "security" for f in findings)

    def test_clean_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "clean.py").write_text("x = 1\n")
            from agent.review.analyzers import analyze_security
            findings = analyze_security(tmpdir)
            assert len(findings) == 0

    @pytest.fixture()
    def repo_with_eval(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "dangerous.py").write_text("result = eval(user_input)\n")
            yield tmpdir

    def test_finds_eval(self, repo_with_eval):
        from agent.review.analyzers import analyze_security
        findings = analyze_security(repo_with_eval)
        assert any("eval" in f.title.lower() for f in findings)


# ─────────────────────────────────────────────
# Verifier Tests
# ─────────────────────────────────────────────

class TestVerifier:
    def test_removes_test_file_false_positives(self):
        from agent.review.verifier import verify_report
        report = ReviewReport(findings=[
            ReviewFinding(
                severity=Severity.CRITICAL,
                title="Possible API key",
                file_path="tests/test_auth.py",
                category="security",
                evidence='API_KEY = "fake-key-for-testing"',
            ),
        ])
        verify_report(report)
        assert len(report.findings) == 0  # Removed — false positive

    def test_keeps_real_findings(self):
        from agent.review.verifier import verify_report
        report = ReviewReport(findings=[
            ReviewFinding(
                severity=Severity.HIGH,
                title="eval() usage",
                file_path="src/main.py",
                category="security",
                evidence="eval(data)",
            ),
        ])
        verify_report(report)
        assert len(report.findings) == 1

    def test_downgrades_critical_without_evidence(self):
        from agent.review.verifier import verify_report
        report = ReviewReport(findings=[
            ReviewFinding(
                severity=Severity.CRITICAL,
                title="Something bad",
                confidence=Confidence.HIGH,
            ),
        ])
        verify_report(report)
        assert report.findings[0].confidence == Confidence.LOW


# ─────────────────────────────────────────────
# Storage Tests
# ─────────────────────────────────────────────

class TestReviewStorage:
    @pytest.fixture()
    def storage(self):
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        s = ReviewStorage(db_path=db_path)
        s.initialize()
        yield s
        s.close()
        os.unlink(db_path)

    def test_save_and_load_job(self, storage):
        job = ReviewJob(job_type=ReviewJobType.REPO_AUDIT, requester="test")
        storage.save_job(job)
        loaded = storage.load_job(job.id)
        assert loaded is not None
        assert loaded["id"] == job.id
        assert loaded["job_type"] == "repo_audit"

    def test_list_jobs(self, storage):
        for i in range(3):
            job = ReviewJob(requester=f"user-{i}")
            storage.save_job(job)
        jobs = storage.list_jobs()
        assert len(jobs) == 3

    def test_save_artifact(self, storage):
        artifact = ReviewArtifact(
            artifact_type=ArtifactType.REVIEW_REPORT,
            job_id="test-job",
            content="# Report\nAll good.",
            format="markdown",
        )
        storage.save_artifact(artifact)
        artifacts = storage.get_artifacts("test-job")
        assert len(artifacts) == 1
        assert artifacts[0]["artifact_type"] == "review_report"
        assert artifacts[0]["format"] == "markdown"

    def test_list_and_get_artifact(self, storage):
        artifact = ReviewArtifact(
            artifact_type=ArtifactType.FINDING_LIST,
            job_id="test-job",
            content_json={"findings": [{"id": "f-1"}]},
            format="json",
        )
        storage.save_artifact(artifact)

        artifacts = storage.list_artifacts(job_id="test-job")
        loaded = storage.get_artifact(artifact.id)

        assert len(artifacts) == 1
        assert artifacts[0]["format"] == "json"
        assert loaded is not None
        assert loaded["content_json"]["findings"][0]["id"] == "f-1"


# ─────────────────────────────────────────────
# Service Integration Tests
# ─────────────────────────────────────────────

class TestReviewService:
    @pytest.fixture()
    def service(self):
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        s = ReviewService(storage=ReviewStorage(db_path=db_path))
        yield s
        os.unlink(db_path)

    @pytest.fixture()
    def sample_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "src").mkdir()
            (Path(tmpdir) / "tests").mkdir()
            (Path(tmpdir) / ".github" / "workflows").mkdir(parents=True)
            (Path(tmpdir) / "src" / "app.py").write_text("def run():\n    return 42\n")
            (Path(tmpdir) / "tests" / "test_app.py").write_text("def test_run():\n    pass\n")
            (Path(tmpdir) / "README.md").write_text("# App\n")
            (Path(tmpdir) / ".github" / "workflows" / "ci.yml").write_text("name: CI\n")
            yield tmpdir

    async def test_repo_audit_end_to_end(self, service, sample_repo):
        """Full repo audit: intake → validate → analyze → verify → report → artifacts."""
        intake = ReviewIntake(
            repo_path=sample_repo,
            review_type=ReviewJobType.REPO_AUDIT,
            requester="daniel",
        )
        job = await service.run_review(intake)

        assert job.status == ReviewJobStatus.COMPLETED
        assert job.report.verdict in ("pass", "pass_with_findings")
        assert job.report.executive_summary
        assert job.report.files_analyzed > 0
        assert len(job.artifacts) >= 3  # Markdown + JSON + trace (+ findings-only if findings exist)
        assert len(job.execution_trace) >= 3  # validate + structure + security + verify + verdict + artifacts

        # Markdown artifact
        md = next(a for a in job.artifacts if a.format == "markdown")
        assert "# Review Report" in md.content

        # JSON artifact
        json_a = next(a for a in job.artifacts if a.format == "json")
        assert json_a.content_json["verdict"] in ("pass", "pass_with_findings")

    async def test_repo_audit_invalid_path(self, service):
        intake = ReviewIntake(repo_path="/nonexistent/repo")
        job = await service.run_review(intake)
        assert job.status == ReviewJobStatus.COMPLETED
        assert job.report.has_critical

    async def test_validation_failure(self, service):
        intake = ReviewIntake(repo_path="")
        job = await service.run_review(intake)
        assert job.status == ReviewJobStatus.FAILED
        assert "Validation failed" in job.error

    async def test_job_persisted(self, service, sample_repo):
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)

        # Should be retrievable
        loaded = service.get_job(job.id)
        assert loaded is not None
        assert loaded["id"] == job.id
        assert loaded["status"] == "completed"

    async def test_release_review(self, service, sample_repo):
        intake = ReviewIntake(
            repo_path=sample_repo,
            review_type=ReviewJobType.RELEASE_REVIEW,
        )
        job = await service.run_review(intake)
        assert job.status == ReviewJobStatus.COMPLETED
        assert "release" in job.report.executive_summary.lower() or "readiness" in job.report.executive_summary.lower()

    async def test_focus_areas(self, service, sample_repo):
        intake = ReviewIntake(
            repo_path=sample_repo,
            focus_areas=["security", "performance"],
        )
        job = await service.run_review(intake)
        assert job.status == ReviewJobStatus.COMPLETED
        assert any("security" in a for a in job.report.assumptions)

    async def test_execution_trace_artifact_created(self, service, sample_repo):
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        trace_artifacts = [a for a in job.artifacts if a.artifact_type == ArtifactType.EXECUTION_TRACE]
        assert len(trace_artifacts) == 1
        assert "trace" in trace_artifacts[0].content_json
        assert len(trace_artifacts[0].content_json["trace"]) >= 3

    async def test_artifacts_linked_to_job(self, service, sample_repo):
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        for artifact in job.artifacts:
            assert artifact.job_id == job.id

    async def test_secrets_redacted_in_evidence(self, service):
        """CRITICAL: evidence must not leak detected secret values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "config.py").write_text('API_KEY = "sk-super-secret-production-key-12345"\n')
            intake = ReviewIntake(repo_path=tmpdir)
            job = await service.run_review(intake)
            for finding in job.report.findings:
                if "secret" in finding.title.lower() or "key" in finding.title.lower():
                    assert "sk-super-secret" not in finding.evidence, "Secret leaked in evidence!"
                    assert "[REDACTED]" in finding.evidence, "Evidence not redacted"

    async def test_finding_has_impact_field(self, service):
        """Findings with security issues should be exportable with impact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "bad.py").write_text('API_KEY = "sk-real-secret-value-here"\n')
            intake = ReviewIntake(repo_path=tmpdir)
            job = await service.run_review(intake)
            if job.report.findings:
                d = job.report.findings[0].to_dict()
                assert "impact" in d  # Field exists even if empty


# ─────────────────────────────────────────────
# Recovery + Reload Tests
# ─────────────────────────────────────────────

class TestReviewRecovery:
    """Review jobs must be fully recoverable from storage."""

    @pytest.fixture()
    def service(self):
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        s = ReviewService(storage=ReviewStorage(db_path=db_path))
        yield s
        os.unlink(db_path)

    @pytest.fixture()
    def sample_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "src").mkdir()
            (Path(tmpdir) / "src" / "app.py").write_text("def run():\n    return 42\n")
            (Path(tmpdir) / "README.md").write_text("# Test\n")
            yield tmpdir

    async def test_full_job_reload_with_intake(self, service, sample_repo):
        """Job reload must include full intake data."""
        intake = ReviewIntake(
            repo_path=sample_repo,
            requester="daniel",
            focus_areas=["security"],
        )
        job = await service.run_review(intake)
        loaded = service.load_job(job.id)
        assert loaded is not None
        assert loaded.intake.repo_path == sample_repo
        assert loaded.intake.requester == "daniel"
        assert loaded.intake.focus_areas == ["security"]
        assert loaded.status == ReviewJobStatus.COMPLETED

    async def test_full_job_reload_preserves_report(self, service, sample_repo):
        """Job reload must preserve full report with findings."""
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        loaded = service.load_job(job.id)
        assert loaded is not None
        assert loaded.report.verdict == job.report.verdict
        assert loaded.report.files_analyzed == job.report.files_analyzed
        assert loaded.report.executive_summary == job.report.executive_summary
        assert len(loaded.report.findings) == len(job.report.findings)

    async def test_full_job_reload_preserves_trace(self, service, sample_repo):
        """Job reload must preserve execution trace."""
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        loaded = service.load_job(job.id)
        assert loaded is not None
        assert len(loaded.execution_trace) == len(job.execution_trace)
        steps = [t.step for t in loaded.execution_trace]
        assert "validate" in steps
        assert "execution_policy" in steps

    async def test_artifact_reload_with_content(self, service, sample_repo):
        """Artifacts must be reloadable with full content."""
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        artifacts = service._storage.get_artifacts(job.id)
        assert len(artifacts) >= 3  # markdown + json + trace
        md = next(a for a in artifacts if a["artifact_type"] == "review_report" and a.get("content"))
        assert "# Review Report" in md["content"]

    async def test_finding_round_trip(self):
        """ReviewFinding.from_dict(to_dict()) must be lossless."""
        f = ReviewFinding(
            severity=Severity.HIGH,
            title="Test finding",
            description="Something wrong",
            impact="Could break production",
            file_path="app.py",
            line_start=42,
            line_end=50,
            category="security",
            evidence="eval(x)",
            recommendation="Remove eval",
            confidence=Confidence.HIGH,
            tags=["security", "critical"],
        )
        d = f.to_dict()
        f2 = ReviewFinding.from_dict(d)
        assert f2.severity == f.severity
        assert f2.title == f.title
        assert f2.impact == f.impact
        assert f2.file_path == f.file_path
        assert f2.line_start == f.line_start
        assert f2.line_end == f.line_end
        assert f2.confidence == f.confidence
        assert f2.tags == f.tags


# ─────────────────────────────────────────────
# Execution Mode Tests
# ─────────────────────────────────────────────

class TestExecutionMode:
    """Execution mode must be explicit, auditable, and honest."""

    @pytest.fixture()
    def service(self):
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        s = ReviewService(storage=ReviewStorage(db_path=db_path))
        yield s
        os.unlink(db_path)

    @pytest.fixture()
    def sample_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("x = 1\n")
            yield tmpdir

    async def test_default_execution_mode_is_read_only(self, service, sample_repo):
        """Without workspace manager, execution mode must be READ_ONLY_HOST."""
        from agent.review.models import ExecutionMode
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        assert job.execution_mode == ExecutionMode.READ_ONLY_HOST

    async def test_execution_mode_in_to_dict(self, service, sample_repo):
        """Execution mode must be serialized."""
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        d = job.to_dict()
        assert d["execution_mode"] == "read_only_host"

    async def test_execution_policy_trace_present(self, service, sample_repo):
        """Trace must contain execution_policy step with mode info."""
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        policy_traces = [t for t in job.execution_trace if t.step == "execution_policy"]
        assert len(policy_traces) == 1
        assert "read_only_host" in policy_traces[0].detail
        assert "host_access=read_only" in policy_traces[0].detail
        assert "policy=repo_host_read_only" in policy_traces[0].detail

    async def test_execution_policy_blocks_unknown_source(self, service, sample_repo):
        intake = ReviewIntake(repo_path=sample_repo, source="unknown_source")
        job = await service.run_review(intake)
        assert job.status == ReviewJobStatus.BLOCKED
        assert "policy" in job.error


# ─────────────────────────────────────────────
# Delivery Bundle Tests
# ─────────────────────────────────────────────

class TestDeliveryBundle:
    """Delivery-ready bundle must be complete and recoverable."""

    @pytest.fixture()
    def service(self):
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        s = ReviewService(storage=ReviewStorage(db_path=db_path))
        yield s
        os.unlink(db_path)

    @pytest.fixture()
    def sample_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "src").mkdir()
            (Path(tmpdir) / "src" / "app.py").write_text("def run():\n    return 42\n")
            (Path(tmpdir) / "README.md").write_text("# App\n")
            yield tmpdir

    async def test_bundle_exists_after_review(self, service, sample_repo):
        intake = ReviewIntake(repo_path=sample_repo, requester="daniel")
        job = await service.run_review(intake)
        bundle = service.get_delivery_bundle(job.id)
        assert bundle is not None
        # Bundle is never delivery-ready without explicit approval
        assert bundle["delivery_ready"] is False
        assert bundle["job_id"] == job.id

    async def test_bundle_contains_markdown(self, service, sample_repo):
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        bundle = service.get_delivery_bundle(job.id)
        assert "# Review Report" in bundle["markdown_report"]

    async def test_bundle_contains_json_report(self, service, sample_repo):
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        bundle = service.get_delivery_bundle(job.id)
        assert bundle["json_report"].get("verdict") in ("pass", "pass_with_findings")
        assert "executive_summary" in bundle["json_report"]

    async def test_bundle_contains_trace(self, service, sample_repo):
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        bundle = service.get_delivery_bundle(job.id)
        assert len(bundle["execution_trace"]) >= 3
        steps = [t["step"] for t in bundle["execution_trace"]]
        assert "validate" in steps

    async def test_bundle_contains_metadata(self, service, sample_repo):
        intake = ReviewIntake(repo_path=sample_repo, requester="daniel")
        job = await service.run_review(intake)
        bundle = service.get_delivery_bundle(job.id)
        assert bundle["requester"] == "daniel"
        assert bundle["execution_mode"] == "read_only_host"
        assert bundle["status"] == "completed"

    async def test_bundle_nonexistent_job(self, service):
        bundle = service.get_delivery_bundle("nonexistent-id")
        assert bundle is None

    async def test_bundle_survives_reload(self, service, sample_repo):
        """Bundle from reloaded job must match original."""
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)
        # Get bundle immediately
        bundle1 = service.get_delivery_bundle(job.id)
        # Reload and get again
        bundle2 = service.get_delivery_bundle(job.id)
        assert bundle1["verdict"] == bundle2["verdict"]
        assert bundle1["markdown_report"] == bundle2["markdown_report"]
        assert bundle1["finding_counts"] == bundle2["finding_counts"]


# ─────────────────────────────────────────────
# PR Review Fixtures
# ─────────────────────────────────────────────

class TestPRReview:
    """PR review with real git repo fixture."""

    @pytest.fixture()
    def service(self):
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        s = ReviewService(storage=ReviewStorage(db_path=db_path))
        yield s
        os.unlink(db_path)

    @pytest.fixture()
    def git_repo(self):
        """Create a real git repo with commits for PR review testing."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmpdir:
            # Init repo
            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)

            # First commit
            (Path(tmpdir) / "app.py").write_text("def main():\n    pass\n")
            subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, capture_output=True)

            # Second commit with changes
            (Path(tmpdir) / "app.py").write_text("def main():\n    return 42\n\ndef helper():\n    pass\n")
            (Path(tmpdir) / "utils.py").write_text("x = 1\n")
            subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add features"], cwd=tmpdir, capture_output=True)

            yield tmpdir

    async def test_pr_review_with_diff(self, service, git_repo):
        """PR review with real git diff."""
        intake = ReviewIntake(
            repo_path=git_repo,
            review_type=ReviewJobType.PR_REVIEW,
            diff_spec="HEAD~1..HEAD",
            requester="daniel",
        )
        job = await service.run_review(intake)
        assert job.status == ReviewJobStatus.COMPLETED
        assert job.report.files_analyzed >= 1
        assert "PR review" in job.report.scope_description or "diff" in job.report.scope_description.lower()

    async def test_pr_review_shows_changed_files(self, service, git_repo):
        intake = ReviewIntake(
            repo_path=git_repo,
            review_type=ReviewJobType.PR_REVIEW,
            diff_spec="HEAD~1..HEAD",
        )
        job = await service.run_review(intake)
        # Should detect at least utils.py (new file) and app.py (modified)
        assert job.report.files_analyzed >= 1

    async def test_pr_review_invalid_diff_spec(self, service, git_repo):
        intake = ReviewIntake(
            repo_path=git_repo,
            review_type=ReviewJobType.PR_REVIEW,
            diff_spec="HEAD; rm -rf /",
        )
        job = await service.run_review(intake)
        assert job.status == ReviewJobStatus.FAILED
        assert "invalid" in job.error.lower()


# ─────────────────────────────────────────────
# Delivery Approval Gating
# ─────────────────────────────────────────────

class TestDeliveryApproval:
    """Review delivery must go through approval gate."""

    @pytest.fixture()
    def service_with_approval(self):
        from agent.core.approval import ApprovalQueue
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        queue = ApprovalQueue()
        s = ReviewService(
            storage=ReviewStorage(db_path=db_path),
            approval_queue=queue,
        )
        yield s, queue
        os.unlink(db_path)

    @pytest.fixture()
    def sample_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("x = 1\n")
            yield tmpdir

    async def test_delivery_approval_creates_request(self, service_with_approval, sample_repo):
        service, queue = service_with_approval
        intake = ReviewIntake(repo_path=sample_repo)
        job = await service.run_review(intake)

        result = service.request_delivery_approval(job.id)
        assert result.get("approval_request_id") is not None
        assert result["approval_status"] == "pending"
        assert result["delivery_ready"] is False

        # Queue should have the request
        pending = queue.get_pending()
        assert len(pending) == 1
        assert pending[0]["context"]["job_id"] == job.id

    async def test_delivery_approval_rejects_incomplete_job(self, service_with_approval, sample_repo):
        service, _ = service_with_approval
        result = service.request_delivery_approval("nonexistent-id")
        assert "error" in result

    async def test_delivery_without_queue_blocks_by_default(self):
        """Without approval queue, delivery is BLOCKED (not bypassed)."""
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        service = ReviewService(storage=ReviewStorage(db_path=db_path))
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("x = 1\n")
            intake = ReviewIntake(repo_path=tmpdir)
            job = await service.run_review(intake)
            result = service.request_delivery_approval(job.id)
            assert result.get("delivery_ready") is False
            assert "error" in result
        os.unlink(db_path)

    async def test_delivery_dev_mode_bypass(self):
        """DEV MODE only: bypass requires explicit env var."""
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        service = ReviewService(storage=ReviewStorage(db_path=db_path))
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("x = 1\n")
            intake = ReviewIntake(repo_path=tmpdir)
            job = await service.run_review(intake)
            import os as _os
            old = _os.environ.get("AGENT_DEV_MODE")
            _os.environ["AGENT_DEV_MODE"] = "1"
            try:
                result = service.request_delivery_approval(job.id)
                assert result.get("approval_bypassed") is True
                assert "DEV MODE" in result.get("warning", "")
            finally:
                if old is None:
                    _os.environ.pop("AGENT_DEV_MODE", None)
                else:
                    _os.environ["AGENT_DEV_MODE"] = old
        os.unlink(db_path)


# ─────────────────────────────────────────────
# Client-Safe Redaction
# ─────────────────────────────────────────────

class TestClientSafeExport:
    """Client-safe export must redact sensitive internal details."""

    @pytest.fixture()
    def service(self):
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        s = ReviewService(storage=ReviewStorage(db_path=db_path))
        yield s
        os.unlink(db_path)

    async def test_client_safe_redacts_paths(self, service):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("x = 1\n")
            intake = ReviewIntake(repo_path=tmpdir)
            job = await service.run_review(intake)
            bundle = service.get_client_safe_bundle(job.id)
            assert bundle is not None
            assert bundle["export_mode"] == "client_safe"
            # Internal paths must be redacted
            assert "/Users/" not in bundle["markdown_report"]
            assert "/home/" not in bundle["markdown_report"]

    async def test_client_safe_strips_trace(self, service):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("x = 1\n")
            intake = ReviewIntake(repo_path=tmpdir)
            job = await service.run_review(intake)
            bundle = service.get_client_safe_bundle(job.id)
            assert "execution_trace" not in bundle
            assert "execution_mode" not in bundle

    async def test_client_safe_nonexistent_job(self, service):
        assert service.get_client_safe_bundle("nope") is None


class TestRedactionPolicy:
    """Redaction policy must be thorough and testable independently."""

    def test_redact_paths(self):
        from agent.review.redaction import redact_paths
        assert "[PATH_REDACTED]" in redact_paths("file at /Users/daniel/code/app.py")
        assert "[PATH_REDACTED]" in redact_paths("found in /home/user/.secret")
        assert "relative/path.py" in redact_paths("relative/path.py")

    def test_redact_hostnames(self):
        from agent.review.redaction import redact_hostnames
        assert "[HOST_REDACTED]" in redact_hostnames("running on b2jk-agentlifespace")
        assert "google.com" in redact_hostnames("connect to google.com")

    def test_redact_secrets(self):
        from agent.review.redaction import redact_secrets
        assert "[SECRET_REDACTED]" in redact_secrets('api_key = "sk-1234567890abcdef"')
        assert "[SECRET_REDACTED]" in redact_secrets("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")

    def test_redact_bundle_strips_internals(self):
        from agent.review.redaction import redact_bundle
        bundle = {
            "markdown_report": "# Report\nPath: /Users/daniel/project\n",
            "findings_only": [{"evidence": "at /home/user/secrets.py", "file_path": "src/app.py"}],
            "json_report": {"scope_description": "/Users/daniel/project", "findings": []},
            "execution_trace": [{"step": "analyze"}],
            "execution_mode": "read_only_host",
            "requester": "daniel",
            "source": "telegram",
        }
        result = redact_bundle(bundle)
        assert "execution_trace" not in result
        assert "execution_mode" not in result
        assert "requester" not in result
        assert "source" not in result
        assert result["export_mode"] == "client_safe"
        assert "/Users/" not in result["markdown_report"]

    def test_redact_finding_description_full_pipeline(self):
        """Finding description must go through full redaction, not just path scrub."""
        from agent.review.redaction import redact_finding
        finding = {
            "description": "Found at /Users/daniel/code on b2jk-server",
            "impact": 'Leaks api_key = "mysecret12345678" to logs',
            "recommendation": "Fix config at /home/user/.env on agent-life-space host",
            "evidence": "",
            "file_path": "src/app.py",
        }
        result = redact_finding(finding)
        assert "/Users/" not in result["description"]
        assert "b2jk" not in result["description"]
        assert "mysecret" not in result["impact"]
        assert "/home/" not in result["recommendation"]
        assert "agent-life-space" not in result["recommendation"]

    def test_redact_bundle_error_field(self):
        """Error field must be redacted in client-safe export."""
        from agent.review.redaction import redact_bundle
        bundle = {
            "error": "Failed at /Users/daniel/project: connection to b2jk-prod refused",
            "markdown_report": "",
            "findings_only": [],
            "json_report": {},
        }
        result = redact_bundle(bundle)
        assert "/Users/" not in result["error"]
        assert "b2jk" not in result["error"]

    def test_git_stderr_leak_in_description(self):
        """Git stderr-style content in description must be redacted."""
        from agent.review.redaction import redact_finding
        finding = {
            "description": "fatal: unable to access '/home/runner/repo/.git/': Permission denied",
            "evidence": "",
            "file_path": "src/app.py",
        }
        result = redact_finding(finding)
        assert "/home/" not in result["description"]

    def test_apply_client_redaction_combined(self):
        from agent.review.redaction import apply_client_redaction
        text = 'Found api_key = "supersecret12345" at /Users/dev/app.py on b2jk-server'
        redacted = apply_client_redaction(text)
        assert "supersecret" not in redacted
        assert "/Users/" not in redacted
        assert "b2jk" not in redacted


# ─────────────────────────────────────────────
# Audit v4 Regression Tests
# ─────────────────────────────────────────────

class TestAuditV4Regressions:
    """Tests for audit findings v4: execution truth, delivery gating,
    client-safe completeness, artifact recovery."""

    async def test_workspace_manager_does_not_set_workspace_bound(self):
        """Even with workspace_manager, execution_mode must be READ_ONLY_HOST
        because v1 analyzers read from host path, not workspace."""
        from unittest.mock import MagicMock

        from agent.review.models import ExecutionMode
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        ws_mgr = MagicMock()
        ws_mgr.create.return_value = MagicMock(id="ws-123")
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        service = ReviewService(
            storage=ReviewStorage(db_path=db_path),
            workspace_manager=ws_mgr,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            intake = ReviewIntake(repo_path=tmpdir)
            job = await service.run_review(intake)
        assert job.execution_mode == ExecutionMode.READ_ONLY_HOST
        assert job.workspace_id == "ws-123"

    async def test_execution_policy_trace_includes_analysis_path(self):
        """Trace must include actual analysis_path, not just mode."""
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        service = ReviewService(storage=ReviewStorage(db_path=db_path))
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            intake = ReviewIntake(repo_path=tmpdir)
            job = await service.run_review(intake)
        policy_traces = [t for t in job.execution_trace if t.step == "execution_policy"]
        assert "analysis_path=" in policy_traces[0].detail

    async def test_delivery_bundle_never_ready_without_approval(self):
        """get_delivery_bundle() must return delivery_ready=False always.
        Delivery requires explicit approval."""
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        service = ReviewService(storage=ReviewStorage(db_path=db_path))
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            intake = ReviewIntake(repo_path=tmpdir)
            job = await service.run_review(intake)
        bundle = service.get_delivery_bundle(job.id)
        assert bundle["delivery_ready"] is False

    def test_client_safe_strips_requester_and_source(self):
        """Client-safe bundle must not contain requester or source."""
        from agent.review.redaction import redact_bundle
        bundle = {
            "requester": "daniel",
            "source": "telegram",
            "execution_mode": "read_only_host",
            "execution_trace": [{"step": "test"}],
            "markdown_report": "",
            "findings_only": [],
            "json_report": {},
        }
        result = redact_bundle(bundle)
        assert "requester" not in result
        assert "source" not in result

    def test_from_dict_hydrates_artifacts(self):
        """ReviewJob.from_dict() must reconstruct artifact metadata."""
        from agent.review.models import ReviewJob
        data = {
            "id": "test-123",
            "job_type": "repo_audit",
            "intake": {"repo_path": "/tmp/test", "review_type": "repo_audit"},
            "artifacts": [
                {"id": "art-1", "artifact_type": "review_report",
                 "job_id": "test-123", "format": "markdown", "created_at": ""},
                {"id": "art-2", "artifact_type": "execution_trace",
                 "job_id": "test-123", "format": "json", "created_at": ""},
            ],
            "execution_trace": [],
            "report": {},
        }
        job = ReviewJob.from_dict(data)
        assert len(job.artifacts) == 2
        assert job.artifacts[0].id == "art-1"
        assert job.artifacts[1].artifact_type.value == "execution_trace"

    def test_job_roundtrip_preserves_include_and_exclude_patterns(self):
        from agent.review.models import ReviewIntake, ReviewJob, ReviewJobType

        job = ReviewJob(
            job_type=ReviewJobType.REPO_AUDIT,
            intake=ReviewIntake(
                repo_path="/tmp/test",
                include_patterns=["*.py"],
                exclude_patterns=["tests/**"],
            ),
        )

        recovered = ReviewJob.from_dict(job.to_dict())

        assert recovered.intake.include_patterns == ["*.py"]
        assert recovered.intake.exclude_patterns == ["tests/**"]

    def test_job_roundtrip_preserves_intake_source(self):
        from agent.review.models import ReviewIntake, ReviewJob

        job = ReviewJob(
            intake=ReviewIntake(
                repo_path="/tmp/test",
                requester="daniel",
                source="telegram",
            ),
            source="telegram",
        )

        recovered = ReviewJob.from_dict(job.to_dict())

        assert recovered.source == "telegram"
        assert recovered.intake.source == "telegram"


# ─────────────────────────────────────────────
# Legacy Deprecation
# ─────────────────────────────────────────────

class TestLegacyDeprecation:
    """Programmer.review_file() must be deprecated."""

    def test_review_file_emits_deprecation_warning(self):
        from agent.brain.programmer import Programmer
        prog = Programmer()
        with pytest.warns(DeprecationWarning, match="ReviewService"):
            # Will fail on file not found, but deprecation warning fires first
            try:
                prog.review_file("nonexistent.py")
            except Exception:
                pass
