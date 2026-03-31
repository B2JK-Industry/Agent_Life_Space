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
    TelemetrySnapshot,
    TraceRecordKind,
    UsageSummary,
)
from agent.control.policy import (
    classify_provider_delivery_outcome,
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
        self._update_delivery_summary_from_event(
            record,
            event_type=event_type,
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
        if record.status == DeliveryLifecycleStatus.HANDED_OFF:
            return record
        request = approval_lookup(record.approval_request_id)
        if not request:
            return record

        mapped_status = record.status
        approval_status = str(request.get("status", ""))
        if approval_status == "executed":
            mapped_status = DeliveryLifecycleStatus.HANDED_OFF
        elif approval_status == "approved":
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

    def record_delivery_event(
        self,
        bundle_id: str,
        *,
        event_type: str,
        detail: str,
        metadata: dict[str, Any] | None = None,
        status: DeliveryLifecycleStatus | str | None = None,
    ) -> DeliveryRecord | None:
        """Append an auditable delivery event without rebuilding the full bundle."""
        self.initialize()
        record = self.get_delivery(bundle_id)
        if record is None:
            return None
        if status is not None:
            record.status = (
                status
                if isinstance(status, DeliveryLifecycleStatus)
                else DeliveryLifecycleStatus(str(status))
            )
        record.updated_at = datetime.now(UTC).isoformat()
        self._append_delivery_event(
            record,
            event_type=event_type,
            detail=detail,
            metadata=metadata or {},
        )
        self._update_delivery_summary_from_event(
            record,
            event_type=event_type,
            metadata=metadata or {},
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
        duration_ms: float | None = None,
        retry_count: int = 0,
        failure_count: int = 0,
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
            duration_ms=duration_ms,
            retry_count=retry_count,
            failure_count=failure_count,
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

    def prune_retained_artifacts(
        self,
        *,
        job_id: str = "",
        artifact_kind: str = "",
        retention_policy_id: str = "",
        limit: int = 100,
    ) -> list[ArtifactRetentionRecord]:
        """Prune expired retained artifacts and clear recoverable snapshots."""
        self.initialize()
        candidates = self.list_retained_artifacts(
            job_id=job_id,
            artifact_kind=artifact_kind,
            retention_policy_id=retention_policy_id,
            limit=limit,
        )
        pruned: list[ArtifactRetentionRecord] = []
        now = datetime.now(UTC).isoformat()
        for record in candidates:
            if record.status != ArtifactRetentionStatus.EXPIRED:
                continue
            record.status = ArtifactRetentionStatus.PRUNED
            record.updated_at = now
            record.recoverable = False
            record.content = ""
            record.content_json = {}
            record.metadata = {
                **record.metadata,
                "pruned_at": now,
                "prune_reason": "retention_expired",
            }
            self._storage.save_artifact_retention_record(record)
            pruned.append(record)
        return pruned

    def get_retention_posture(self, *, limit: int = 5000) -> dict[str, Any]:
        """Summarize retained-artifact lifecycle posture for operator reporting."""
        records = self.list_retained_artifacts(limit=limit)
        by_status = {
            ArtifactRetentionStatus.ACTIVE.value: 0,
            ArtifactRetentionStatus.EXPIRED.value: 0,
            ArtifactRetentionStatus.PRUNED.value: 0,
        }
        by_policy: dict[str, int] = {}
        recoverable = 0
        for record in records:
            status = record.status.value
            by_status[status] = by_status.get(status, 0) + 1
            by_policy[record.retention_policy_id] = (
                by_policy.get(record.retention_policy_id, 0) + 1
            )
            if record.recoverable:
                recoverable += 1
        return {
            "total": len(records),
            "by_status": by_status,
            "by_policy": by_policy,
            "recoverable_records": recoverable,
        }

    def record_cost_entry(
        self,
        *,
        job_id: str,
        job_kind: JobKind,
        title: str = "",
        workspace_id: str = "",
        usage: UsageSummary | dict[str, Any] | None = None,
        entry_id: str = "",
        source_type: str = "job_usage_snapshot",
        metadata: dict[str, Any] | None = None,
    ) -> CostLedgerEntry:
        self.initialize()
        entry = CostLedgerEntry(
            entry_id=entry_id or f"usage-{job_id}",
            job_id=job_id,
            job_kind=job_kind,
            title=title,
            workspace_id=workspace_id,
            recorded_at=datetime.now(UTC).isoformat(),
            usage=self._coerce_usage(usage),
            source_type=source_type,
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

    def get_cost_accuracy(self, *, limit: int = 50) -> dict[str, Any]:
        """Compare estimated vs actual costs across recent jobs."""
        self.initialize()
        plans = self._storage.list_plan_records(status="", limit=limit)
        cost_entries = self.list_cost_entries(limit=500)

        # Index cost entries by job_id
        cost_by_job: dict[str, float] = {}
        for entry in cost_entries:
            cost_by_job[entry.job_id] = cost_by_job.get(entry.job_id, 0.0) + entry.usage.total_cost_usd

        comparisons: list[dict[str, Any]] = []
        for plan_data in plans:
            plan = JobPlanRecord.from_dict(plan_data) if isinstance(plan_data, dict) else plan_data
            job_id = plan.linked_job_id
            if not job_id or job_id not in cost_by_job:
                continue
            estimated = plan.plan.get("budget", {}).get("estimated_cost_usd", 0.0) if isinstance(plan.plan, dict) else 0.0
            if estimated <= 0:
                continue
            actual = cost_by_job[job_id]
            ratio = actual / estimated if estimated > 0 else 0.0
            comparisons.append({
                "plan_id": plan.plan_id,
                "job_id": job_id,
                "estimated_usd": round(estimated, 4),
                "actual_usd": round(actual, 4),
                "ratio": round(ratio, 3),
                "delta_usd": round(actual - estimated, 4),
            })

        if not comparisons:
            return {
                "comparisons": [],
                "total_estimated_usd": 0.0,
                "total_actual_usd": 0.0,
                "avg_ratio": 0.0,
                "accuracy_pct": 0.0,
                "sample_size": 0,
            }

        total_estimated = sum(c["estimated_usd"] for c in comparisons)
        total_actual = sum(c["actual_usd"] for c in comparisons)
        avg_ratio = total_actual / total_estimated if total_estimated > 0 else 0.0
        ratios = sorted(c["ratio"] for c in comparisons)
        median_ratio = ratios[len(ratios) // 2] if ratios else 0.0
        # Accuracy: 100% means perfect estimate; <100% means under-estimate, >100% over-estimate
        accuracy_pct = round((1.0 - abs(1.0 - avg_ratio)) * 100, 1) if avg_ratio > 0 else 0.0

        return {
            "comparisons": comparisons,
            "total_estimated_usd": round(total_estimated, 4),
            "total_actual_usd": round(total_actual, 4),
            "avg_ratio": round(avg_ratio, 3),
            "median_ratio": round(median_ratio, 3),
            "accuracy_pct": accuracy_pct,
            "sample_size": len(comparisons),
        }

    # ── Margin tracking ────────────────────────

    def record_job_revenue(
        self,
        *,
        job_id: str,
        revenue_usd: float,
        source: str = "",
    ) -> ProductJobRecord | None:
        """Record revenue for a job and recalculate margin."""
        self.initialize()
        job = self.get_product_job(job_id)
        if job is None:
            return None
        job.revenue_usd = revenue_usd
        job.revenue_source = source
        job.margin_usd = revenue_usd - job.usage.total_cost_usd
        job.updated_at = datetime.now(UTC).isoformat()
        self._storage.save_product_job_record(job)
        return job

    def get_margin_summary(self, *, limit: int = 100) -> dict[str, Any]:
        """Aggregate margin data across recent product jobs."""
        self.initialize()
        jobs = self.list_product_jobs(limit=limit)
        if not jobs:
            return {
                "total_jobs": 0,
                "total_revenue_usd": 0.0,
                "total_cost_usd": 0.0,
                "total_margin_usd": 0.0,
                "avg_margin_pct": 0.0,
                "profitable_jobs": 0,
            }

        total_revenue = sum(j.revenue_usd for j in jobs)
        total_cost = sum(j.usage.total_cost_usd for j in jobs)
        total_margin = total_revenue - total_cost
        profitable = sum(1 for j in jobs if j.revenue_usd > j.usage.total_cost_usd)
        avg_margin_pct = (
            (total_margin / total_revenue * 100) if total_revenue > 0 else 0.0
        )

        return {
            "total_jobs": len(jobs),
            "total_revenue_usd": round(total_revenue, 4),
            "total_cost_usd": round(total_cost, 4),
            "total_margin_usd": round(total_margin, 4),
            "avg_margin_pct": round(avg_margin_pct, 1),
            "profitable_jobs": profitable,
            "jobs_with_revenue": sum(1 for j in jobs if j.revenue_usd > 0),
        }

    # ── Telemetry ──────────────────────────────

    def record_telemetry_snapshot(
        self,
        *,
        status_provider: Any = None,
        worker_stats: dict[str, Any] | None = None,
    ) -> TelemetrySnapshot:
        """Capture a point-in-time telemetry snapshot from current runtime state.

        Builds snapshot from persisted product jobs, cost entries, deliveries,
        plus optional live data from status_provider and worker_stats.
        Records as a TELEMETRY trace for time-series querying.
        """
        self.initialize()

        # Job metrics from persisted product jobs
        product_jobs = self.list_product_jobs(limit=500)
        completed = [j for j in product_jobs if j.status == "completed"]
        failed = [j for j in product_jobs if j.failure_count > 0]
        retried = [j for j in product_jobs if j.retry_count > 0]
        durations = [j.duration_ms for j in completed if j.duration_ms and j.duration_ms > 0]
        avg_duration = sum(durations) / len(durations) if durations else 0.0
        max_duration = max(durations) if durations else 0.0
        p95_duration = 0.0
        if durations:
            sorted_d = sorted(durations)
            p95_idx = min(int(len(sorted_d) * 0.95), len(sorted_d) - 1)
            p95_duration = sorted_d[p95_idx]

        # Cost from ledger
        cost_entries = self.list_cost_entries(limit=500)
        total_cost = sum(e.usage.total_cost_usd for e in cost_entries)
        avg_cost = total_cost / len(completed) if completed else 0.0

        # Delivery metrics
        deliveries = self.list_deliveries(limit=500)
        delivery_outcomes: dict[str, int] = {}
        for d in deliveries:
            provider = d.summary.get("provider_delivery", {})
            outcome = provider.get("outcome", "") if provider else ""
            if outcome:
                delivery_outcomes[outcome] = delivery_outcomes.get(outcome, 0) + 1

        # Live data from worker/system
        ws = worker_stats or {}
        queue_depth = int(ws.get("queue_size", 0))
        active_jobs = int(ws.get("active_jobs", 0))
        circuit_breaker = bool(ws.get("circuit_breaker_open", False))

        # System resources (best-effort)
        memory_pct = 0.0
        cpu_pct = 0.0
        if status_provider and callable(status_provider):
            try:
                agent_status = status_provider()
                health = agent_status.get("health", {})
                memory_pct = float(health.get("memory_percent", 0.0))
                cpu_pct = float(health.get("cpu_percent", 0.0))
            except Exception:
                pass

        snapshot = TelemetrySnapshot(
            jobs_completed=len(completed),
            jobs_failed=len(failed),
            jobs_retried=len(retried),
            jobs_active=active_jobs,
            avg_duration_ms=avg_duration,
            max_duration_ms=max_duration,
            p95_duration_ms=p95_duration,
            total_cost_usd=total_cost,
            avg_cost_per_job_usd=avg_cost,
            queue_depth=queue_depth,
            circuit_breaker_open=circuit_breaker,
            deliveries_total=len(deliveries),
            deliveries_pending=delivery_outcomes.get("pending", 0),
            deliveries_failed=delivery_outcomes.get("failed", 0),
            deliveries_delivered=delivery_outcomes.get("delivered", 0),
            memory_percent=memory_pct,
            cpu_percent=cpu_pct,
        )

        # Persist as trace record for time-series history
        self.record_trace(
            trace_kind=TraceRecordKind.TELEMETRY,
            title="Runtime telemetry snapshot",
            detail=(
                f"jobs={len(completed)}/{len(failed)}f "
                f"avg_dur={avg_duration:.0f}ms "
                f"cost=${total_cost:.4f}"
            ),
            metadata=snapshot.to_dict(),
        )

        return snapshot

    def get_telemetry_summary(
        self,
        *,
        window_hours: int = 24,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Aggregate telemetry snapshots over a time window.

        Returns summary stats: latest snapshot, trend direction,
        aggregated metrics over the window.
        """
        self.initialize()
        traces = self.list_traces(trace_kind="telemetry", limit=limit)
        if not traces:
            return {"snapshots": 0, "latest": None, "window_hours": window_hours}

        cutoff = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
        in_window = [
            t for t in traces
            if t.created_at >= cutoff
        ]

        snapshots = [
            TelemetrySnapshot.from_dict(t.metadata)
            for t in in_window
            if t.metadata
        ]

        if not snapshots:
            latest_trace = traces[0]
            return {
                "snapshots": 0,
                "latest": TelemetrySnapshot.from_dict(latest_trace.metadata).to_dict()
                if latest_trace.metadata else None,
                "window_hours": window_hours,
                "note": "No snapshots in the requested window",
            }

        latest = snapshots[0]  # Traces are returned newest-first

        # Aggregate over window
        all_completed = [s.jobs_completed for s in snapshots]
        all_failed = [s.jobs_failed for s in snapshots]
        all_costs = [s.total_cost_usd for s in snapshots]
        all_durations = [s.avg_duration_ms for s in snapshots if s.avg_duration_ms > 0]

        # Trend detection: compare first half vs second half of snapshots
        trend = "stable"
        if len(snapshots) >= 4:
            mid = len(snapshots) // 2
            recent_half = snapshots[:mid]
            older_half = snapshots[mid:]
            recent_fail_rate = (
                sum(s.jobs_failed for s in recent_half)
                / max(sum(s.jobs_completed for s in recent_half), 1)
            )
            older_fail_rate = (
                sum(s.jobs_failed for s in older_half)
                / max(sum(s.jobs_completed for s in older_half), 1)
            )
            if recent_fail_rate > older_fail_rate * 1.5:
                trend = "degrading"
            elif recent_fail_rate < older_fail_rate * 0.7:
                trend = "improving"

        return {
            "snapshots": len(snapshots),
            "window_hours": window_hours,
            "latest": latest.to_dict(),
            "trend": trend,
            "aggregated": {
                "max_jobs_completed": max(all_completed) if all_completed else 0,
                "max_jobs_failed": max(all_failed) if all_failed else 0,
                "max_cost_usd": round(max(all_costs), 6) if all_costs else 0.0,
                "avg_duration_ms": (
                    round(sum(all_durations) / len(all_durations), 1)
                    if all_durations else 0.0
                ),
                "total_snapshots_with_failures": sum(
                    1 for s in snapshots if s.jobs_failed > 0
                ),
                "circuit_breaker_triggered": sum(
                    1 for s in snapshots if s.circuit_breaker_open
                ),
            },
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

    def _update_delivery_summary_from_event(
        self,
        record: DeliveryRecord,
        *,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        provider_context = dict(metadata.get("provider_context", {}))
        provider_id = (
            str(metadata.get("provider_id", ""))
            or str(provider_context.get("provider_id", ""))
        )
        capability_id = (
            str(metadata.get("capability_id", ""))
            or str(provider_context.get("capability_id", ""))
        )
        route_id = (
            str(metadata.get("route_id", ""))
            or str(provider_context.get("route_id", ""))
        )
        provider_receipt = dict(metadata.get("provider_receipt", {}))
        provider_status = str(
            provider_receipt.get("status")
            or metadata.get("provider_status", "")
        )

        if not (provider_id or capability_id or route_id or provider_receipt or event_type.startswith("gateway_")):
            return

        provider_summary = dict(record.summary.get("provider_delivery", {}))
        provider_summary.update(
            {
                "provider_id": provider_id or provider_summary.get("provider_id", ""),
                "capability_id": capability_id or provider_summary.get("capability_id", ""),
                "route_id": route_id or provider_summary.get("route_id", ""),
                "gateway_run_id": str(
                    metadata.get("gateway_run_id", provider_summary.get("gateway_run_id", ""))
                ),
                "target_url": str(metadata.get("target_url", provider_summary.get("target_url", ""))),
                "receipt": provider_receipt or provider_summary.get("receipt", {}),
                "last_event_type": event_type,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

        if event_type == "gateway_requested":
            provider_summary.setdefault("outcome", "requested")
            provider_summary.setdefault("provider_status", "")
        elif event_type == "gateway_succeeded":
            outcome = classify_provider_delivery_outcome(
                receipt_status=provider_status,
                ok=True,
            )
            provider_summary.update(outcome)
        elif event_type == "gateway_failed":
            outcome = classify_provider_delivery_outcome(ok=False)
            provider_summary.update(outcome)
            provider_summary["error"] = str(metadata.get("error", ""))

        if provider_status:
            provider_summary["provider_status"] = provider_status
        record.summary["provider_delivery"] = provider_summary

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
        try:
            created = datetime.fromisoformat(created_at)
        except (ValueError, TypeError):
            return ""
        return (created + timedelta(days=retain_days)).isoformat()

    def _refresh_retention_record(self, record: ArtifactRetentionRecord) -> bool:
        if record.status == ArtifactRetentionStatus.PRUNED or not record.expires_at:
            return False
        try:
            expires_at = datetime.fromisoformat(record.expires_at)
        except (ValueError, TypeError):
            return False
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
