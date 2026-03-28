"""
Agent Life Space — Control-Plane Policies

Deterministic policy profiles for jobs, artifacts, delivery, review gates,
and external gateway decisions.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.build.models import BuildJobType
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
class BuildExecutionPolicy:
    """Deterministic execution boundary for mutable builder work."""

    id: str
    label: str
    description: str
    environment_profile_id: str = "build_workspace_local"
    allow_workspace_mutation: bool = True
    workspace_required: bool = True
    allowed_sources: tuple[str, ...] = ("manual", "operator", "api", "resume")
    allowed_build_types: tuple[BuildJobType, ...] = (
        BuildJobType.IMPLEMENTATION,
        BuildJobType.INTEGRATION,
        BuildJobType.DEVOPS,
        BuildJobType.TESTING,
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


@dataclass(frozen=True)
class EnvironmentProfile:
    """Deterministic execution environment profile."""

    id: str
    label: str
    description: str
    execution_mode: str
    workspace_required: bool = False
    host_read_only: bool = False
    allow_network: bool = False
    acquisition_allowed: bool = False


@dataclass(frozen=True)
class OperatingEnvironmentProfile:
    """Higher-level local/operator/enterprise execution posture."""

    id: str
    label: str
    description: str
    intended_for: str
    default_environment_profile_ids: tuple[str, ...]
    default_build_execution_policy_id: str
    default_delivery_policy_id: str
    default_gateway_policy_id: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EscalationBudgetPolicy:
    """Deterministic rule for when response/model escalation is allowed."""

    id: str
    label: str
    block_on_hard_cap: bool = True
    block_on_stop_loss: bool = True
    block_on_soft_cap: bool = True
    block_on_requires_approval: bool = True


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

_BUILD_EXECUTION_POLICIES: dict[str, BuildExecutionPolicy] = {
    "workspace_local_mutation": BuildExecutionPolicy(
        id="workspace_local_mutation",
        label="Workspace local mutation",
        description=(
            "Allow mutable builder execution only inside the managed workspace "
            "for approved local/operator build sources."
        ),
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

_ENVIRONMENT_PROFILES: dict[str, EnvironmentProfile] = {
    "review_host_read_only": EnvironmentProfile(
        id="review_host_read_only",
        label="Review host read-only",
        description="Read-only host access for repository analysis without mutable execution.",
        execution_mode="read_only_host",
        workspace_required=False,
        host_read_only=True,
        allow_network=False,
        acquisition_allowed=False,
    ),
    "build_workspace_local": EnvironmentProfile(
        id="build_workspace_local",
        label="Build workspace local",
        description="Workspace-bound mutable execution for builder flows.",
        execution_mode="workspace_bound",
        workspace_required=True,
        host_read_only=False,
        allow_network=False,
        acquisition_allowed=False,
    ),
    "repo_import_mirror": EnvironmentProfile(
        id="repo_import_mirror",
        label="Repo import mirror",
        description="Acquire a supported git source into a managed local mirror before routing.",
        execution_mode="read_only_host",
        workspace_required=False,
        host_read_only=True,
        allow_network=False,
        acquisition_allowed=True,
    ),
    "delivery_export_only": EnvironmentProfile(
        id="delivery_export_only",
        label="Delivery export only",
        description="Assemble export/evidence packages without performing external send.",
        execution_mode="read_only_host",
        workspace_required=False,
        host_read_only=True,
        allow_network=False,
        acquisition_allowed=False,
    ),
}

_OPERATING_ENVIRONMENT_PROFILES: dict[str, OperatingEnvironmentProfile] = {
    "local_owner": OperatingEnvironmentProfile(
        id="local_owner",
        label="Local owner",
        description=(
            "Single-machine owner workflow using CLI and local managed workspaces "
            "without external send."
        ),
        intended_for="Local owner-operated development and review",
        default_environment_profile_ids=(
            "review_host_read_only",
            "build_workspace_local",
            "delivery_export_only",
        ),
        default_build_execution_policy_id="workspace_local_mutation",
        default_delivery_policy_id="approval_required",
        default_gateway_policy_id="disabled_by_default",
        notes=(
            "Mutable work remains workspace-bound.",
            "External send still stays approval-gated and disabled by default.",
        ),
    ),
    "operator_controlled": OperatingEnvironmentProfile(
        id="operator_controlled",
        label="Operator controlled",
        description=(
            "Operator-routed build/review workflow with managed acquisition, "
            "approval, and handoff discipline."
        ),
        intended_for="CLI-orchestrated operator workflow over review and build jobs",
        default_environment_profile_ids=(
            "review_host_read_only",
            "build_workspace_local",
            "repo_import_mirror",
            "delivery_export_only",
        ),
        default_build_execution_policy_id="workspace_local_mutation",
        default_delivery_policy_id="approval_required",
        default_gateway_policy_id="disabled_by_default",
        notes=(
            "Managed repo import is allowed before routing.",
            "Delivery remains handoff-oriented until a real external send path exists.",
        ),
    ),
    "enterprise_hardened": OperatingEnvironmentProfile(
        id="enterprise_hardened",
        label="Enterprise hardened",
        description=(
            "Policy-first posture for stricter deployment environments with "
            "managed import, approval-heavy delivery, and gateway-ready defaults."
        ),
        intended_for="Controlled deployment environments with stronger compliance posture",
        default_environment_profile_ids=(
            "review_host_read_only",
            "build_workspace_local",
            "repo_import_mirror",
            "delivery_export_only",
        ),
        default_build_execution_policy_id="workspace_local_mutation",
        default_delivery_policy_id="gateway_only",
        default_gateway_policy_id="approval_before_gateway",
        notes=(
            "Assumes stronger approval and audit requirements around delivery.",
            "Environment matrix exists before the live enterprise runtime does.",
        ),
    ),
}

_ESCALATION_BUDGET_POLICIES: dict[str, EscalationBudgetPolicy] = {
    "cost_guarded": EscalationBudgetPolicy(
        id="cost_guarded",
        label="Cost-guarded escalation",
        block_on_hard_cap=True,
        block_on_stop_loss=True,
        block_on_soft_cap=True,
        block_on_requires_approval=True,
    )
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


def get_build_execution_policy(
    policy_id: str = "workspace_local_mutation",
) -> BuildExecutionPolicy:
    """Resolve a configured build execution policy."""
    return _BUILD_EXECUTION_POLICIES.get(
        policy_id,
        _BUILD_EXECUTION_POLICIES["workspace_local_mutation"],
    )


def list_build_execution_policies() -> list[BuildExecutionPolicy]:
    """Return known build execution policies."""
    return list(_BUILD_EXECUTION_POLICIES.values())


def select_build_execution_policy(
    *,
    build_type: BuildJobType | str,
    source: str = "manual",
    policy_id: str = "workspace_local_mutation",
) -> BuildExecutionPolicy:
    """Select a deterministic build execution policy for mutable work."""
    normalized_type = (
        build_type if isinstance(build_type, BuildJobType) else BuildJobType(str(build_type))
    )
    policy = get_build_execution_policy(policy_id)
    if not policy.allow_workspace_mutation:
        return policy
    if source and source not in policy.allowed_sources:
        return BuildExecutionPolicy(
            id=f"{policy.id}_blocked_source",
            label=f"{policy.label} (blocked)",
            description=f"Blocked unknown build source '{source}'.",
            environment_profile_id=policy.environment_profile_id,
            allow_workspace_mutation=False,
            workspace_required=policy.workspace_required,
            allowed_sources=policy.allowed_sources,
            allowed_build_types=policy.allowed_build_types,
        )
    if normalized_type not in policy.allowed_build_types:
        return BuildExecutionPolicy(
            id=f"{policy.id}_blocked_type",
            label=f"{policy.label} (blocked)",
            description=(
                f"Blocked build type '{normalized_type.value}' under execution policy "
                f"'{policy.id}'."
            ),
            environment_profile_id=policy.environment_profile_id,
            allow_workspace_mutation=False,
            workspace_required=policy.workspace_required,
            allowed_sources=policy.allowed_sources,
            allowed_build_types=policy.allowed_build_types,
        )
    return policy


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


def get_environment_profile(
    profile_id: str = "review_host_read_only",
) -> EnvironmentProfile:
    """Resolve an execution environment profile."""
    return _ENVIRONMENT_PROFILES.get(
        profile_id,
        _ENVIRONMENT_PROFILES["review_host_read_only"],
    )


def list_environment_profiles() -> list[EnvironmentProfile]:
    """Return known execution environment profiles."""
    return list(_ENVIRONMENT_PROFILES.values())


def get_operating_environment_profile(
    profile_id: str = "local_owner",
) -> OperatingEnvironmentProfile:
    """Resolve a higher-level operating environment profile."""
    return _OPERATING_ENVIRONMENT_PROFILES.get(
        profile_id,
        _OPERATING_ENVIRONMENT_PROFILES["local_owner"],
    )


def list_operating_environment_profiles() -> list[OperatingEnvironmentProfile]:
    """Return higher-level local/operator/enterprise environment profiles."""
    return list(_OPERATING_ENVIRONMENT_PROFILES.values())


def get_escalation_budget_policy(
    policy_id: str = "cost_guarded",
) -> EscalationBudgetPolicy:
    """Resolve the budget posture policy for response escalation."""
    return _ESCALATION_BUDGET_POLICIES.get(
        policy_id,
        _ESCALATION_BUDGET_POLICIES["cost_guarded"],
    )


def allow_budget_escalation(
    budget_status: dict[str, object] | None,
    *,
    policy_id: str = "cost_guarded",
) -> tuple[bool, str]:
    """Return whether model escalation is allowed under the current budget posture."""
    policy = get_escalation_budget_policy(policy_id)
    status = budget_status or {}
    if policy.block_on_hard_cap and bool(status.get("hard_cap_hit")):
        return False, "Budget hard cap blocks model escalation."
    if policy.block_on_stop_loss and bool(status.get("stop_loss_hit")):
        return False, "Budget stop-loss blocks model escalation."
    if policy.block_on_requires_approval and bool(status.get("requires_approval")):
        return False, "Budget approval requirement blocks model escalation."
    if policy.block_on_soft_cap and bool(status.get("soft_cap_hit")):
        return False, "Budget soft cap blocks model escalation."
    return True, ""
