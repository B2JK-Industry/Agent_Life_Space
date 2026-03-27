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
    ) -> None:
        self._job_queries = job_queries
        self._artifact_queries = artifact_queries
        self._approval_queue = approval_queue
        self._operator_controls = operator_controls
        self._status_provider = status_provider

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

        return {
            "summary": {
                "total_jobs": len(jobs),
                "total_artifacts": len(artifacts),
                "blocked_jobs": len(blocked_jobs),
                "pending_approvals": len(pending_approvals),
                "disabled_tools": controls.get("total_disabled", 0),
                "active_workspaces": workspace_health.get("by_status", {}).get("active", 0),
                "active_workers": worker_execution.get("active_jobs", 0),
            },
            "inbox": inbox[:limit],
            "recent_jobs": jobs[:limit],
            "recent_artifacts": artifacts[:limit],
            "pending_approvals": pending_approvals[:limit],
            "controls": controls,
            "workspace_health": workspace_health,
            "worker_execution": worker_execution,
            "agent_status": agent_status,
        }
