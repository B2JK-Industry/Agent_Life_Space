"""Tests for shared control-plane foundation (agent.control.models)."""

import tempfile
from datetime import UTC, datetime, timedelta

from agent.control.models import (
    ArtifactKind,
    ArtifactRef,
    ArtifactRetentionStatus,
    ExecutionMode,
    ExecutionStep,
    JobKind,
    JobStatus,
    JobTiming,
    UsageSummary,
)


class TestJobKindAndStatus:
    def test_job_kinds(self):
        assert JobKind.REVIEW.value == "review"
        assert JobKind.BUILD.value == "build"
        assert JobKind.OPERATE.value == "operate"

    def test_job_statuses(self):
        assert JobStatus.CREATED.value == "created"
        assert JobStatus.RUNNING.value == "running"
        assert JobStatus.COMPLETED.value == "completed"
        assert JobStatus.BLOCKED.value == "blocked"

    def test_execution_modes(self):
        assert ExecutionMode.READ_ONLY_HOST.value == "read_only_host"
        assert ExecutionMode.WORKSPACE_BOUND.value == "workspace_bound"


class TestJobTiming:
    def test_timing_creation(self):
        t = JobTiming()
        assert t.created_at != ""
        assert t.started_at == ""
        assert t.completed_at == ""

    def test_mark_started(self):
        t = JobTiming()
        t.mark_started()
        assert t.started_at != ""

    def test_mark_completed(self):
        t = JobTiming()
        t.mark_started()
        t.mark_completed()
        assert t.completed_at != ""
        assert t.duration_ms is not None
        assert t.duration_ms >= 0

    def test_timing_roundtrip(self):
        t = JobTiming()
        t.mark_started()
        t.mark_completed()
        d = t.to_dict()
        t2 = JobTiming.from_dict(d)
        assert t2.created_at == t.created_at
        assert t2.started_at == t.started_at
        assert t2.completed_at == t.completed_at


class TestExecutionStep:
    def test_step_complete(self):
        s = ExecutionStep(step="validate")
        s.complete("input valid")
        assert s.status == "completed"
        assert s.detail == "input valid"
        assert s.duration_ms >= 0

    def test_step_fail(self):
        s = ExecutionStep(step="build")
        s.fail("compilation error")
        assert s.status == "failed"
        assert s.error == "compilation error"

    def test_step_roundtrip(self):
        s = ExecutionStep(step="test")
        s.complete("all passed")
        d = s.to_dict()
        s2 = ExecutionStep.from_dict(d)
        assert s2.step == "test"
        assert s2.status == "completed"
        assert s2.detail == "all passed"


class TestArtifactRef:
    def test_artifact_kinds(self):
        assert ArtifactKind.PATCH.value == "patch"
        assert ArtifactKind.VERIFICATION_REPORT.value == "verification_report"
        assert ArtifactKind.ACCEPTANCE_REPORT.value == "acceptance_report"
        assert ArtifactKind.DELIVERY_BUNDLE.value == "delivery_bundle"
        assert ArtifactKind.SECURITY_REPORT.value == "security_report"
        assert ArtifactKind.EXECUTIVE_SUMMARY.value == "executive_summary"

    def test_ref_roundtrip(self):
        ref = ArtifactRef(kind=ArtifactKind.PATCH, job_id="job-123")
        d = ref.to_dict()
        ref2 = ArtifactRef.from_dict(d)
        assert ref2.kind == ArtifactKind.PATCH
        assert ref2.job_id == "job-123"


class TestUsageSummary:
    def test_usage_roundtrip(self):
        u = UsageSummary(total_tokens=1000, total_cost_usd=0.05, model_used="test")
        d = u.to_dict()
        u2 = UsageSummary.from_dict(d)
        assert u2.total_tokens == 1000
        assert u2.total_cost_usd == 0.05
        assert u2.model_used == "test"


class TestRetentionState:
    def test_retention_record_expires_under_policy(self):
        from agent.control.state import ControlPlaneStateService
        from agent.control.storage import ControlPlaneStorage

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db:
            state = ControlPlaneStateService(
                storage=ControlPlaneStorage(db_path=db.name)
            )
            expired_created_at = (datetime.now(UTC) - timedelta(days=40)).isoformat()

            state.record_retained_artifact(
                record_id="artifact-1",
                artifact_id="artifact-1",
                job_id="build-1",
                job_kind=JobKind.BUILD,
                artifact_kind=ArtifactKind.EXECUTION_TRACE,
                source_type="build_artifact",
                created_at=expired_created_at,
                content_json={"trace": []},
            )

            record = state.get_retained_artifact("artifact-1")

        assert record is not None
        assert record.retention_policy_id == "operational_trace_30d"
        assert record.status == ArtifactRetentionStatus.EXPIRED

    def test_prune_expired_record_clears_snapshot(self):
        from agent.control.state import ControlPlaneStateService
        from agent.control.storage import ControlPlaneStorage

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db:
            state = ControlPlaneStateService(
                storage=ControlPlaneStorage(db_path=db.name)
            )
            expired_created_at = (datetime.now(UTC) - timedelta(days=40)).isoformat()

            state.record_retained_artifact(
                record_id="artifact-1",
                artifact_id="artifact-1",
                job_id="build-1",
                job_kind=JobKind.BUILD,
                artifact_kind=ArtifactKind.EXECUTION_TRACE,
                source_type="build_artifact",
                created_at=expired_created_at,
                content="trace payload",
                content_json={"trace": ["step"]},
            )

            pruned = state.prune_retained_artifacts(limit=10)
            record = state.get_retained_artifact("artifact-1")

        assert len(pruned) == 1
        assert record is not None
        assert record.status == ArtifactRetentionStatus.PRUNED
        assert record.content == ""
        assert record.content_json == {}
        assert record.recoverable is False
