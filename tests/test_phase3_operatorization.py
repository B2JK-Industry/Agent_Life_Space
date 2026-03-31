"""
Tests for v1.25.0 Phase 3 operatorization closure:
recurring workflows, pipelines, margin tracking, channel policy fix.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent.control.models import (
    JobKind,
    JobPipeline,
    PipelineStage,
    ProductJobRecord,
    RecurringWorkflow,
    UsageSummary,
)
from agent.control.pipeline import PipelineOrchestrator
from agent.control.recurring import RecurringWorkflowManager, compute_next_run
from agent.social.channel_policy import (
    ChannelTrustLevel,
    ResponseClass,
    can_send_response,
    get_channel_capabilities,
)

# ─────────────────────────────────────────────
# RecurringWorkflow model tests
# ─────────────────────────────────────────────

class TestRecurringWorkflowModel:

    def test_default_values(self):
        w = RecurringWorkflow()
        assert w.status == "active"
        assert w.run_count == 0
        assert w.workflow_id  # auto-generated

    def test_roundtrip(self):
        w = RecurringWorkflow(
            name="weekly-audit",
            job_kind=JobKind.REVIEW,
            schedule="weekly",
            intake_template={"repo_path": "."},
        )
        d = w.to_dict()
        restored = RecurringWorkflow.from_dict(d)
        assert restored.name == "weekly-audit"
        assert restored.schedule == "weekly"
        assert restored.job_kind == JobKind.REVIEW


class TestRecurringWorkflowManager:

    def test_create_workflow(self):
        mgr = RecurringWorkflowManager()
        w = mgr.create(
            name="daily-review",
            job_kind="review",
            schedule="daily",
            intake_template={"repo_path": "."},
        )
        assert w.name == "daily-review"
        assert w.status == "active"
        assert w.next_run_at  # computed

    def test_list_workflows(self):
        mgr = RecurringWorkflowManager()
        mgr.create(name="w1", job_kind="review", schedule="daily", intake_template={})
        mgr.create(name="w2", job_kind="build", schedule="weekly", intake_template={})
        assert len(mgr.list_workflows()) == 2

    def test_pause_and_activate(self):
        mgr = RecurringWorkflowManager()
        w = mgr.create(name="test", job_kind="review", schedule="daily", intake_template={})
        assert mgr.pause(w.workflow_id)
        assert mgr.get(w.workflow_id).status == "paused"
        assert mgr.activate(w.workflow_id)
        assert mgr.get(w.workflow_id).status == "active"

    def test_get_due_workflows(self):
        mgr = RecurringWorkflowManager()
        w = mgr.create(name="due", job_kind="review", schedule="daily", intake_template={})
        # Set next_run_at to past
        w.next_run_at = "2020-01-01T00:00:00"
        assert len(mgr.get_due_workflows()) == 1

    def test_record_execution_success(self):
        mgr = RecurringWorkflowManager()
        w = mgr.create(name="test", job_kind="review", schedule="daily", intake_template={})
        mgr.record_execution(w.workflow_id, job_id="job-123", success=True)
        assert w.run_count == 1
        assert w.last_job_id == "job-123"
        assert w.error_count == 0

    def test_record_execution_failure_auto_pause(self):
        mgr = RecurringWorkflowManager()
        w = mgr.create(name="test", job_kind="review", schedule="daily", intake_template={})
        w.max_consecutive_errors = 2
        mgr.record_execution(w.workflow_id, success=False, error="timeout")
        assert w.error_count == 1
        assert w.status == "active"
        mgr.record_execution(w.workflow_id, success=False, error="timeout")
        assert w.status == "failed"

    def test_compute_next_run(self):
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        daily = compute_next_run("daily", base)
        assert "2026-01-02" in daily
        weekly = compute_next_run("weekly", base)
        assert "2026-01-08" in weekly


# ─────────────────────────────────────────────
# Pipeline model tests
# ─────────────────────────────────────────────

class TestPipelineModels:

    def test_pipeline_stage_defaults(self):
        s = PipelineStage()
        assert s.condition == "on_success"
        assert s.status == "pending"

    def test_pipeline_roundtrip(self):
        p = JobPipeline(
            name="review-then-build",
            stages=[
                PipelineStage(name="review", job_kind=JobKind.REVIEW),
                PipelineStage(name="build", job_kind=JobKind.BUILD, condition="on_success"),
            ],
        )
        d = p.to_dict()
        restored = JobPipeline.from_dict(d)
        assert restored.name == "review-then-build"
        assert len(restored.stages) == 2
        assert restored.stages[1].condition == "on_success"


class TestPipelineOrchestrator:

    def test_create_pipeline(self):
        orch = PipelineOrchestrator()
        p = orch.create_pipeline(
            name="test-pipeline",
            stages=[
                {"name": "review", "job_kind": "review", "intake_template": {"repo_path": "."}},
                {"name": "build", "job_kind": "build", "intake_template": {"repo_path": "."}},
            ],
        )
        assert p.name == "test-pipeline"
        assert len(p.stages) == 2
        assert p.status == "draft"

    def test_list_pipelines(self):
        orch = PipelineOrchestrator()
        orch.create_pipeline(name="p1", stages=[{"name": "s1", "job_kind": "review"}])
        orch.create_pipeline(name="p2", stages=[{"name": "s1", "job_kind": "build"}])
        assert len(orch.list_pipelines()) == 2

    def test_should_execute_stage(self):
        assert PipelineOrchestrator._should_execute_stage("always", "failed")
        assert PipelineOrchestrator._should_execute_stage("on_success", "completed")
        assert not PipelineOrchestrator._should_execute_stage("on_success", "failed")
        assert PipelineOrchestrator._should_execute_stage("on_failure", "failed")
        assert not PipelineOrchestrator._should_execute_stage("on_failure", "completed")

    async def test_execute_pipeline_no_agent(self):
        orch = PipelineOrchestrator(agent=None)
        p = orch.create_pipeline(
            name="test",
            stages=[{"name": "review", "job_kind": "review", "intake_template": {"repo_path": "."}}],
        )
        result = await orch.execute_pipeline(p.pipeline_id)
        assert not result["ok"]

    async def test_execute_nonexistent_pipeline(self):
        orch = PipelineOrchestrator()
        result = await orch.execute_pipeline("nonexistent")
        assert not result["ok"]


# ─────────────────────────────────────────────
# Margin tracking tests
# ─────────────────────────────────────────────

class TestMarginFields:

    def test_product_job_record_has_margin_fields(self):
        job = ProductJobRecord(
            job_id="j1",
            job_kind=JobKind.REVIEW,
            title="test",
            status="completed",
            usage=UsageSummary(total_cost_usd=0.05),
            revenue_usd=0.10,
        )
        assert job.revenue_usd == 0.10
        assert job.margin_usd == 0.0  # Not auto-calculated in model
        d = job.to_dict()
        assert "revenue_usd" in d
        assert "margin_usd" in d

    def test_product_job_record_roundtrip_with_margin(self):
        job = ProductJobRecord(
            job_id="j1",
            job_kind=JobKind.BUILD,
            title="test",
            status="completed",
            revenue_usd=1.0,
            margin_usd=0.95,
            revenue_source="customer",
            pipeline_id="pipe-1",
            workflow_id="wf-1",
        )
        d = job.to_dict()
        restored = ProductJobRecord.from_dict(d)
        assert restored.revenue_usd == 1.0
        assert restored.margin_usd == 0.95
        assert restored.pipeline_id == "pipe-1"
        assert restored.workflow_id == "wf-1"


# ─────────────────────────────────────────────
# Bug #4: Channel policy fix
# ─────────────────────────────────────────────

class TestChannelPolicyFix:

    def test_private_owner_gets_full_trust(self):
        caps = get_channel_capabilities("private", is_owner=True)
        assert caps.trust_level == ChannelTrustLevel.FULL

    def test_private_nonowner_gets_safe_mode(self):
        caps = get_channel_capabilities("private", is_owner=False)
        assert caps.trust_level == ChannelTrustLevel.SAFE_MODE

    def test_group_gets_safe_mode(self):
        caps = get_channel_capabilities("group", is_owner=True, is_group=True)
        assert caps.trust_level == ChannelTrustLevel.SAFE_MODE

    def test_supergroup_gets_safe_mode(self):
        caps = get_channel_capabilities("supergroup", is_owner=False, is_group=True)
        assert caps.trust_level == ChannelTrustLevel.SAFE_MODE

    def test_internal_allowed_for_owner_private(self):
        caps = get_channel_capabilities("private", is_owner=True)
        assert can_send_response(ResponseClass.INTERNAL, caps)

    def test_internal_blocked_for_api(self):
        caps = get_channel_capabilities("agent_api")
        assert not can_send_response(ResponseClass.INTERNAL, caps)
