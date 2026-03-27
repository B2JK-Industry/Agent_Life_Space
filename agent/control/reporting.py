"""
Agent Life Space — Operator Reporting Surface

Builds a compact operator-facing report over shared job queries and approvals.
"""

from __future__ import annotations

from typing import Any


class OperatorReportService:
    """Assemble a simple inbox/report view for the operator surface."""

    def __init__(
        self,
        job_queries: Any,
        artifact_queries: Any = None,
        approval_queue: Any = None,
        operator_controls: Any = None,
        status_provider: Any = None,
        control_plane_state: Any = None,
        workspace_queries: Any = None,
    ) -> None:
        self._job_queries = job_queries
        self._artifact_queries = artifact_queries
        self._approval_queue = approval_queue
        self._operator_controls = operator_controls
        self._status_provider = status_provider
        self._control_plane_state = control_plane_state
        self._workspace_queries = workspace_queries

    def get_report(self, limit: int = 20) -> dict[str, Any]:
        jobs = [job.to_dict() for job in self._job_queries.list_jobs(limit=limit)]
        artifacts = (
            [artifact.to_dict() for artifact in self._artifact_queries.list_artifacts(limit=limit)]
            if self._artifact_queries is not None
            else []
        )
        blocked_jobs = [
            job for job in jobs
            if job["status"] in {"blocked", "failed", "dead_lettered"}
        ][:limit]
        pending_approvals = (
            self._approval_queue.get_pending()
            if self._approval_queue is not None
            else []
        )
        controls = (
            self._operator_controls.get_status()
            if self._operator_controls is not None
            else {}
        )
        agent_status = self._status_provider() if callable(self._status_provider) else {}
        workspace_health = agent_status.get("workspaces", {})
        worker_execution = agent_status.get("worker_execution", {})
        recent_plans = (
            [plan.to_dict() for plan in self._control_plane_state.list_plans(limit=limit)]
            if self._control_plane_state is not None
            else []
        )
        recent_traces = (
            [trace.to_dict() for trace in self._control_plane_state.list_traces(limit=limit)]
            if self._control_plane_state is not None
            else []
        )
        recent_deliveries = (
            [delivery.to_dict() for delivery in self._control_plane_state.list_deliveries(limit=limit)]
            if self._control_plane_state is not None
            else []
        )
        recent_workspace_records = (
            [record.to_dict() for record in self._workspace_queries.list_workspaces(limit=limit)]
            if self._workspace_queries is not None
            else []
        )

        inbox: list[dict[str, Any]] = []
        for approval in pending_approvals[:limit]:
            inbox.append(
                {
                    "kind": "approval",
                    "id": approval["id"],
                    "status": approval["status"],
                    "title": approval["description"],
                    "detail": approval["reason"],
                }
            )
        for job in blocked_jobs[:limit]:
            inbox.append(
                {
                    "kind": "job_attention",
                    "id": job["job_id"],
                    "status": job["status"],
                    "title": job["title"],
                    "detail": job["blocked_reason"] or job["outcome"],
                }
            )
        failed_workspaces = workspace_health.get("by_status", {}).get("failed", 0)
        if failed_workspaces:
            inbox.append(
                {
                    "kind": "workspace_attention",
                    "id": "workspace_health",
                    "status": "failed",
                    "title": "Workspace failures need review",
                    "detail": f"{failed_workspaces} failed workspaces recorded",
                }
            )
        if worker_execution.get("circuit_breaker_open"):
            inbox.append(
                {
                    "kind": "worker_attention",
                    "id": "worker_execution",
                    "status": "blocked",
                    "title": "Worker circuit breaker is open",
                    "detail": "Job runner is currently rejecting new work due to recent failures.",
                }
            )
        for delivery in recent_deliveries[:limit]:
            if delivery["status"] in {"awaiting_approval", "rejected"}:
                inbox.append(
                    {
                        "kind": "delivery_attention",
                        "id": delivery["bundle_id"],
                        "status": delivery["status"],
                        "title": delivery["title"],
                        "detail": delivery["events"][-1]["detail"] if delivery["events"] else "",
                    }
                )

        return {
            "summary": {
                "total_jobs": len(jobs),
                "total_artifacts": len(artifacts),
                "blocked_jobs": len(blocked_jobs),
                "pending_approvals": len(pending_approvals),
                "persisted_plans": len(recent_plans),
                "recent_traces": len(recent_traces),
                "delivery_records": len(recent_deliveries),
                "disabled_tools": controls.get("total_disabled", 0),
                "active_workspaces": workspace_health.get("by_status", {}).get("active", 0),
                "active_workers": worker_execution.get("active_jobs", 0),
            },
            "inbox": inbox[:limit],
            "recent_jobs": jobs[:limit],
            "recent_artifacts": artifacts[:limit],
            "recent_plans": recent_plans[:limit],
            "recent_traces": recent_traces[:limit],
            "recent_deliveries": recent_deliveries[:limit],
            "recent_workspace_records": recent_workspace_records[:limit],
            "pending_approvals": pending_approvals[:limit],
            "controls": controls,
            "workspace_health": workspace_health,
            "worker_execution": worker_execution,
            "agent_status": agent_status,
        }
