"""
Agent Life Space — Control-Plane State Service

Shared durable state for planner handoff, planning traces,
and delivery lifecycle records.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent.control.models import (
    DeliveryEvent,
    DeliveryLifecycleStatus,
    DeliveryPackage,
    DeliveryRecord,
    ExecutionTraceRecord,
    JobPlanRecord,
    PlanRecordStatus,
    TraceRecordKind,
)
from agent.control.storage import ControlPlaneStorage


class ControlPlaneStateService:
    """High-level API over persisted control-plane records."""

    def __init__(self, storage: ControlPlaneStorage | None = None) -> None:
        self._storage = storage or ControlPlaneStorage()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        self._storage.initialize()
        self._initialized = True

    def record_plan(
        self,
        *,
        intake: dict[str, Any],
        qualification: dict[str, Any],
        plan: dict[str, Any],
        status: PlanRecordStatus,
        linked_job_id: str = "",
    ) -> JobPlanRecord:
        self.initialize()
        now = datetime.now(UTC).isoformat()
        existing = self.get_plan(plan.get("id", ""))
        created_at = existing.created_at if existing is not None else now
        record = JobPlanRecord(
            plan_id=plan.get("id", ""),
            status=status,
            title=plan.get("title", ""),
            resolved_work_type=plan.get("resolved_work_type", ""),
            requester=intake.get("requester", ""),
            repo_path=intake.get("repo_path", ""),
            git_url=intake.get("git_url", ""),
            linked_job_id=linked_job_id or (existing.linked_job_id if existing else ""),
            created_at=created_at,
            updated_at=now,
            intake=dict(intake),
            qualification=dict(qualification),
            plan=dict(plan),
        )
        self._storage.save_plan_record(record)
        return record

    def capture_plan_traces(self, record: JobPlanRecord) -> list[ExecutionTraceRecord]:
        self.initialize()
        traces = [
            ExecutionTraceRecord(
                trace_kind=TraceRecordKind.QUALIFICATION,
                title="Qualification decision",
                detail=(
                    f"supported={record.qualification.get('supported', False)}; "
                    f"resolved_work_type={record.qualification.get('resolved_work_type', '')}; "
                    f"scope_size={record.qualification.get('scope_size', '')}; "
                    f"risk_level={record.qualification.get('risk_level', '')}"
                ),
                plan_id=record.plan_id,
                metadata={
                    "warnings": record.qualification.get("warnings", []),
                    "blockers": record.qualification.get("blockers", []),
                    "reasons": record.qualification.get("reasons", []),
                    "risk_factors": record.qualification.get("risk_factors", []),
                },
            ),
            ExecutionTraceRecord(
                trace_kind=TraceRecordKind.BUDGET,
                title="Budget envelope decision",
                detail=(
                    f"tier={record.plan.get('budget_envelope', '')}; "
                    f"estimated_cost_usd={record.plan.get('budget', {}).get('estimated_cost_usd', 0)}; "
                    f"within_budget={record.plan.get('budget', {}).get('within_budget', False)}"
                ),
                plan_id=record.plan_id,
                metadata=record.plan.get("budget", {}),
            ),
            ExecutionTraceRecord(
                trace_kind=TraceRecordKind.CAPABILITY,
                title="Capability assignments",
                detail=(
                    f"{len(record.plan.get('capability_assignments', []))} capability "
                    "assignment(s) selected"
                ),
                plan_id=record.plan_id,
                metadata={
                    "capability_assignments": record.plan.get(
                        "capability_assignments",
                        [],
                    )
                },
            ),
            ExecutionTraceRecord(
                trace_kind=TraceRecordKind.DELIVERY,
                title="Delivery phase plan",
                detail=(
                    f"planned_artifacts={len(record.plan.get('planned_artifacts', []))}; "
                    f"recommended_next_action={record.plan.get('recommended_next_action', '')}"
                ),
                plan_id=record.plan_id,
                metadata={
                    "planned_artifacts": record.plan.get("planned_artifacts", []),
                    "phases": record.plan.get("phases", []),
                },
            ),
        ]
        for trace in traces:
            self._storage.save_trace_record(trace)
        return traces

    def update_plan_status(
        self,
        plan_id: str,
        *,
        status: PlanRecordStatus,
        linked_job_id: str = "",
    ) -> JobPlanRecord | None:
        self.initialize()
        record = self.get_plan(plan_id)
        if record is None:
            return None
        record.status = status
        if linked_job_id:
            record.linked_job_id = linked_job_id
        record.updated_at = datetime.now(UTC).isoformat()
        self._storage.save_plan_record(record)
        return record

    def get_plan(self, plan_id: str) -> JobPlanRecord | None:
        self.initialize()
        data = self._storage.load_plan_record(plan_id)
        if data is None:
            return None
        return JobPlanRecord.from_dict(data)

    def list_plans(
        self,
        *,
        status: str = "",
        limit: int = 50,
    ) -> list[JobPlanRecord]:
        self.initialize()
        return [
            JobPlanRecord.from_dict(item)
            for item in self._storage.list_plan_records(status=status, limit=limit)
        ]

    def record_trace(
        self,
        *,
        trace_kind: TraceRecordKind,
        title: str,
        detail: str,
        plan_id: str = "",
        job_id: str = "",
        workspace_id: str = "",
        bundle_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionTraceRecord:
        self.initialize()
        record = ExecutionTraceRecord(
            trace_kind=trace_kind,
            title=title,
            detail=detail,
            plan_id=plan_id,
            job_id=job_id,
            workspace_id=workspace_id,
            bundle_id=bundle_id,
            metadata=metadata or {},
        )
        self._storage.save_trace_record(record)
        return record

    def list_traces(
        self,
        *,
        trace_kind: str = "",
        plan_id: str = "",
        job_id: str = "",
        workspace_id: str = "",
        bundle_id: str = "",
        limit: int = 200,
    ) -> list[ExecutionTraceRecord]:
        self.initialize()
        return [
            ExecutionTraceRecord.from_dict(item)
            for item in self._storage.list_trace_records(
                trace_kind=trace_kind,
                plan_id=plan_id,
                job_id=job_id,
                workspace_id=workspace_id,
                bundle_id=bundle_id,
                limit=limit,
            )
        ]

    def record_delivery_bundle(
        self,
        *,
        bundle: DeliveryPackage,
        status: DeliveryLifecycleStatus,
        event_type: str,
        detail: str,
        approval_request_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryRecord:
        self.initialize()
        existing = self.get_delivery(bundle.bundle_id)
        now = datetime.now(UTC).isoformat()
        record = existing or DeliveryRecord(
            bundle_id=bundle.bundle_id,
            job_id=bundle.job_id,
            job_kind=bundle.job_kind,
            title=bundle.title,
            requester=bundle.requester,
            workspace_id=bundle.workspace_id,
            artifact_ids=list(bundle.artifact_ids),
            created_at=bundle.created_at or now,
            summary=dict(bundle.summary),
        )
        record.title = bundle.title
        record.requester = bundle.requester
        record.workspace_id = bundle.workspace_id
        record.artifact_ids = list(bundle.artifact_ids)
        record.summary = dict(bundle.summary)
        record.status = status
        record.updated_at = now
        if approval_request_id:
            record.approval_request_id = approval_request_id
        self._append_delivery_event(
            record,
            event_type=event_type,
            detail=detail,
            metadata=metadata or {},
        )
        self._storage.save_delivery_record(record)
        return record

    def refresh_delivery_status(
        self,
        bundle_id: str,
        *,
        approval_lookup: Any = None,
    ) -> DeliveryRecord | None:
        self.initialize()
        record = self.get_delivery(bundle_id)
        if record is None or not record.approval_request_id or approval_lookup is None:
            return record
        request = approval_lookup(record.approval_request_id)
        if not request:
            return record

        mapped_status = record.status
        approval_status = str(request.get("status", ""))
        if approval_status in {"approved", "executed"}:
            mapped_status = DeliveryLifecycleStatus.APPROVED
        elif approval_status in {"denied", "expired"}:
            mapped_status = DeliveryLifecycleStatus.REJECTED
        elif approval_status in {"pending", "partially_approved"}:
            mapped_status = DeliveryLifecycleStatus.AWAITING_APPROVAL

        if mapped_status != record.status:
            self._append_delivery_event(
                record,
                event_type="approval_status",
                detail=f"Approval status is now {approval_status}",
                metadata={"approval_status": approval_status},
            )
            record.status = mapped_status
            record.updated_at = datetime.now(UTC).isoformat()
            self._storage.save_delivery_record(record)
        return record

    def mark_delivery_handed_off(
        self,
        bundle_id: str,
        *,
        detail: str = "",
    ) -> DeliveryRecord | None:
        self.initialize()
        record = self.get_delivery(bundle_id)
        if record is None:
            return None
        record.status = DeliveryLifecycleStatus.HANDED_OFF
        record.updated_at = datetime.now(UTC).isoformat()
        self._append_delivery_event(
            record,
            event_type="handed_off",
            detail=detail or "Delivery package handed off",
            metadata={},
        )
        self._storage.save_delivery_record(record)
        return record

    def get_delivery(self, bundle_id: str) -> DeliveryRecord | None:
        self.initialize()
        data = self._storage.load_delivery_record(bundle_id)
        if data is None:
            return None
        return DeliveryRecord.from_dict(data)

    def list_deliveries(
        self,
        *,
        status: str = "",
        job_id: str = "",
        workspace_id: str = "",
        limit: int = 50,
    ) -> list[DeliveryRecord]:
        self.initialize()
        return [
            DeliveryRecord.from_dict(item)
            for item in self._storage.list_delivery_records(
                status=status,
                job_id=job_id,
                workspace_id=workspace_id,
                limit=limit,
            )
        ]

    def get_stats(self) -> dict[str, Any]:
        self.initialize()
        return self._storage.get_stats()

    def _append_delivery_event(
        self,
        record: DeliveryRecord,
        *,
        event_type: str,
        detail: str,
        metadata: dict[str, Any],
    ) -> None:
        candidate = DeliveryEvent(
            event_type=event_type,
            status=record.status.value,
            detail=detail,
            metadata=metadata,
        )
        last = record.events[-1] if record.events else None
        if (
            last is not None
            and last.event_type == candidate.event_type
            and last.status == candidate.status
            and last.detail == candidate.detail
        ):
            return
        record.events.append(candidate)
