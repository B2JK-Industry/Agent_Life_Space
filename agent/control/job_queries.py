"""
Agent Life Space — Cross-System Job Queries

Shared inspection layer for build/review job state.
Normalizes bounded-context storage into one control-plane surface.
"""

from __future__ import annotations

from typing import Any

from agent.build.models import BuildJob
from agent.control.models import JobKind, JobQueryDetail, JobQuerySummary
from agent.review.models import ReviewJob


class JobQueryService:
    """Query build and review jobs through one shared control-plane API."""

    def __init__(
        self,
        build_service: Any = None,
        review_service: Any = None,
        task_manager: Any = None,
        job_runner: Any = None,
        agent_loop_provider: Any = None,
    ) -> None:
        self._build_service = build_service
        self._review_service = review_service
        self._task_manager = task_manager
        self._job_runner = job_runner
        self._agent_loop_provider = agent_loop_provider

    def list_jobs(
        self,
        kind: JobKind | str | None = None,
        status: str = "",
        limit: int = 20,
    ) -> list[JobQuerySummary]:
        """List jobs across supported bounded contexts."""
        normalized_kind = self._normalize_kind(kind)
        records: list[JobQuerySummary] = []

        if normalized_kind in (None, JobKind.BUILD) and self._build_service is not None:
            build_jobs = self._build_service.list_jobs(status=status, limit=limit)
            records.extend(
                self._build_summary(BuildJob.from_dict(job))
                for job in build_jobs
            )

        if normalized_kind in (None, JobKind.REVIEW) and self._review_service is not None:
            review_jobs = self._review_service.list_jobs(status=status, limit=limit)
            records.extend(
                self._review_summary(ReviewJob.from_dict(job))
                for job in review_jobs
            )

        if normalized_kind in (None, JobKind.OPERATE):
            records.extend(self._operate_summaries(limit=limit, status=status))

        records.sort(key=lambda job: job.created_at, reverse=True)
        return records[:limit]

    def get_job(
        self,
        job_id: str,
        kind: JobKind | str | None = None,
    ) -> JobQueryDetail | None:
        """Load one job through the shared control-plane surface."""
        normalized_kind = self._normalize_kind(kind)

        if normalized_kind in (None, JobKind.BUILD) and self._build_service is not None:
            build_job = self._build_service.load_job(job_id)
            if build_job is not None:
                return self._build_detail(build_job)

        if normalized_kind in (None, JobKind.REVIEW) and self._review_service is not None:
            review_job = self._review_service.load_job(job_id)
            if review_job is not None:
                return self._review_detail(review_job)

        if normalized_kind in (None, JobKind.OPERATE):
            operate_job = self._get_operate_job(job_id)
            if operate_job is not None:
                return operate_job

        return None

    def _normalize_kind(self, kind: JobKind | str | None) -> JobKind | None:
        if kind in (None, "", "all"):
            return None
        if isinstance(kind, JobKind):
            return kind
        return JobKind(str(kind))

    def _build_summary(self, job: BuildJob) -> JobQuerySummary:
        blocked_reason = job.error if job.status.value == "blocked" else ""
        outcome = "accepted" if job.acceptance.accepted else "rejected"
        if job.post_build_review_verdict:
            outcome = (
                f"{outcome}; review={job.post_build_review_verdict}"
            )
        return JobQuerySummary(
            job_id=job.id,
            job_kind=JobKind.BUILD,
            status=job.status.value,
            title=job.intake.description or job.build_type.value,
            subkind="build_job",
            requester=job.requester,
            execution_mode=job.execution_mode.value,
            created_at=job.timing.created_at,
            completed_at=job.timing.completed_at,
            artifact_count=len(job.artifacts),
            scope=", ".join(job.intake.target_files[:3]),
            outcome=outcome,
            blocked_reason=blocked_reason,
            error=job.error,
        )

    def _build_detail(self, job: BuildJob) -> JobQueryDetail:
        summary = self._build_summary(job)
        return JobQueryDetail(
            **summary.__dict__,
            metadata={
                "build_type": job.build_type.value,
                "capability_id": job.capability_id,
                "workspace_id": job.workspace_id,
                "resume": {
                    "resumed_from_job_id": job.resumed_from_job_id,
                    "resume_count": job.resume_count,
                },
                "checkpoints": [checkpoint.to_dict() for checkpoint in job.checkpoints],
                "verification_passed": job.verification_passed,
                "verification_results": [v.to_dict() for v in job.verification_results],
                "acceptance": job.acceptance.to_dict(),
                "error": job.error,
                "codegen_fallback": bool(job.metadata.get("codegen_fallback")),
                "codegen_error": str(job.metadata.get("codegen_error", "")),
                "post_build_review": {
                    "requested": job.intake.run_post_build_review,
                    "job_id": job.post_build_review_job_id,
                    "verdict": job.post_build_review_verdict,
                    "finding_counts": job.post_build_review_findings,
                },
            },
        )

    def _review_summary(self, job: ReviewJob) -> JobQuerySummary:
        return JobQuerySummary(
            job_id=job.id,
            job_kind=JobKind.REVIEW,
            status=job.status.value,
            title=job.intake.context or job.job_type.value,
            subkind="review_job",
            requester=job.requester,
            execution_mode=job.execution_mode.value,
            created_at=job.created_at,
            completed_at=job.completed_at,
            artifact_count=len(job.artifacts),
            scope=job.intake.diff_spec or job.intake.repo_path,
            outcome=job.report.verdict,
            blocked_reason=job.error,
            error=job.error,
        )

    def _review_detail(self, job: ReviewJob) -> JobQueryDetail:
        summary = self._review_summary(job)
        return JobQueryDetail(
            **summary.__dict__,
            metadata={
                "review_type": job.job_type.value,
                "phase": job.phase.value,
                "workspace_id": job.workspace_id,
                "timing": job.timing.to_dict(),
                "usage": job.usage.to_dict(),
                "finding_counts": job.report.finding_counts,
                "verdict_confidence": job.report.verdict_confidence.value,
                "focus_areas": job.intake.focus_areas,
                "include_patterns": job.intake.include_patterns,
                "exclude_patterns": job.intake.exclude_patterns,
                "error": job.error,
            },
        )

    def _operate_summaries(
        self,
        *,
        limit: int,
        status: str = "",
    ) -> list[JobQuerySummary]:
        records: list[JobQuerySummary] = []

        if self._task_manager is not None:
            for task in self._task_manager.list_tasks(limit=limit):
                if status and task.status.value != status:
                    continue
                records.append(
                    JobQuerySummary(
                        job_id=task.id,
                        job_kind=JobKind.OPERATE,
                        status=task.status.value,
                        title=task.name,
                        subkind="task",
                        created_at=task.created_at,
                        completed_at=task.completed_at or "",
                        scope=", ".join(task.tags[:3]),
                        outcome="approved" if task.requires_approval else "",
                        blocked_reason=task.error or "",
                    )
                )

        if self._job_runner is not None:
            for record in self._job_runner.get_recent_jobs(limit=limit):
                if status and record.status.value != status:
                    continue
                records.append(
                    JobQuerySummary(
                        job_id=record.id,
                        job_kind=JobKind.OPERATE,
                        status=record.status.value,
                        title=record.name,
                        subkind="job_runner",
                        created_at=record.created_at,
                        completed_at=record.completed_at or "",
                        outcome="retrying" if record.retry_count > 0 else "",
                        blocked_reason=record.error or "",
                    )
                )

        agent_loop = self._resolve_agent_loop()
        if agent_loop is not None:
            loop_status = agent_loop.get_status()
            loop_state = "running" if loop_status["running"] else "idle"
            if not status or loop_state == status:
                records.append(
                    JobQuerySummary(
                        job_id="agent_loop",
                        job_kind=JobKind.OPERATE,
                        status=loop_state,
                        title="Agent loop",
                        subkind="agent_loop",
                        created_at="",
                        completed_at="",
                        outcome=f"queue={loop_status['queue_size']}",
                    )
                )

        return records

    def _get_operate_job(self, job_id: str) -> JobQueryDetail | None:
        if self._task_manager is not None:
            task = self._task_manager.get_task(job_id)
            if task is not None:
                return JobQueryDetail(
                    job_id=task.id,
                    job_kind=JobKind.OPERATE,
                    status=task.status.value,
                    title=task.name,
                    subkind="task",
                    created_at=task.created_at,
                    completed_at=task.completed_at or "",
                    scope=", ".join(task.tags[:3]),
                    blocked_reason=task.error or "",
                    metadata=task.to_dict(),
                )

        if self._job_runner is not None:
            record = self._job_runner.get_job_status(job_id)
            if record is not None:
                return JobQueryDetail(
                    job_id=record.id,
                    job_kind=JobKind.OPERATE,
                    status=record.status.value,
                    title=record.name,
                    subkind="job_runner",
                    created_at=record.created_at,
                    completed_at=record.completed_at or "",
                    blocked_reason=record.error or "",
                    metadata=record.to_dict(),
                )

        agent_loop = self._resolve_agent_loop()
        if job_id == "agent_loop" and agent_loop is not None:
            status = agent_loop.get_status()
            return JobQueryDetail(
                job_id="agent_loop",
                job_kind=JobKind.OPERATE,
                status="running" if status["running"] else "idle",
                title="Agent loop",
                subkind="agent_loop",
                created_at="",
                completed_at="",
                metadata={
                    "status": status,
                    "queue_snapshot": agent_loop.get_queue_snapshot(),
                },
            )

        return None

    def _resolve_agent_loop(self) -> Any | None:
        if callable(self._agent_loop_provider):
            return self._agent_loop_provider()
        return self._agent_loop_provider
