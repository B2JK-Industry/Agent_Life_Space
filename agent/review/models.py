"""
Agent Life Space — Review Domain Model

Canonical domain objects for the reviewer product.
Designed to extend to builder/operator in future phases.

Domain objects:
    ReviewJob      — unit of work with lifecycle
    ReviewFinding  — single observation with severity and evidence
    ReviewArtifact — identifiable, timestamped, exportable output
    ExecutionTrace — audit record of what happened during execution
    ReviewReport   — structured output with executive summary + findings
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class ReviewJobType(str, Enum):
    """Supported review job types."""
    REPO_AUDIT = "repo_audit"
    PR_REVIEW = "pr_review"
    RELEASE_REVIEW = "release_review"


class ReviewJobStatus(str, Enum):
    """Job lifecycle states."""
    CREATED = "created"          # Job exists, not started
    VALIDATING = "validating"    # Input validation in progress
    ANALYZING = "analyzing"      # Analysis running
    VERIFYING = "verifying"      # Verifier pass running
    COMPLETED = "completed"      # Done, report ready
    FAILED = "failed"            # Unrecoverable error
    CANCELLED = "cancelled"      # Cancelled by operator


class Severity(str, Enum):
    """Finding severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ArtifactType(str, Enum):
    """Types of artifacts produced by review."""
    REVIEW_REPORT = "review_report"
    FINDING_LIST = "finding_list"
    EXECUTION_TRACE = "execution_trace"
    DIFF_ANALYSIS = "diff_analysis"
    SECURITY_REPORT = "security_report"
    EXECUTIVE_SUMMARY = "executive_summary"


class Confidence(str, Enum):
    """Confidence level for findings and verdicts."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ─────────────────────────────────────────────
# Review Intake
# ─────────────────────────────────────────────

@dataclass
class ReviewIntake:
    """Input specification for a review job.

    Supports:
        - local repo path
        - git diff / commit range
        - review type selection
        - optional focus areas
    """
    repo_path: str = ""                        # Local path to repository
    diff_spec: str = ""                        # e.g. "HEAD~3..HEAD", "main..feature-branch"
    review_type: ReviewJobType = ReviewJobType.REPO_AUDIT
    focus_areas: list[str] = field(default_factory=list)  # e.g. ["security", "performance"]
    max_files: int = 100                       # Limit scope for large repos
    include_patterns: list[str] = field(default_factory=list)  # e.g. ["*.py", "src/**"]
    exclude_patterns: list[str] = field(default_factory=list)  # e.g. ["*.lock", "node_modules"]
    requester: str = ""                        # Who requested this review
    context: str = ""                          # Free-text context for the reviewer

    def validate(self) -> list[str]:
        """Return list of validation errors. Empty = valid."""
        errors: list[str] = []
        if not self.repo_path:
            errors.append("repo_path is required")
        if self.review_type == ReviewJobType.PR_REVIEW and not self.diff_spec:
            errors.append("diff_spec is required for PR review")
        if self.max_files < 1:
            errors.append("max_files must be >= 1")
        return errors


# ─────────────────────────────────────────────
# Review Finding
# ─────────────────────────────────────────────

@dataclass
class ReviewFinding:
    """Single observation from a review.

    Every finding has severity, evidence, and location.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    severity: Severity = Severity.MEDIUM
    title: str = ""
    description: str = ""
    impact: str = ""                    # Business/security impact of this finding
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    category: str = ""                  # e.g. "security", "architecture", "performance"
    evidence: str = ""                  # What the reviewer saw
    recommendation: str = ""            # What should change
    confidence: Confidence = Confidence.MEDIUM
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "impact": self.impact,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "category": self.category,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "confidence": self.confidence.value,
            "tags": self.tags,
        }

    @property
    def location(self) -> str:
        if not self.file_path:
            return ""
        if self.line_start and self.line_end and self.line_start != self.line_end:
            return f"{self.file_path}:{self.line_start}-{self.line_end}"
        if self.line_start:
            return f"{self.file_path}:{self.line_start}"
        return self.file_path


# ─────────────────────────────────────────────
# Execution Trace
# ─────────────────────────────────────────────

@dataclass
class ExecutionTrace:
    """Audit record of a step during review execution."""
    step: str = ""               # e.g. "validate", "analyze", "verify"
    status: str = "started"      # started, completed, failed
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    detail: str = ""
    error: str = ""

    @property
    def duration_ms(self) -> int:
        if self.completed_at:
            return int((self.completed_at - self.started_at) * 1000)
        return 0

    def complete(self, detail: str = "") -> None:
        self.status = "completed"
        self.completed_at = time.time()
        if detail:
            self.detail = detail

    def fail(self, error: str) -> None:
        self.status = "failed"
        self.completed_at = time.time()
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
            "error": self.error,
        }


# ─────────────────────────────────────────────
# Review Artifact
# ─────────────────────────────────────────────

@dataclass
class ReviewArtifact:
    """Identifiable, timestamped output from a review job."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    artifact_type: ArtifactType = ArtifactType.REVIEW_REPORT
    job_id: str = ""
    content: str = ""                   # Markdown or structured text
    content_json: dict[str, Any] = field(default_factory=dict)  # JSON export
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    format: str = "markdown"            # "markdown" or "json"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "artifact_type": self.artifact_type.value,
            "job_id": self.job_id,
            "content_length": len(self.content),
            "has_json": bool(self.content_json),
            "created_at": self.created_at,
            "format": self.format,
        }


# ─────────────────────────────────────────────
# Review Report
# ─────────────────────────────────────────────

@dataclass
class ReviewReport:
    """Structured review output with canonical sections."""
    executive_summary: str = ""
    findings: list[ReviewFinding] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    verdict: str = ""                   # e.g. "pass", "pass_with_findings", "fail"
    verdict_confidence: Confidence = Confidence.MEDIUM
    scope_description: str = ""
    files_analyzed: int = 0
    total_lines: int = 0

    @property
    def finding_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in Severity}
        for f in self.findings:
            counts[f.severity.value] += 1
        return counts

    @property
    def has_critical(self) -> bool:
        return any(f.severity == Severity.CRITICAL for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executive_summary": self.executive_summary,
            "findings": [f.to_dict() for f in self.findings],
            "finding_counts": self.finding_counts,
            "open_questions": self.open_questions,
            "assumptions": self.assumptions,
            "verdict": self.verdict,
            "verdict_confidence": self.verdict_confidence.value,
            "scope_description": self.scope_description,
            "files_analyzed": self.files_analyzed,
            "total_lines": self.total_lines,
        }

    def to_markdown(self) -> str:
        """Export report as canonical Markdown."""
        lines: list[str] = []
        lines.append("# Review Report\n")

        # Executive summary
        lines.append("## Executive Summary\n")
        lines.append(self.executive_summary or "_No summary available._")
        lines.append("")

        # Verdict
        lines.append(f"**Verdict:** {self.verdict} (confidence: {self.verdict_confidence.value})")
        lines.append(f"**Scope:** {self.scope_description}")
        lines.append(f"**Files analyzed:** {self.files_analyzed} | **Lines:** {self.total_lines}")
        lines.append("")

        # Findings
        counts = self.finding_counts
        lines.append("## Findings\n")
        lines.append(
            f"| Severity | Count |\n|----------|-------|\n"
            f"| Critical | {counts['critical']} |\n"
            f"| High | {counts['high']} |\n"
            f"| Medium | {counts['medium']} |\n"
            f"| Low | {counts['low']} |\n"
        )

        if not self.findings:
            lines.append("_No findings._\n")
        else:
            for f in sorted(self.findings, key=lambda x: list(Severity).index(x.severity)):
                lines.append(f"### [{f.severity.value.upper()}] {f.title}\n")
                if f.location:
                    lines.append(f"**Location:** `{f.location}`")
                if f.category:
                    lines.append(f"**Category:** {f.category}")
                lines.append(f"**Confidence:** {f.confidence.value}\n")
                lines.append(f"{f.description}\n")
                if f.impact:
                    lines.append(f"**Impact:** {f.impact}\n")
                if f.evidence:
                    lines.append(f"**Evidence:**\n```\n{f.evidence}\n```\n")
                if f.recommendation:
                    lines.append(f"**Recommendation:** {f.recommendation}\n")
                lines.append("---\n")

        # Open questions
        if self.open_questions:
            lines.append("## Open Questions\n")
            for q in self.open_questions:
                lines.append(f"- {q}")
            lines.append("")

        # Assumptions
        if self.assumptions:
            lines.append("## Assumptions\n")
            for a in self.assumptions:
                lines.append(f"- {a}")
            lines.append("")

        return "\n".join(lines)


# ─────────────────────────────────────────────
# Review Job
# ─────────────────────────────────────────────

@dataclass
class ReviewJob:
    """Unit of work for the reviewer product.

    Carries full lifecycle: intake → validate → analyze → verify → report.
    Extensible to builder/operator jobs via shared fields.
    """
    # Identity
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    job_type: ReviewJobType = ReviewJobType.REPO_AUDIT
    source: str = "manual"              # "telegram", "api", "manual", "scheduled"
    requester: str = ""
    owner: str = "agent"

    # Input
    intake: ReviewIntake = field(default_factory=ReviewIntake)

    # Workspace
    workspace_id: str = ""

    # Lifecycle
    status: ReviewJobStatus = ReviewJobStatus.CREATED
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str = ""
    completed_at: str = ""

    # Output
    report: ReviewReport = field(default_factory=ReviewReport)
    artifacts: list[ReviewArtifact] = field(default_factory=list)
    execution_trace: list[ExecutionTrace] = field(default_factory=list)

    # Cost
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    model_used: str = ""

    # Error
    error: str = ""

    def trace(self, step: str) -> ExecutionTrace:
        """Start a new execution trace step."""
        t = ExecutionTrace(step=step)
        self.execution_trace.append(t)
        return t

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_type": self.job_type.value,
            "source": self.source,
            "requester": self.requester,
            "owner": self.owner,
            "workspace_id": self.workspace_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "report": self.report.to_dict(),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "execution_trace": [t.to_dict() for t in self.execution_trace],
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "model_used": self.model_used,
            "error": self.error,
            "finding_counts": self.report.finding_counts,
            "verdict": self.report.verdict,
        }
