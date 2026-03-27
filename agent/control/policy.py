"""
Agent Life Space — Control-Plane Policies

Deterministic policy profiles for jobs, artifacts, delivery, review gates,
and external gateway decisions.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.control.models import ArtifactKind, JobKind
from agent.review.models import ReviewJobType


@dataclass(frozen=True)
class JobPersistencePolicy:
    """Deterministic persistence profile for product jobs."""

    id: str
    label: str
    job_kind: JobKind
    retain_days: int
    keep_execution_history: bool = True
    keep_artifact_links: bool = True
    record_cost_ledger: bool = True


@dataclass(frozen=True)
class ArtifactRetentionPolicy:
    """Deterministic retention and recovery profile."""

    id: str
    label: str
    description: str
    retain_days: int
    keep_snapshot: bool = True
    recoverable: bool = True
    max_snapshot_bytes: int = 5 * 1024 * 1024


@dataclass(frozen=True)
class ReviewGatePolicy:
    """Deterministic blocking profile for post-build review."""

    id: str
    label: str
    description: str
    max_critical: int
    max_high: int
    block_fail_verdict: bool = True
    advisory_only: bool = False


@dataclass(frozen=True)
class ReviewExecutionPolicy:
    """Deterministic execution boundary for repository and diff review access."""

    id: str
    label: str
    description: str
    allow_host_read: bool = True
    allow_git_subprocess: bool = False
    allowed_sources: tuple[str, ...] = ("manual", "telegram", "api", "operator")
    allowed_review_types: tuple[ReviewJobType, ...] = (
        ReviewJobType.REPO_AUDIT,
        ReviewJobType.RELEASE_REVIEW,
    )


@dataclass(frozen=True)
class DeliveryDecisionPolicy:
    """Deterministic delivery policy profile."""

    id: str
    label: str
    approval_required: bool = True
    allow_external_send: bool = False
    gateway_required: bool = False


@dataclass(frozen=True)
class ExternalGatewayPolicy:
    """Deterministic external-gateway policy profile."""

    id: str
    label: str
    enabled: bool = False
    require_approval: bool = True
    record_cost: bool = True
    allow_network: bool = False


_REVIEW_GATE_POLICIES: dict[str, ReviewGatePolicy] = {
    "critical_findings": ReviewGatePolicy(
        id="critical_findings",
        label="Critical findings block",
        description="Block completion only when critical findings or fail verdicts appear.",
        max_critical=0,
        max_high=999,
        block_fail_verdict=True,
    ),
    "high_or_critical": ReviewGatePolicy(
        id="high_or_critical",
        label="High or critical findings block",
        description="Block completion when critical or high findings appear.",
        max_critical=0,
        max_high=0,
        block_fail_verdict=True,
    ),
    "advisory": ReviewGatePolicy(
        id="advisory",
        label="Advisory only",
        description="Never block completion on review findings; capture them for delivery.",
        max_critical=999,
        max_high=999,
        block_fail_verdict=False,
        advisory_only=True,
    ),
}

_REVIEW_EXECUTION_POLICIES: dict[str, ReviewExecutionPolicy] = {
    "repo_host_read_only": ReviewExecutionPolicy(
        id="repo_host_read_only",
        label="Repo host read-only",
        description=(
            "Allow read-only host access for repository-wide or release review "
            "without invoking git diff subprocesses."
        ),
        allow_host_read=True,
        allow_git_subprocess=False,
        allowed_review_types=(
            ReviewJobType.REPO_AUDIT,
            ReviewJobType.RELEASE_REVIEW,
        ),
    ),
    "diff_host_git_read_only": ReviewExecutionPolicy(
        id="diff_host_git_read_only",
        label="Diff host+git read-only",
        description=(
            "Allow read-only host access plus git diff subprocess execution for "
            "explicit PR/diff review requests."
        ),
        allow_host_read=True,
        allow_git_subprocess=True,
        allowed_review_types=(ReviewJobType.PR_REVIEW,),
    ),
}

_JOB_PERSISTENCE_POLICIES: dict[str, JobPersistencePolicy] = {
    "build_persistent": JobPersistencePolicy(
        id="build_persistent",
        label="Build job persistence",
        job_kind=JobKind.BUILD,
        retain_days=365,
    ),
    "review_persistent": JobPersistencePolicy(
        id="review_persistent",
        label="Review job persistence",
        job_kind=JobKind.REVIEW,
        retain_days=365,
    ),
}

_ARTIFACT_RETENTION_POLICIES: dict[str, ArtifactRetentionPolicy] = {
    "delivery_evidence_365d": ArtifactRetentionPolicy(
        id="delivery_evidence_365d",
        label="Delivery evidence",
        description="Keep delivery and client-facing evidence for one year with recovery snapshots.",
        retain_days=365,
        keep_snapshot=True,
        recoverable=True,
    ),
    "artifact_recovery_180d": ArtifactRetentionPolicy(
        id="artifact_recovery_180d",
        label="Artifact recovery",
        description="Keep implementation artifacts and reports for 180 days with recovery snapshots.",
        retain_days=180,
        keep_snapshot=True,
        recoverable=True,
    ),
    "operational_trace_30d": ArtifactRetentionPolicy(
        id="operational_trace_30d",
        label="Operational traces",
        description="Keep operational traces and verification telemetry for 30 days.",
        retain_days=30,
        keep_snapshot=True,
        recoverable=True,
    ),
}

_DELIVERY_POLICIES: dict[str, DeliveryDecisionPolicy] = {
    "approval_required": DeliveryDecisionPolicy(
        id="approval_required",
        label="Approval required",
        approval_required=True,
        allow_external_send=False,
        gateway_required=False,
    ),
    "gateway_only": DeliveryDecisionPolicy(
        id="gateway_only",
        label="Gateway only",
        approval_required=True,
        allow_external_send=False,
        gateway_required=True,
    ),
}

_EXTERNAL_GATEWAY_POLICIES: dict[str, ExternalGatewayPolicy] = {
    "disabled_by_default": ExternalGatewayPolicy(
        id="disabled_by_default",
        label="Disabled by default",
        enabled=False,
        require_approval=True,
        record_cost=True,
        allow_network=False,
    ),
    "approval_before_gateway": ExternalGatewayPolicy(
        id="approval_before_gateway",
        label="Approval before gateway",
        enabled=True,
        require_approval=True,
        record_cost=True,
        allow_network=False,
    ),
}


def get_review_gate_policy(policy_id: str = "critical_findings") -> ReviewGatePolicy:
    """Resolve a configured review gate policy."""
    return _REVIEW_GATE_POLICIES.get(policy_id, _REVIEW_GATE_POLICIES["critical_findings"])


def list_review_gate_policies() -> list[ReviewGatePolicy]:
    """Return known post-build review gate policies."""
    return list(_REVIEW_GATE_POLICIES.values())


def get_review_execution_policy(
    policy_id: str = "repo_host_read_only",
) -> ReviewExecutionPolicy:
    """Resolve a configured review execution policy."""
    return _REVIEW_EXECUTION_POLICIES.get(
        policy_id,
        _REVIEW_EXECUTION_POLICIES["repo_host_read_only"],
    )


def list_review_execution_policies() -> list[ReviewExecutionPolicy]:
    """Return known review execution policies."""
    return list(_REVIEW_EXECUTION_POLICIES.values())


def select_review_execution_policy(
    *,
    review_type: ReviewJobType | str,
    diff_spec: str = "",
    source: str = "manual",
) -> ReviewExecutionPolicy:
    """Select a deterministic execution policy for a review request."""
    normalized_type = (
        review_type
        if isinstance(review_type, ReviewJobType)
        else ReviewJobType(str(review_type))
    )
    policy = (
        _REVIEW_EXECUTION_POLICIES["diff_host_git_read_only"]
        if normalized_type == ReviewJobType.PR_REVIEW or diff_spec
        else _REVIEW_EXECUTION_POLICIES["repo_host_read_only"]
    )
    if source and source not in policy.allowed_sources:
        return ReviewExecutionPolicy(
            id=f"{policy.id}_blocked_source",
            label=f"{policy.label} (blocked)",
            description=f"Blocked unknown review source '{source}'.",
            allow_host_read=False,
            allow_git_subprocess=False,
            allowed_sources=policy.allowed_sources,
            allowed_review_types=policy.allowed_review_types,
        )
    if normalized_type not in policy.allowed_review_types:
        return ReviewExecutionPolicy(
            id=f"{policy.id}_blocked_type",
            label=f"{policy.label} (blocked)",
            description=(
                f"Blocked review type '{normalized_type.value}' under execution "
                f"policy '{policy.id}'."
            ),
            allow_host_read=False,
            allow_git_subprocess=False,
            allowed_sources=policy.allowed_sources,
            allowed_review_types=policy.allowed_review_types,
        )
    return policy


def get_job_persistence_policy(job_kind: JobKind | str) -> JobPersistencePolicy:
    """Resolve the persistence profile for a product job kind."""
    normalized = job_kind if isinstance(job_kind, JobKind) else JobKind(str(job_kind))
    if normalized == JobKind.REVIEW:
        return _JOB_PERSISTENCE_POLICIES["review_persistent"]
    return _JOB_PERSISTENCE_POLICIES["build_persistent"]


def list_job_persistence_policies() -> list[JobPersistencePolicy]:
    """Return known product-job persistence profiles."""
    return list(_JOB_PERSISTENCE_POLICIES.values())


def get_artifact_retention_policy(
    policy_id: str = "artifact_recovery_180d",
) -> ArtifactRetentionPolicy:
    """Resolve a configured artifact retention policy."""
    return _ARTIFACT_RETENTION_POLICIES.get(
        policy_id,
        _ARTIFACT_RETENTION_POLICIES["artifact_recovery_180d"],
    )


def list_artifact_retention_policies() -> list[ArtifactRetentionPolicy]:
    """Return known artifact retention profiles."""
    return list(_ARTIFACT_RETENTION_POLICIES.values())


def select_artifact_retention_policy(
    *,
    job_kind: JobKind | str,
    artifact_kind: ArtifactKind | str,
) -> ArtifactRetentionPolicy:
    """Select a deterministic retention profile for a shared artifact."""
    _ = job_kind if isinstance(job_kind, JobKind) else JobKind(str(job_kind))
    kind = (
        artifact_kind
        if isinstance(artifact_kind, ArtifactKind)
        else ArtifactKind(str(artifact_kind))
    )
    if kind in {
        ArtifactKind.REVIEW_REPORT,
        ArtifactKind.FINDING_LIST,
        ArtifactKind.SECURITY_REPORT,
        ArtifactKind.EXECUTIVE_SUMMARY,
        ArtifactKind.DELIVERY_BUNDLE,
    }:
        return _ARTIFACT_RETENTION_POLICIES["delivery_evidence_365d"]
    if kind in {
        ArtifactKind.PATCH,
        ArtifactKind.DIFF,
        ArtifactKind.ACCEPTANCE_REPORT,
    }:
        return _ARTIFACT_RETENTION_POLICIES["artifact_recovery_180d"]
    return _ARTIFACT_RETENTION_POLICIES["operational_trace_30d"]


def get_delivery_policy(policy_id: str = "approval_required") -> DeliveryDecisionPolicy:
    """Resolve a configured delivery decision policy."""
    return _DELIVERY_POLICIES.get(policy_id, _DELIVERY_POLICIES["approval_required"])


def list_delivery_policies() -> list[DeliveryDecisionPolicy]:
    """Return known delivery decision policies."""
    return list(_DELIVERY_POLICIES.values())


def get_external_gateway_policy(
    policy_id: str = "disabled_by_default",
) -> ExternalGatewayPolicy:
    """Resolve a configured external gateway policy."""
    return _EXTERNAL_GATEWAY_POLICIES.get(
        policy_id,
        _EXTERNAL_GATEWAY_POLICIES["disabled_by_default"],
    )


def list_external_gateway_policies() -> list[ExternalGatewayPolicy]:
    """Return known external gateway policy profiles."""
    return list(_EXTERNAL_GATEWAY_POLICIES.values())
