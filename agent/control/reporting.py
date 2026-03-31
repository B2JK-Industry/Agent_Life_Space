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
        gateway_service: Any = None,
    ) -> None:
        self._job_queries = job_queries
        self._artifact_queries = artifact_queries
        self._approval_queue = approval_queue
        self._operator_controls = operator_controls
        self._status_provider = status_provider
        self._control_plane_state = control_plane_state
        self._workspace_queries = workspace_queries
        self._gateway_service = gateway_service

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
        all_approvals = (
            self._approval_queue.list_requests(limit=max(limit, 200))
            if self._approval_queue is not None
            else []
        )
        controls = (
            self._operator_controls.get_status()
            if self._operator_controls is not None
            else {}
        )
        agent_status = self._status_provider() if callable(self._status_provider) else {}
        finance_status = agent_status.get("finance", {})
        finance_budget = finance_status.get("budget", {})
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
        recent_gateway_traces = (
            [
                trace.to_dict()
                for trace in self._control_plane_state.list_traces(
                    trace_kind="gateway",
                    limit=limit,
                )
            ]
            if self._control_plane_state is not None
            else []
        )
        recent_quality_traces = (
            [
                trace.to_dict()
                for trace in self._control_plane_state.list_traces(
                    trace_kind="quality",
                    limit=limit,
                )
            ]
            if self._control_plane_state is not None
            else []
        )
        recent_release_traces = (
            [
                trace.to_dict()
                for trace in self._control_plane_state.list_traces(
                    trace_kind="release",
                    limit=limit,
                )
            ]
            if self._control_plane_state is not None
            else []
        )
        recent_deliveries = (
            [delivery.to_dict() for delivery in self._control_plane_state.list_deliveries(limit=limit)]
            if self._control_plane_state is not None
            else []
        )
        recent_persisted_jobs = (
            [record.to_dict() for record in self._control_plane_state.list_product_jobs(limit=limit)]
            if self._control_plane_state is not None
            else []
        )
        recent_retained_artifacts = (
            [record.to_dict() for record in self._control_plane_state.list_retained_artifacts(limit=limit)]
            if self._control_plane_state is not None
            else []
        )
        recent_cost_entries = (
            [entry.to_dict() for entry in self._control_plane_state.list_cost_entries(limit=limit)]
            if self._control_plane_state is not None
            else []
        )
        recent_workspace_records = (
            [record.to_dict() for record in self._workspace_queries.list_workspaces(limit=limit)]
            if self._workspace_queries is not None
            else []
        )
        gateway_catalog = (
            self._gateway_service.describe_capability_catalog()
            if self._gateway_service is not None
            else {}
        )
        gateway_summary = gateway_catalog.get("summary", {})
        provider_delivery_views = [
            item
            for item in (
                self._provider_delivery_view(delivery)
                for delivery in recent_deliveries
            )
            if item is not None
        ]
        provider_delivery_summary = self._provider_delivery_summary(provider_delivery_views)
        retention_posture = (
            self._control_plane_state.get_retention_posture(limit=max(limit, 500))
            if self._control_plane_state is not None
            else {}
        )
        control_stats = (
            self._control_plane_state.get_stats()
            if self._control_plane_state is not None
            else {}
        )

        inbox: list[dict[str, Any]] = []
        for approval in pending_approvals[:limit]:
            inbox.append(
                {
                    "kind": "approval",
                    "id": approval["id"],
                    "status": approval["status"],
                    "title": approval["description"],
                    "detail": self._approval_detail(approval),
                }
            )
        for job in blocked_jobs[:limit]:
            inbox.append(
                {
                    "kind": "job_attention",
                    "id": job["job_id"],
                    "status": job["status"],
                    "title": job["title"],
                    "detail": self._job_attention_detail(job),
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
        if finance_budget.get("hard_cap_hit") or finance_budget.get("stop_loss_hit"):
            inbox.append(
                {
                    "kind": "budget_attention",
                    "id": "finance_budget",
                    "status": "blocked",
                    "title": "Budget policy is blocking new execution",
                    "detail": "; ".join(finance_budget.get("warnings", [])),
                }
            )
        elif finance_budget.get("soft_cap_hit"):
            inbox.append(
                {
                    "kind": "budget_attention",
                    "id": "finance_budget",
                    "status": "warning",
                    "title": "Budget soft cap exceeded",
                    "detail": "; ".join(finance_budget.get("warnings", [])),
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
        for retention in recent_retained_artifacts[:limit]:
            if retention["status"] == "expired":
                inbox.append(
                    {
                        "kind": "retention_attention",
                        "id": retention["record_id"],
                        "status": retention["status"],
                        "title": retention["title"] or retention["artifact_kind"],
                        "detail": (
                            f"{retention['artifact_kind']} exceeded retention policy "
                            f"{retention['retention_policy_id']}"
                        ),
                    }
                )
        for job in recent_persisted_jobs[:limit]:
            if job.get("failure_count", 0) > 0:
                inbox.append(
                    {
                        "kind": "product_job_attention",
                        "id": job["job_id"],
                        "status": job["status"],
                        "title": job["title"],
                        "detail": self._job_attention_detail(job),
                    }
                )
        latest_review_quality = (
            recent_quality_traces[0]["metadata"]
            if recent_quality_traces
            else {}
        )
        latest_release_readiness = (
            recent_release_traces[0]["metadata"]
            if recent_release_traces
            else {}
        )
        review_quality_trend = dict(latest_review_quality.get("trend", {}))
        if review_quality_trend.get("regression_detected"):
            inbox.append(
                {
                    "kind": "quality_attention",
                    "id": "review_quality",
                    "status": "warning",
                    "title": "Review quality regression detected",
                    "detail": review_quality_trend.get(
                        "summary",
                        "Golden review quality regressed versus the previous release.",
                    ),
                }
            )
        if latest_release_readiness and not latest_release_readiness.get("ready", True):
            inbox.append(
                {
                    "kind": "release_attention",
                    "id": "release_readiness",
                    "status": "blocked",
                    "title": "Release readiness gate is failing",
                    "detail": "; ".join(
                        latest_release_readiness.get("blocking_reasons", [])
                    )
                    or latest_release_readiness.get(
                        "summary",
                        "Release readiness checks failed.",
                    ),
                }
            )
        if gateway_summary.get("total_routes", 0) > 0 and gateway_summary.get(
            "configured_routes",
            0,
        ) == 0:
            inbox.append(
                {
                    "kind": "gateway_attention",
                    "id": "gateway_catalog",
                    "status": "warning",
                    "title": "External gateway routes are not configured",
                    "detail": (
                        f"{gateway_summary.get('total_routes', 0)} provider route(s) "
                        "exist but none are ready for live send."
                    ),
                }
            )
        for provider_delivery in provider_delivery_views[:limit]:
            if provider_delivery["attention_required"]:
                inbox.append(
                    {
                        "kind": "provider_delivery_attention",
                        "id": provider_delivery["bundle_id"],
                        "status": provider_delivery["outcome"],
                        "title": provider_delivery["title"],
                        "detail": provider_delivery["detail"],
                    }
                )

        approval_backlog = self._approval_backlog(all_approvals)

        completed_jobs = [j for j in jobs if j["status"] == "completed"]
        failed_jobs_list = [j for j in jobs if j["status"] == "failed"]

        return {
            "summary": {
                "total_jobs": len(jobs),
                "completed_jobs": len(completed_jobs),
                "failed_jobs": len(failed_jobs_list),
                "total_artifacts": len(artifacts),
                "blocked_jobs": len(blocked_jobs),
                "pending_approvals": len(pending_approvals),
                "approval_requests_total": approval_backlog["total"],
                "partial_approvals": approval_backlog["by_status"].get(
                    "partially_approved",
                    0,
                ),
                "blocked_approval_requests": (
                    approval_backlog["by_status"].get("denied", 0)
                    + approval_backlog["by_status"].get("expired", 0)
                ),
                "persisted_plans": control_stats.get("plans", len(recent_plans)),
                "recent_traces": control_stats.get("traces", len(recent_traces)),
                "recent_gateway_traces": len(recent_gateway_traces),
                "release_ready": latest_release_readiness.get("ready", True),
                "delivery_records": control_stats.get("deliveries", len(recent_deliveries)),
                "persisted_product_jobs": control_stats.get("product_jobs", len(recent_persisted_jobs)),
                "retained_artifacts": control_stats.get("retained_artifacts", len(recent_retained_artifacts)),
                "expired_retained_artifacts": retention_posture.get("by_status", {}).get("expired", 0),
                "pruned_retained_artifacts": retention_posture.get("by_status", {}).get("pruned", 0),
                "cost_ledger_entries": control_stats.get("cost_entries", len(recent_cost_entries)),
                "recorded_cost_usd": control_stats.get("recorded_cost_usd", 0.0),
                "gateway_routes_total": gateway_summary.get("total_routes", 0),
                "gateway_routes_configured": gateway_summary.get("configured_routes", 0),
                "provider_delivery_pending": provider_delivery_summary["by_outcome"].get(
                    "pending",
                    0,
                ),
                "provider_delivery_failed": provider_delivery_summary["by_outcome"].get(
                    "failed",
                    0,
                ),
                "disabled_tools": controls.get("total_disabled", 0),
                "active_workspaces": workspace_health.get("by_status", {}).get("active", 0),
                "active_workers": worker_execution.get("active_jobs", 0),
                "daily_budget_remaining_usd": finance_budget.get("daily_remaining", 0.0),
                "monthly_budget_remaining_usd": finance_budget.get("monthly_remaining", 0.0),
                "budget_within_limit": finance_budget.get("within_budget", True),
                "failed_product_jobs": sum(
                    1 for record in recent_persisted_jobs
                    if record.get("failure_count", 0) > 0
                ),
                "retried_product_jobs": sum(
                    1 for record in recent_persisted_jobs
                    if record.get("retry_count", 0) > 0
                ),
                "quality_regression_detected": review_quality_trend.get(
                    "regression_detected",
                    False,
                ),
                "max_product_job_duration_ms": max(
                    (record.get("duration_ms") or 0.0) for record in recent_persisted_jobs
                ) if recent_persisted_jobs else 0.0,
            },
            "budget_posture": {
                "daily_spent": finance_budget.get("daily_spent", 0.0),
                "daily_remaining": finance_budget.get("daily_remaining", 0.0),
                "daily_budget": finance_budget.get("daily_budget", 0.0),
                "monthly_spent": finance_budget.get("monthly_spent", 0.0),
                "monthly_remaining": finance_budget.get("monthly_remaining", 0.0),
                "monthly_budget": finance_budget.get("monthly_budget", 0.0),
                "within_budget": finance_budget.get("within_budget", True),
                "hard_cap_hit": finance_budget.get("hard_cap_hit", False),
                "soft_cap_hit": finance_budget.get("soft_cap_hit", False),
                "stop_loss_hit": finance_budget.get("stop_loss_hit", False),
                "warnings": list(finance_budget.get("warnings", [])),
                "single_tx_approval_cap": (
                    finance_budget.get("forecast", {}).get("single_tx_approval_cap", 0.0)
                ),
            },
            "approval_backlog": approval_backlog,
            "retention_posture": retention_posture,
            "inbox": inbox[:limit],
            "recent_jobs": jobs[:limit],
            "recent_artifacts": artifacts[:limit],
            "recent_plans": recent_plans[:limit],
            "recent_traces": recent_traces[:limit],
            "recent_gateway_traces": recent_gateway_traces[:limit],
            "latest_review_quality": latest_review_quality,
            "review_quality_trend": review_quality_trend,
            "latest_release_readiness": latest_release_readiness,
            "gateway_catalog": gateway_catalog,
            "provider_delivery_summary": provider_delivery_summary,
            "recent_provider_deliveries": provider_delivery_views[:limit],
            "recent_deliveries": recent_deliveries[:limit],
            "recent_persisted_jobs": recent_persisted_jobs[:limit],
            "recent_retained_artifacts": recent_retained_artifacts[:limit],
            "cost_accuracy": self._control_plane_state.get_cost_accuracy(limit=limit) if self._control_plane_state else {},
            "recent_cost_entries": recent_cost_entries[:limit],
            "recent_workspace_records": recent_workspace_records[:limit],
            "pending_approvals": pending_approvals[:limit],
            "controls": controls,
            "workspace_health": workspace_health,
            "worker_execution": worker_execution,
            "agent_status": agent_status,
        }

    def _approval_detail(self, approval: dict[str, Any]) -> str:
        status = approval.get("status", "")
        if status == "partially_approved":
            required = int(approval.get("required_approvals", 1) or 1)
            received = len(approval.get("approvals_received", []))
            return (
                f"{received}/{required} approvals received; "
                "awaiting additional approval"
            )
        if status == "denied":
            return approval.get("denial_reason", "") or approval.get("reason", "")
        return approval.get("reason", "")

    def _approval_backlog(self, approvals: list[dict[str, Any]]) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        by_category: dict[str, int] = {}
        blocked_reasons: list[str] = []
        for approval in approvals:
            status = str(approval.get("status", ""))
            category = str(approval.get("category", ""))
            by_status[status] = by_status.get(status, 0) + 1
            by_category[category] = by_category.get(category, 0) + 1
            detail = self._approval_detail(approval)
            if status in {"partially_approved", "denied", "expired"} and detail:
                blocked_reasons.append(detail)
        return {
            "total": len(approvals),
            "by_status": by_status,
            "by_category": by_category,
            "blocked_reasons": blocked_reasons,
        }

    def _job_attention_detail(self, job: dict[str, Any]) -> str:
        denial = job.get("metadata", {}).get("denial", {})
        if denial.get("summary"):
            detail = denial.get("detail", "")
            if detail:
                return f"{denial['summary']}: {detail}"
            return denial["summary"]
        return (
            job.get("metadata", {}).get("last_error", "")
            or job.get("blocked_reason", "")
            or job.get("outcome", "")
        )

    def _provider_delivery_view(self, delivery: dict[str, Any]) -> dict[str, Any] | None:
        provider = dict(delivery.get("summary", {}).get("provider_delivery", {}))
        if not provider:
            return None
        provider_id = provider.get("provider_id", "")
        route_id = provider.get("route_id", "")
        provider_status = provider.get("provider_status", "")
        outcome = provider.get("outcome", "unknown")
        detail_parts = []
        if provider_id:
            detail_parts.append(f"provider={provider_id}")
        if route_id:
            detail_parts.append(f"route={route_id}")
        if provider_status:
            detail_parts.append(f"provider_status={provider_status}")
        if provider.get("target_url"):
            detail_parts.append(f"target={provider['target_url']}")
        if provider.get("error"):
            detail_parts.append(provider["error"])
        return {
            "bundle_id": delivery.get("bundle_id", ""),
            "job_id": delivery.get("job_id", ""),
            "title": delivery.get("title", ""),
            "status": delivery.get("status", ""),
            "provider_id": provider_id,
            "capability_id": provider.get("capability_id", ""),
            "route_id": route_id,
            "provider_status": provider_status,
            "outcome": outcome,
            "attention_required": bool(provider.get("attention_required", False)),
            "receipt": dict(provider.get("receipt", {})),
            "detail": "; ".join(detail_parts),
        }

    def _provider_delivery_summary(
        self,
        provider_deliveries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        by_outcome: dict[str, int] = {}
        by_provider: dict[str, int] = {}
        for item in provider_deliveries:
            outcome = str(item.get("outcome", "unknown"))
            provider_id = str(item.get("provider_id", ""))
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            if provider_id:
                by_provider[provider_id] = by_provider.get(provider_id, 0) + 1
        return {
            "total": len(provider_deliveries),
            "by_outcome": by_outcome,
            "by_provider": by_provider,
        }
