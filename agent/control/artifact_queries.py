"""
Agent Life Space — Cross-System Artifact Queries

Shared inspection and recovery layer for build/review artifacts.
Normalizes bounded-context artifact storage into one control-plane surface.
"""

from __future__ import annotations

from typing import Any

from agent.control.models import (
    ArtifactKind,
    ArtifactQueryDetail,
    ArtifactQuerySummary,
    JobKind,
)

_REVIEW_TO_SHARED_KIND = {
    "review_report": ArtifactKind.REVIEW_REPORT,
    "finding_list": ArtifactKind.FINDING_LIST,
    "execution_trace": ArtifactKind.EXECUTION_TRACE,
    "diff_analysis": ArtifactKind.DIFF_ANALYSIS,
    "security_report": ArtifactKind.SECURITY_REPORT,
    "executive_summary": ArtifactKind.EXECUTIVE_SUMMARY,
}


class ArtifactQueryService:
    """Query build and review artifacts through one shared control-plane API."""

    def __init__(
        self,
        build_service: Any = None,
        review_service: Any = None,
    ) -> None:
        self._build_service = build_service
        self._review_service = review_service

    def list_artifacts(
        self,
        *,
        kind: JobKind | str | None = None,
        job_id: str = "",
        artifact_kind: str = "",
        limit: int = 20,
    ) -> list[ArtifactQuerySummary]:
        normalized_kind = self._normalize_kind(kind)
        records: list[ArtifactQuerySummary] = []

        if normalized_kind in (None, JobKind.BUILD) and self._build_service is not None:
            build_kind = artifact_kind if self._is_build_kind(artifact_kind) else ""
            records.extend(
                self._build_summary(artifact)
                for artifact in self._build_service.list_artifacts(
                    job_id=job_id,
                    artifact_kind=build_kind,
                    limit=limit,
                )
            )

        if normalized_kind in (None, JobKind.REVIEW) and self._review_service is not None:
            review_kind = self._to_review_artifact_type(artifact_kind)
            records.extend(
                self._review_summary(artifact)
                for artifact in self._review_service.list_artifacts(
                    job_id=job_id,
                    artifact_kind=review_kind,
                    limit=limit,
                )
            )

        records.sort(key=lambda artifact: artifact.created_at, reverse=True)
        return records[:limit]

    def get_artifact(
        self,
        artifact_id: str,
        *,
        kind: JobKind | str | None = None,
    ) -> ArtifactQueryDetail | None:
        normalized_kind = self._normalize_kind(kind)

        if normalized_kind in (None, JobKind.BUILD) and self._build_service is not None:
            build_artifact = self._build_service.get_artifact(artifact_id)
            if build_artifact is not None:
                return self._build_detail(build_artifact)

        if normalized_kind in (None, JobKind.REVIEW) and self._review_service is not None:
            review_artifact = self._review_service.get_artifact(artifact_id)
            if review_artifact is not None:
                return self._review_detail(review_artifact)

        return None

    def _normalize_kind(self, kind: JobKind | str | None) -> JobKind | None:
        if kind in (None, "", "all"):
            return None
        if isinstance(kind, JobKind):
            return kind
        return JobKind(str(kind))

    def _is_build_kind(self, artifact_kind: str) -> bool:
        if not artifact_kind:
            return False
        try:
            candidate = ArtifactKind(artifact_kind)
        except ValueError:
            return False
        return candidate in {
            ArtifactKind.PATCH,
            ArtifactKind.DIFF,
            ArtifactKind.VERIFICATION_REPORT,
            ArtifactKind.ACCEPTANCE_REPORT,
            ArtifactKind.REVIEW_REPORT,
            ArtifactKind.FINDING_LIST,
            ArtifactKind.EXECUTION_TRACE,
        }

    def _to_review_artifact_type(self, artifact_kind: str) -> str:
        if not artifact_kind:
            return ""
        if artifact_kind in _REVIEW_TO_SHARED_KIND:
            return artifact_kind
        for review_type, shared_kind in _REVIEW_TO_SHARED_KIND.items():
            if shared_kind.value == artifact_kind:
                return review_type
        return ""

    def _build_summary(self, artifact: dict[str, Any]) -> ArtifactQuerySummary:
        content = artifact.get("content", "")
        content_json = artifact.get("content_json") or {}
        kind = ArtifactKind(artifact.get("artifact_kind", "execution_trace"))
        return ArtifactQuerySummary(
            artifact_id=artifact.get("id", ""),
            artifact_kind=kind,
            job_id=artifact.get("job_id", ""),
            job_kind=JobKind.BUILD,
            source_type="build_artifact",
            format=artifact.get("format", "text"),
            created_at=artifact.get("created_at", ""),
            content_length=len(content),
            has_json=bool(content_json),
            title=self._artifact_title(kind, JobKind.BUILD),
        )

    def _build_detail(self, artifact: dict[str, Any]) -> ArtifactQueryDetail:
        summary = self._build_summary(artifact)
        return ArtifactQueryDetail(
            **summary.__dict__,
            content=artifact.get("content", ""),
            content_json=artifact.get("content_json") or {},
            metadata={
                "domain": "build",
                "storage_kind": artifact.get("artifact_kind", ""),
            },
        )

    def _review_summary(self, artifact: dict[str, Any]) -> ArtifactQuerySummary:
        artifact_type = artifact.get("artifact_type", "review_report")
        kind = _REVIEW_TO_SHARED_KIND.get(artifact_type, ArtifactKind.REVIEW_REPORT)
        content = artifact.get("content", "")
        content_json = artifact.get("content_json") or {}
        return ArtifactQuerySummary(
            artifact_id=artifact.get("id", ""),
            artifact_kind=kind,
            job_id=artifact.get("job_id", ""),
            job_kind=JobKind.REVIEW,
            source_type="review_artifact",
            format=artifact.get("format", "markdown"),
            created_at=artifact.get("created_at", ""),
            content_length=len(content),
            has_json=bool(content_json),
            title=self._artifact_title(kind, JobKind.REVIEW),
        )

    def _review_detail(self, artifact: dict[str, Any]) -> ArtifactQueryDetail:
        summary = self._review_summary(artifact)
        return ArtifactQueryDetail(
            **summary.__dict__,
            content=artifact.get("content", ""),
            content_json=artifact.get("content_json") or {},
            metadata={
                "domain": "review",
                "storage_kind": artifact.get("artifact_type", ""),
            },
        )

    def _artifact_title(self, kind: ArtifactKind, job_kind: JobKind) -> str:
        labels = {
            ArtifactKind.REVIEW_REPORT: "Review report",
            ArtifactKind.FINDING_LIST: "Finding list",
            ArtifactKind.DIFF_ANALYSIS: "Diff analysis",
            ArtifactKind.SECURITY_REPORT: "Security report",
            ArtifactKind.EXECUTIVE_SUMMARY: "Executive summary",
            ArtifactKind.PATCH: "Patch export",
            ArtifactKind.DIFF: "Workspace diff",
            ArtifactKind.VERIFICATION_REPORT: "Verification report",
            ArtifactKind.ACCEPTANCE_REPORT: "Acceptance report",
            ArtifactKind.EXECUTION_TRACE: "Execution trace",
        }
        return f"{job_kind.value}:{labels.get(kind, kind.value)}"
