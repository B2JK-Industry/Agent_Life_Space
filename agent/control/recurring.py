"""
Agent Life Space — Recurring Workflow Manager

Enables cron-triggered product jobs: "every Monday run security audit on repo X".
Workflows are persisted in the control plane and checked periodically by AgentCron.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from agent.control.models import JobKind, RecurringWorkflow

logger = structlog.get_logger(__name__)

# Schedule name → timedelta
_SCHEDULE_INTERVALS: dict[str, timedelta] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
}


def compute_next_run(schedule: str, from_time: datetime | None = None) -> str:
    """Compute next run time from a schedule string.

    Supports: "daily", "weekly", "monthly", "hourly".
    Returns ISO timestamp string.
    """
    base = from_time or datetime.now(UTC)
    interval = _SCHEDULE_INTERVALS.get(schedule.lower())
    if interval is None:
        # Unknown schedule — default to daily
        interval = timedelta(days=1)
    return (base + interval).isoformat()


class RecurringWorkflowManager:
    """Manages recurring workflow definitions and execution scheduling."""

    def __init__(self, control_plane_state: Any = None) -> None:
        self._state = control_plane_state
        self._workflows: dict[str, RecurringWorkflow] = {}

    def create(
        self,
        *,
        name: str,
        job_kind: JobKind | str,
        schedule: str,
        intake_template: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> RecurringWorkflow:
        """Create a new recurring workflow."""
        kind = job_kind if isinstance(job_kind, JobKind) else JobKind(str(job_kind))
        workflow = RecurringWorkflow(
            name=name,
            job_kind=kind,
            schedule=schedule,
            intake_template=dict(intake_template),
            next_run_at=compute_next_run(schedule),
            metadata=metadata or {},
        )
        self._workflows[workflow.workflow_id] = workflow
        logger.info(
            "recurring_workflow_created",
            workflow_id=workflow.workflow_id,
            name=name,
            schedule=schedule,
        )
        return workflow

    def get(self, workflow_id: str) -> RecurringWorkflow | None:
        return self._workflows.get(workflow_id)

    def list_workflows(self, status: str = "") -> list[RecurringWorkflow]:
        workflows = list(self._workflows.values())
        if status:
            workflows = [w for w in workflows if w.status == status]
        return sorted(workflows, key=lambda w: w.created_at, reverse=True)

    def pause(self, workflow_id: str) -> bool:
        workflow = self._workflows.get(workflow_id)
        if workflow and workflow.status == "active":
            workflow.status = "paused"
            return True
        return False

    def activate(self, workflow_id: str) -> bool:
        workflow = self._workflows.get(workflow_id)
        if workflow and workflow.status in ("paused", "failed"):
            workflow.status = "active"
            workflow.error_count = 0
            workflow.next_run_at = compute_next_run(workflow.schedule)
            return True
        return False

    def get_due_workflows(self) -> list[RecurringWorkflow]:
        """Return active workflows whose next_run_at has passed."""
        now = datetime.now(UTC).isoformat()
        return [
            w for w in self._workflows.values()
            if w.status == "active" and w.next_run_at and w.next_run_at <= now
        ]

    def record_execution(
        self,
        workflow_id: str,
        *,
        job_id: str = "",
        success: bool = True,
        error: str = "",
    ) -> None:
        """Record the result of a workflow execution."""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return
        now = datetime.now(UTC)
        workflow.last_run_at = now.isoformat()
        workflow.run_count += 1
        if success:
            workflow.last_job_id = job_id
            workflow.error_count = 0
            workflow.next_run_at = compute_next_run(workflow.schedule, now)
        else:
            workflow.error_count += 1
            if workflow.error_count >= workflow.max_consecutive_errors:
                workflow.status = "failed"
                logger.warning(
                    "recurring_workflow_failed",
                    workflow_id=workflow_id,
                    error_count=workflow.error_count,
                    error=error,
                )
            else:
                workflow.next_run_at = compute_next_run(workflow.schedule, now)
        logger.info(
            "recurring_workflow_executed",
            workflow_id=workflow_id,
            success=success,
            run_count=workflow.run_count,
        )
