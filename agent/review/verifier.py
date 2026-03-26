"""
Agent Life Space — Review Verifier

Post-analysis verification pass. Deterministic checks on findings:
    - false positive reduction (known safe patterns)
    - severity consistency (critical finding in test file → downgrade)
    - evidence completeness (finding without file ref → flag)
    - confidence adjustment

No LLM — pure rules.
"""

from __future__ import annotations

import structlog

from agent.review.models import Confidence, ReviewFinding, ReviewReport, Severity

logger = structlog.get_logger(__name__)


def verify_report(report: ReviewReport) -> ReviewReport:
    """Run verification pass over a report. Modifies findings in-place."""
    verified: list[ReviewFinding] = []
    removed = 0

    for finding in report.findings:
        adjusted = _verify_finding(finding)
        if adjusted is not None:
            verified.append(adjusted)
        else:
            removed += 1

    report.findings = verified

    if removed:
        logger.info("verifier_removed_findings", count=removed)

    return report


def _verify_finding(finding: ReviewFinding) -> ReviewFinding | None:
    """Verify a single finding. Returns adjusted finding or None to remove."""

    # Rule 1: Test files — downgrade severity for secret/unsafe findings
    if finding.file_path and "test" in finding.file_path.lower():
        if finding.category == "security" and finding.severity in (Severity.CRITICAL, Severity.HIGH):
            # Secrets in test files are usually mock values
            if any(w in (finding.evidence or "").lower() for w in ["mock", "fake", "test", "dummy", "example"]):
                return None  # Remove — false positive
            finding.severity = Severity.LOW
            finding.confidence = Confidence.LOW
            finding.tags.append("downgraded:test_file")

    # Rule 2: Findings without file path get lower confidence
    if not finding.file_path and finding.confidence == Confidence.HIGH:
        finding.confidence = Confidence.MEDIUM

    # Rule 3: Critical findings need evidence
    if finding.severity == Severity.CRITICAL and not finding.evidence:
        finding.confidence = Confidence.LOW
        finding.tags.append("needs_evidence")

    # Rule 4: Known safe patterns
    if finding.title == "eval() usage" and finding.file_path:
        # ast.literal_eval is safe
        if "literal_eval" in (finding.evidence or ""):
            return None

    return finding
