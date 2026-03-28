"""
Agent Life Space — Evidence Export

Assemble compliance-friendly evidence packages over shared control-plane state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class EvidenceExportService:
    """Build evidence packages for review/build jobs and their linked records."""

    def __init__(
        self,
        *,
        job_queries: Any,
        artifact_queries: Any,
        control_plane_state: Any,
        workspace_queries: Any = None,
        approval_queue: Any = None,
        runtime_model: Any = None,
    ) -> None:
        self._job_queries = job_queries
        self._artifact_queries = artifact_queries
        self._control_plane_state = control_plane_state
        self._workspace_queries = workspace_queries
        self._approval_queue = approval_queue
        self._runtime_model = runtime_model

    def export_job(self, job_id: str, *, kind: str | None = None) -> dict[str, Any]:
        """Assemble a full evidence package for one job id."""
        persisted_job = self._control_plane_state.get_product_job(job_id)
        live_job = self._job_queries.get_job(job_id=job_id, kind=kind)
        if persisted_job is None and live_job is None:
            return {"error": f"Job '{job_id}' not found"}

        normalized_kind = (
            persisted_job.job_kind.value
            if persisted_job is not None
            else (live_job.job_kind.value if live_job is not None else "")
        )
        artifacts = [
            artifact.to_dict()
            for artifact in self._artifact_queries.list_artifacts(
                kind=normalized_kind or None,
                job_id=job_id,
                limit=500,
            )
        ]
        retained = [
            record.to_dict()
            for record in self._control_plane_state.list_retained_artifacts(
                job_id=job_id,
                limit=500,
            )
        ]
        traces = [
            trace.to_dict()
            for trace in self._control_plane_state.list_traces(
                job_id=job_id,
                limit=500,
            )
        ]
        deliveries = [
            record.to_dict()
            for record in self._control_plane_state.list_deliveries(
                job_id=job_id,
                limit=100,
            )
        ]
        cost_entries = [
            entry.to_dict()
            for entry in self._control_plane_state.list_cost_entries(
                job_id=job_id,
                limit=100,
            )
        ]
        approvals = (
            self._approval_queue.list_requests(job_id=job_id, limit=200)
            if self._approval_queue is not None
            else []
        )
        workspaces = []
        if self._workspace_queries is not None:
            for record in self._workspace_queries.list_workspaces(limit=500):
                if job_id in record.job_ids:
                    workspaces.append(record.to_dict())

        artifact_traceability = [
            self._traceability_row(
                artifact=artifact,
                retained=retained,
                deliveries=deliveries,
                approvals=approvals,
                workspaces=workspaces,
            )
            for artifact in artifacts
        ]

        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "job_id": job_id,
            "job_kind": normalized_kind,
            "summary": {
                "artifact_count": len(artifacts),
                "retained_record_count": len(retained),
                "trace_count": len(traces),
                "delivery_count": len(deliveries),
                "approval_count": len(approvals),
                "workspace_count": len(workspaces),
                "cost_entry_count": len(cost_entries),
                "recorded_cost_usd": round(
                    sum(item["usage"]["total_cost_usd"] for item in cost_entries),
                    6,
                ),
            },
            "persisted_job": persisted_job.to_dict() if persisted_job is not None else None,
            "live_job": live_job.to_dict() if live_job is not None else None,
            "artifacts": artifacts,
            "retained_artifacts": retained,
            "traces": traces,
            "deliveries": deliveries,
            "approvals": approvals,
            "workspaces": workspaces,
            "cost_entries": cost_entries,
            "artifact_traceability": artifact_traceability,
            "runtime_model": (
                self._runtime_model.get_model()
                if self._runtime_model is not None
                else {}
            ),
        }

    def export_job_markdown(self, job_id: str, *, kind: str | None = None) -> str:
        """Render a compact markdown evidence summary."""
        package = self.export_job(job_id, kind=kind)
        if package.get("error"):
            return f"# Evidence Export\n\nError: {package['error']}\n"

        lines = [
            "# Evidence Export",
            "",
            f"- Job ID: `{package['job_id']}`",
            f"- Job Kind: `{package['job_kind']}`",
            f"- Exported At: `{package['exported_at']}`",
            f"- Artifacts: `{package['summary']['artifact_count']}`",
            f"- Traces: `{package['summary']['trace_count']}`",
            f"- Deliveries: `{package['summary']['delivery_count']}`",
            f"- Approvals: `{package['summary']['approval_count']}`",
            f"- Workspaces: `{package['summary']['workspace_count']}`",
            f"- Recorded Cost: `${package['summary']['recorded_cost_usd']:.4f}`",
            "",
            "## Artifact Traceability",
            "",
        ]
        if not package["artifact_traceability"]:
            lines.append("_No linked artifacts._")
        else:
            for row in package["artifact_traceability"]:
                lines.append(
                    f"- `{row['artifact_id']}` `{row['artifact_kind']}` "
                    f"retention=`{row['retention_status'] or 'none'}` "
                    f"recoverable=`{row['recoverable']}` "
                    f"deliveries=`{len(row['bundle_ids'])}` approvals=`{len(row['approval_ids'])}` "
                    f"workspaces=`{len(row['workspace_ids'])}`"
                )
        return "\n".join(lines) + "\n"

    def _traceability_row(
        self,
        *,
        artifact: dict[str, Any],
        retained: list[dict[str, Any]],
        deliveries: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
        workspaces: list[dict[str, Any]],
    ) -> dict[str, Any]:
        artifact_id = artifact.get("artifact_id", "")
        retention = next(
            (
                record
                for record in retained
                if record.get("artifact_id") == artifact_id or record.get("record_id") == artifact_id
            ),
            {},
        )
        bundle_ids = [
            record.get("bundle_id", "")
            for record in deliveries
            if artifact_id in record.get("artifact_ids", [])
        ]
        approval_ids = [
            record.get("id", "")
            for record in approvals
            if artifact_id in record.get("context", {}).get("artifact_ids", [])
        ]
        workspace_ids = [
            record.get("workspace_id", "")
            for record in workspaces
            if artifact_id in record.get("artifact_ids", [])
        ]
        return {
            "artifact_id": artifact_id,
            "artifact_kind": artifact.get("artifact_kind", ""),
            "retention_record_id": retention.get("record_id", ""),
            "retention_status": retention.get("status", ""),
            "retention_policy_id": retention.get("retention_policy_id", ""),
            "recoverable": bool(retention.get("recoverable", False)),
            "bundle_ids": [bundle_id for bundle_id in bundle_ids if bundle_id],
            "approval_ids": [approval_id for approval_id in approval_ids if approval_id],
            "workspace_ids": [workspace_id for workspace_id in workspace_ids if workspace_id],
        }
