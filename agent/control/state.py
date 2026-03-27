"""
Agent Life Space — Control-Plane State Service

Shared durable state for planner handoff, planning traces,
and delivery lifecycle records.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agent.control.models import (
    ArtifactKind,
    ArtifactRetentionRecord,
    ArtifactRetentionStatus,
    CostLedgerEntry,
    DeliveryEvent,
    DeliveryLifecycleStatus,
    DeliveryPackage,
    DeliveryRecord,
    ExecutionTraceRecord,
    JobKind,
    JobPlanRecord,
    PlanRecordStatus,
    ProductJobRecord,
    TraceRecordKind,
    UsageSummary,
)
from agent.control.policy import (
    get_artifact_retention_policy,
    get_job_persistence_policy,
    select_artifact_retention_policy,
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

    def record_product_job(
        self,
        *,
        job_id: str,
        job_kind: JobKind,
        title: str,
        status: str,
        subkind: str = "",
        requester: str = "",
        source: str = "",
        execution_mode: str = "",
        workspace_id: str = "",
        scope: str = "",
        outcome: str = "",
        blocked_reason: str = "",
        artifact_ids: list[str] | None = None,
        created_at: str = "",
        completed_at: str = "",
        usage: UsageSummary | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProductJobRecord:
        self.initialize()
        now = datetime.now(UTC).isoformat()
        existing = self.get_product_job(job_id)
        persistence_policy = get_job_persistence_policy(job_kind)
        record = ProductJobRecord(
            job_id=job_id,
            job_kind=job_kind,
            title=title,
            status=status,
            subkind=subkind,
            requester=requester,
            source=source,
            execution_mode=execution_mode,
            workspace_id=workspace_id,
            scope=scope,
            outcome=outcome,
            blocked_reason=blocked_reason,
            artifact_ids=list(artifact_ids or []),
            created_at=existing.created_at if existing is not None else (created_at or now),
            updated_at=now,
            completed_at=completed_at,
            usage=self._coerce_usage(usage),
            metadata={
                **(existing.metadata if existing is not None else {}),
                **(metadata or {}),
                "persistence_policy_id": persistence_policy.id,
                "retention_days": persistence_policy.retain_days,
            },
        )
        self._storage.save_product_job_record(record)
        if persistence_policy.record_cost_ledger:
            self.record_cost_entry(
                job_id=record.job_id,
                job_kind=record.job_kind,
                title=record.title,
                workspace_id=record.workspace_id,
                usage=record.usage,
                metadata={"source_status": record.status},
            )
        return record

    def get_product_job(self, job_id: str) -> ProductJobRecord | None:
        self.initialize()
        data = self._storage.load_product_job_record(job_id)
        if data is None:
            return None
        return ProductJobRecord.from_dict(data)

    def list_product_jobs(
        self,
        *,
        job_kind: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[ProductJobRecord]:
        self.initialize()
        return [
            ProductJobRecord.from_dict(item)
            for item in self._storage.list_product_job_records(
                job_kind=job_kind,
                status=status,
                limit=limit,
            )
        ]

    def record_retained_artifact(
        self,
        *,
        record_id: str,
        job_id: str,
        job_kind: JobKind,
        artifact_kind: ArtifactKind,
        source_type: str,
        title: str = "",
        artifact_format: str = "text",
        artifact_id: str = "",
        bundle_id: str = "",
        retention_policy_id: str = "",
        created_at: str = "",
        content: str = "",
        content_json: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRetentionRecord:
        self.initialize()
        now = datetime.now(UTC)
        existing = self.get_retained_artifact(record_id)
        policy = (
            get_artifact_retention_policy(retention_policy_id)
            if retention_policy_id
            else select_artifact_retention_policy(
                job_kind=job_kind,
                artifact_kind=artifact_kind,
            )
        )
        expires_at = self._expires_at(
            created_at=existing.created_at if existing is not None else (created_at or now.isoformat()),
            retain_days=policy.retain_days,
        )
        record = ArtifactRetentionRecord(
            record_id=record_id,
            artifact_id=artifact_id,
            bundle_id=bundle_id,
            job_id=job_id,
            job_kind=job_kind,
            artifact_kind=artifact_kind,
            source_type=source_type,
            title=title,
            format=artifact_format,
            retention_policy_id=policy.id,
            status=ArtifactRetentionStatus.ACTIVE,
            created_at=existing.created_at if existing is not None else (created_at or now.isoformat()),
            updated_at=now.isoformat(),
            expires_at=expires_at,
            recoverable=policy.recoverable,
            content=content if policy.keep_snapshot else "",
            content_json=dict(content_json or {}) if policy.keep_snapshot else {},
            metadata={**(metadata or {}), "retention_days": policy.retain_days},
        )
        self._refresh_retention_record(record)
        self._storage.save_artifact_retention_record(record)
        return record

    def get_retained_artifact(self, record_id: str) -> ArtifactRetentionRecord | None:
        self.initialize()
        data = self._storage.load_artifact_retention_record(record_id)
        if data is None:
            return None
        record = ArtifactRetentionRecord.from_dict(data)
        if self._refresh_retention_record(record):
            self._storage.save_artifact_retention_record(record)
        return record

    def list_retained_artifacts(
        self,
        *,
        status: str = "",
        job_id: str = "",
        artifact_kind: str = "",
        retention_policy_id: str = "",
        limit: int = 100,
    ) -> list[ArtifactRetentionRecord]:
        self.initialize()
        records = [
            ArtifactRetentionRecord.from_dict(item)
            for item in self._storage.list_artifact_retention_records(
                status="",
                job_id=job_id,
                artifact_kind=artifact_kind,
                retention_policy_id=retention_policy_id,
                limit=limit,
            )
        ]
        refreshed: list[ArtifactRetentionRecord] = []
        for record in records:
            if self._refresh_retention_record(record):
                self._storage.save_artifact_retention_record(record)
            refreshed.append(record)
        if status:
            refreshed = [record for record in refreshed if record.status.value == status]
        return refreshed[:limit]

    def record_cost_entry(
        self,
        *,
        job_id: str,
        job_kind: JobKind,
        title: str = "",
        workspace_id: str = "",
        usage: UsageSummary | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CostLedgerEntry:
        self.initialize()
        entry = CostLedgerEntry(
            entry_id=f"usage-{job_id}",
            job_id=job_id,
            job_kind=job_kind,
            title=title,
            workspace_id=workspace_id,
            recorded_at=datetime.now(UTC).isoformat(),
            usage=self._coerce_usage(usage),
            metadata=dict(metadata or {}),
        )
        self._storage.save_cost_ledger_entry(entry)
        return entry

    def list_cost_entries(
        self,
        *,
        job_id: str = "",
        job_kind: str = "",
        limit: int = 100,
    ) -> list[CostLedgerEntry]:
        self.initialize()
        return [
            CostLedgerEntry.from_dict(item)
            for item in self._storage.list_cost_ledger_entries(
                job_id=job_id,
                job_kind=job_kind,
                limit=limit,
            )
        ]

    def get_stats(self) -> dict[str, Any]:
        self.initialize()
        stats = self._storage.get_stats()
        total_recorded_cost = round(
            sum(entry.usage.total_cost_usd for entry in self.list_cost_entries(limit=500)),
            6,
        )
        return {
            **stats,
            "recorded_cost_usd": total_recorded_cost,
        }

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

    def _coerce_usage(
        self,
        usage: UsageSummary | dict[str, Any] | None,
    ) -> UsageSummary:
        if isinstance(usage, UsageSummary):
            return usage
        if isinstance(usage, dict):
            return UsageSummary.from_dict(usage)
        return UsageSummary()

    def _expires_at(self, *, created_at: str, retain_days: int) -> str:
        created = datetime.fromisoformat(created_at)
        return (created + timedelta(days=retain_days)).isoformat()

    def _refresh_retention_record(self, record: ArtifactRetentionRecord) -> bool:
        if record.status == ArtifactRetentionStatus.PRUNED or not record.expires_at:
            return False
        expires_at = datetime.fromisoformat(record.expires_at)
        if expires_at <= datetime.now(UTC):
            if record.status != ArtifactRetentionStatus.EXPIRED:
                record.status = ArtifactRetentionStatus.EXPIRED
                record.updated_at = datetime.now(UTC).isoformat()
                return True
            return False
        if record.status != ArtifactRetentionStatus.ACTIVE:
            record.status = ArtifactRetentionStatus.ACTIVE
            record.updated_at = datetime.now(UTC).isoformat()
            return True
        return False
