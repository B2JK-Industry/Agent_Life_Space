"""
Agent Life Space — Review Redaction Policy

Policy-driven redaction for review output.
Separates internal operator export from client-safe export.

Redaction targets:
    - absolute filesystem paths
    - hostname/username patterns
    - secret values in evidence
    - internal execution metadata
    - raw error messages with stack traces
"""

from __future__ import annotations

import re
from typing import Any

# ─────────────────────────────────────────────
# Redaction patterns
# ─────────────────────────────────────────────

_PATH_PATTERNS = [
    re.compile(r'/(?:Users|home|root)/[^\s"\'`\]]+'),
    re.compile(r'/(?:var|tmp|private)/[^\s"\'`\]]+'),
    re.compile(r'[A-Z]:\\(?:Users|Documents)[^\s"\'`\]]+'),  # Windows
]

_HOSTNAME_PATTERNS = [
    re.compile(r'\b(?:b2jk-\w+|agent-?life-?space)\b', re.IGNORECASE),
]

_SECRET_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|api[_-]?secret|password|token|secret)\s*=\s*["\'][^"\']{8,}["\']'),
    re.compile(r'(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}'),
    re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----'),
]


def redact_paths(text: str) -> str:
    """Replace absolute filesystem paths with [PATH_REDACTED]."""
    for pattern in _PATH_PATTERNS:
        text = pattern.sub("[PATH_REDACTED]", text)
    return text


def redact_hostnames(text: str) -> str:
    """Replace known hostname patterns with [HOST_REDACTED]."""
    for pattern in _HOSTNAME_PATTERNS:
        text = pattern.sub("[HOST_REDACTED]", text)
    return text


def redact_secrets(text: str) -> str:
    """Replace secret values with [SECRET_REDACTED]."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[SECRET_REDACTED]", text)
    return text


def apply_client_redaction(text: str) -> str:
    """Apply full client-safe redaction pipeline."""
    text = redact_paths(text)
    text = redact_hostnames(text)
    text = redact_secrets(text)
    return text


def redact_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Redact a single finding dict for client-safe export.

    All client-facing text fields go through apply_client_redaction()
    (paths + hostnames + secrets). File paths are redacted only if absolute.
    """
    f = dict(finding)
    if f.get("evidence"):
        f["evidence"] = apply_client_redaction(f["evidence"])
    if f.get("file_path"):
        # Keep relative paths, redact absolute
        if f["file_path"].startswith("/") or f["file_path"].startswith("\\"):
            f["file_path"] = redact_paths(f["file_path"])
    if f.get("location"):
        f["location"] = apply_client_redaction(str(f["location"]))
    # All client-facing text fields through full redaction pipeline
    for text_field in ("description", "impact", "recommendation"):
        if f.get(text_field):
            f[text_field] = apply_client_redaction(f[text_field])
    return f


def redact_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Apply full redaction to a delivery bundle. Returns new dict."""
    result = dict(bundle)

    # Redact markdown report
    if result.get("markdown_report"):
        result["markdown_report"] = apply_client_redaction(result["markdown_report"])
    if result.get("operator_summary_markdown"):
        result["operator_summary_markdown"] = apply_client_redaction(
            result["operator_summary_markdown"]
        )
    if result.get("pr_comment_markdown"):
        result["pr_comment_markdown"] = apply_client_redaction(
            result["pr_comment_markdown"]
        )

    # Redact findings
    if result.get("findings_only"):
        result["findings_only"] = [redact_finding(f) for f in result["findings_only"]]

    # Redact JSON report
    jr = result.get("json_report", {})
    if jr:
        jr = dict(jr)
        if jr.get("scope_description"):
            jr["scope_description"] = apply_client_redaction(jr["scope_description"])
        if jr.get("executive_summary"):
            jr["executive_summary"] = apply_client_redaction(jr["executive_summary"])
        if jr.get("findings"):
            jr["findings"] = [redact_finding(f) for f in jr["findings"]]
        result["json_report"] = jr

    summary_pack = result.get("summary_pack", {})
    if summary_pack:
        redacted_pack = dict(summary_pack)
        for key in ("operator_summary_markdown", "pr_comment_markdown", "scope_description"):
            if redacted_pack.get(key):
                redacted_pack[key] = apply_client_redaction(redacted_pack[key])
        if redacted_pack.get("top_findings"):
            redacted_pack["top_findings"] = [
                redact_finding(finding) for finding in redacted_pack["top_findings"]
            ]
        if redacted_pack.get("open_questions"):
            redacted_pack["open_questions"] = [
                apply_client_redaction(str(item))
                for item in redacted_pack["open_questions"]
            ]
        result["summary_pack"] = redacted_pack

    # Strip internal-only fields
    result.pop("execution_trace", None)
    result.pop("execution_mode", None)
    result.pop("requester", None)       # Internal identity
    result.pop("source", None)          # Internal channel (telegram/api/manual)

    # Redact error field if present (may leak paths/traces)
    if result.get("error"):
        result["error"] = apply_client_redaction(result["error"])

    result["export_mode"] = "client_safe"
    return result
