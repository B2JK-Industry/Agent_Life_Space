"""
Agent Life Space — Workspace Query Surface

Expose workspace records as first-class control-plane joins.
"""

from __future__ import annotations

from typing import Any

from agent.control.models import WorkspaceQueryDetail, WorkspaceQuerySummary


class WorkspaceQueryService:
    """Join workspace records to jobs, artifacts, approvals, and delivery bundles."""

    def __init__(
        self,
        *,
        workspace_manager: Any = None,
        build_service: Any = None,
        review_service: Any = None,
        approval_queue: Any = None,
        control_plane_state: Any = None,
    ) -> None:
        self._workspace_manager = workspace_manager
        self._build_service = build_service
        self._review_service = review_service
        self._approval_queue = approval_queue
        self._control_plane_state = control_plane_state

    def list_workspaces(
        self,
        *,
        status: str = "",
        limit: int = 20,
    ) -> list[WorkspaceQuerySummary]:
        if self._workspace_manager is None:
            return []
        records = []
        for workspace in self._workspace_manager.list_workspaces():
            if status and workspace.status.value != status:
                continue
            records.append(self._summary(workspace))
        records.sort(key=lambda item: item.created_at, reverse=True)
        return records[:limit]

    def get_workspace(self, workspace_id: str) -> WorkspaceQueryDetail | None:
        if self._workspace_manager is None:
            return None
        workspace = self._workspace_manager.get(workspace_id)
        if workspace is None:
            return None
        summary = self._summary(workspace)
        return WorkspaceQueryDetail(
            **summary.__dict__,
            path=workspace.path,
            commands_run=list(workspace.commands_run),
            files_created=list(workspace.files_created),
            output=workspace.output,
            error=workspace.error,
            audit_trail=self._workspace_manager.get_audit_trail(workspace_id),
        )

    def _summary(self, workspace: Any) -> WorkspaceQuerySummary:
        job_ids = self._workspace_job_ids(workspace.id)
        artifact_ids = self._workspace_artifact_ids(job_ids)
        approval_ids = self._workspace_approval_ids(workspace.id)
        bundle_ids = self._workspace_bundle_ids(workspace.id)
        return WorkspaceQuerySummary(
            workspace_id=workspace.id,
            name=workspace.name,
            status=workspace.status.value,
            created_at=workspace.created_at,
            completed_at=workspace.completed_at or "",
            task_id=workspace.task_id,
            owner_id=workspace.owner_id,
            job_ids=job_ids,
            artifact_ids=artifact_ids,
            approval_ids=approval_ids,
            bundle_ids=bundle_ids,
        )

    def _workspace_job_ids(self, workspace_id: str) -> list[str]:
        job_ids: list[str] = []
        if self._build_service is not None:
            for item in self._build_service.list_jobs(limit=500):
                if item.get("workspace_id") == workspace_id:
                    job_ids.append(item.get("id", ""))
        if self._review_service is not None:
            for item in self._review_service.list_jobs(limit=500):
                if item.get("workspace_id") == workspace_id:
                    job_ids.append(item.get("id", ""))
        return [job_id for job_id in job_ids if job_id]

    def _workspace_artifact_ids(self, job_ids: list[str]) -> list[str]:
        artifact_ids: list[str] = []
        for job_id in job_ids:
            if self._build_service is not None:
                artifact_ids.extend(
                    artifact.get("id", "")
                    for artifact in self._build_service.list_artifacts(
                        job_id=job_id,
                        limit=500,
                    )
                )
            if self._review_service is not None:
                artifact_ids.extend(
                    artifact.get("id", "")
                    for artifact in self._review_service.list_artifacts(
                        job_id=job_id,
                        limit=500,
                    )
                )
        deduped: list[str] = []
        for artifact_id in artifact_ids:
            if artifact_id and artifact_id not in deduped:
                deduped.append(artifact_id)
        return deduped

    def _workspace_approval_ids(self, workspace_id: str) -> list[str]:
        if self._approval_queue is None:
            return []
        return [
            item.get("id", "")
            for item in self._approval_queue.list_requests(
                workspace_id=workspace_id,
                limit=500,
            )
            if item.get("id")
        ]

    def _workspace_bundle_ids(self, workspace_id: str) -> list[str]:
        if self._control_plane_state is None:
            return []
        return [
            item.bundle_id
            for item in self._control_plane_state.list_deliveries(
                workspace_id=workspace_id,
                limit=200,
            )
        ]
