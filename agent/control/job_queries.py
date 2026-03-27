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

    def __init__(self, build_service: Any = None, review_service: Any = None) -> None:
        self._build_service = build_service
        self._review_service = review_service

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
            requester=job.requester,
            execution_mode=job.execution_mode.value,
            created_at=job.timing.created_at,
            completed_at=job.timing.completed_at,
            artifact_count=len(job.artifacts),
            scope=", ".join(job.intake.target_files[:3]),
            outcome=outcome,
            blocked_reason=blocked_reason,
        )

    def _build_detail(self, job: BuildJob) -> JobQueryDetail:
        summary = self._build_summary(job)
        return JobQueryDetail(
            **summary.__dict__,
            metadata={
                "build_type": job.build_type.value,
                "workspace_id": job.workspace_id,
                "verification_passed": job.verification_passed,
                "verification_results": [v.to_dict() for v in job.verification_results],
                "acceptance": job.acceptance.to_dict(),
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
            requester=job.requester,
            execution_mode=job.execution_mode.value,
            created_at=job.created_at,
            completed_at=job.completed_at,
            artifact_count=len(job.artifacts),
            scope=job.intake.diff_spec or job.intake.repo_path,
            outcome=job.report.verdict,
            blocked_reason=job.error,
        )

    def _review_detail(self, job: ReviewJob) -> JobQueryDetail:
        summary = self._review_summary(job)
        return JobQueryDetail(
            **summary.__dict__,
            metadata={
                "review_type": job.job_type.value,
                "workspace_id": job.workspace_id,
                "finding_counts": job.report.finding_counts,
                "verdict_confidence": job.report.verdict_confidence.value,
                "focus_areas": job.intake.focus_areas,
                "include_patterns": job.intake.include_patterns,
                "exclude_patterns": job.intake.exclude_patterns,
            },
        )
