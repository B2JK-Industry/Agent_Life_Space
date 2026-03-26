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

from agent.review.models import (
    ArtifactType,
    Confidence,
    ReviewArtifact,
    ReviewFinding,
    ReviewIntake,
    ReviewJob,
    ReviewJobStatus,
    ReviewJobType,
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
        )
        storage.save_artifact(artifact)
        artifacts = storage.get_artifacts("test-job")
        assert len(artifacts) == 1
        assert artifacts[0]["artifact_type"] == "review_report"


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
        assert bundle["delivery_ready"] is True
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
