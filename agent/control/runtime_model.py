"""
Agent Life Space — Runtime Model

Explicit coexistence rules for the wider runtime model.
Makes the relationship between product jobs, planning tasks,
infrastructure jobs, and conversational loop items visible.
"""

from __future__ import annotations

from agent.control.policy import (
    list_build_execution_policies,
    list_data_handling_rules,
    list_environment_profiles,
    list_external_capability_providers,
    list_external_capability_routes,
    list_external_gateway_contracts,
    list_external_gateway_policies,
    list_operating_environment_profiles,
)


class RuntimeModelService:
    """Describe the canonical role of each runtime surface."""

    def get_model(self) -> dict[str, object]:
        surfaces = [
            {
                "surface": "BuildJob",
                "category": "product_job",
                "owner": "agent.build",
                "canonical_for": [
                    "implementation execution",
                    "workspace-bound verification",
                    "acceptance tracking",
                ],
                "not_for": [
                    "background maintenance scheduling",
                    "conversation queue buffering",
                ],
                "durability": "sqlite + artifact storage",
                "query_surface": "control-plane jobs/artifacts",
                "coexistence_rule": (
                    "Use BuildJob as the source of truth for operator-visible build work."
                ),
            },
            {
                "surface": "ReviewJob",
                "category": "product_job",
                "owner": "agent.review",
                "canonical_for": [
                    "repo/pr/release review execution",
                    "review artifact recovery",
                    "delivery approval gating",
                ],
                "not_for": [
                    "task decomposition",
                    "background retries",
                ],
                "durability": "sqlite + artifact storage",
                "query_surface": "control-plane jobs/artifacts",
                "coexistence_rule": (
                    "Use ReviewJob as the source of truth for operator-visible review work."
                ),
            },
            {
                "surface": "Task",
                "category": "planning_commitment",
                "owner": "agent.tasks",
                "canonical_for": [
                    "internal planning commitments",
                    "dependency tracking",
                    "scheduled/recurring work",
                ],
                "not_for": [
                    "product review/build execution history",
                ],
                "durability": "sqlite task store",
                "query_surface": "control-plane jobs (operate subkind=task)",
                "coexistence_rule": (
                    "Tasks may reference product jobs, but they do not replace product job records."
                ),
            },
            {
                "surface": "JobRunner",
                "category": "infrastructure_execution",
                "owner": "agent.core.job_runner",
                "canonical_for": [
                    "timeouts, retries, dead-letter handling",
                    "maintenance/background execution records",
                ],
                "not_for": [
                    "build/review lifecycle modeling",
                ],
                "durability": "in-memory runtime history",
                "query_surface": "control-plane jobs (operate subkind=job_runner)",
                "coexistence_rule": (
                    "JobRunner remains an infrastructure substrate and should wrap work, not define product semantics."
                ),
            },
            {
                "surface": "AgentLoop",
                "category": "conversation_queue",
                "owner": "agent.core.agent_loop",
                "canonical_for": [
                    "queued conversational work items",
                    "owner-facing progress buffering",
                ],
                "not_for": [
                    "durable product execution history",
                    "approval-grade audit logs",
                ],
                "durability": "ephemeral in-memory queue",
                "query_surface": "control-plane jobs (operate subkind=agent_loop)",
                "coexistence_rule": (
                    "AgentLoop can enqueue or reference downstream work, but durable execution belongs in Task or product-job records."
                ),
            },
        ]
        return {
            "source_of_truth": "agent/control/runtime_model.py",
            "status": "explicit_for_current_phase",
            "environment_profiles": [
                {
                    "id": profile.id,
                    "label": profile.label,
                    "description": profile.description,
                    "execution_mode": profile.execution_mode,
                    "workspace_required": profile.workspace_required,
                    "host_read_only": profile.host_read_only,
                    "allow_network": profile.allow_network,
                    "acquisition_allowed": profile.acquisition_allowed,
                }
                for profile in list_environment_profiles()
            ],
            "operating_environment_profiles": [
                {
                    "id": profile.id,
                    "label": profile.label,
                    "description": profile.description,
                    "intended_for": profile.intended_for,
                    "default_environment_profile_ids": list(profile.default_environment_profile_ids),
                    "default_build_execution_policy_id": profile.default_build_execution_policy_id,
                    "default_delivery_policy_id": profile.default_delivery_policy_id,
                    "default_gateway_policy_id": profile.default_gateway_policy_id,
                    "notes": list(profile.notes),
                }
                for profile in list_operating_environment_profiles()
            ],
            "build_execution_policies": [
                {
                    "id": policy.id,
                    "label": policy.label,
                    "description": policy.description,
                    "environment_profile_id": policy.environment_profile_id,
                    "workspace_required": policy.workspace_required,
                    "allowed_sources": list(policy.allowed_sources),
                    "allowed_build_types": [item.value for item in policy.allowed_build_types],
                }
                for policy in list_build_execution_policies()
            ],
            "external_gateway_policies": [
                {
                    "id": policy.id,
                    "label": policy.label,
                    "enabled": policy.enabled,
                    "require_approval": policy.require_approval,
                    "record_cost": policy.record_cost,
                    "allow_network": policy.allow_network,
                    "auth_required": policy.auth_required,
                    "auth_header_name": policy.auth_header_name,
                    "timeout_seconds": policy.timeout_seconds,
                    "max_retries": policy.max_retries,
                    "retry_backoff_seconds": policy.retry_backoff_seconds,
                    "rate_limit_calls": policy.rate_limit_calls,
                    "rate_limit_window_seconds": policy.rate_limit_window_seconds,
                    "allowed_target_kinds": list(policy.allowed_target_kinds),
                    "allowed_url_schemes": list(policy.allowed_url_schemes),
                    "environment_profile_id": policy.environment_profile_id,
                }
                for policy in list_external_gateway_policies()
            ],
            "external_gateway_contracts": [
                {
                    "id": contract.id,
                    "label": contract.label,
                    "description": contract.description,
                    "request_fields": list(contract.request_fields),
                    "response_fields": list(contract.response_fields),
                    "default_policy_id": contract.default_policy_id,
                    "approval_required": contract.approval_required,
                    "record_cost": contract.record_cost,
                    "allow_network": contract.allow_network,
                    "supported_target_kinds": list(contract.supported_target_kinds),
                }
                for contract in list_external_gateway_contracts()
            ],
            "external_capability_providers": [
                {
                    "id": provider.id,
                    "label": provider.label,
                    "description": provider.description,
                    "contract_id": provider.contract_id,
                    "gateway_policy_id": provider.gateway_policy_id,
                    "capability_ids": list(provider.capability_ids),
                    "notes": list(provider.notes),
                }
                for provider in list_external_capability_providers()
            ],
            "external_capability_routes": [
                {
                    "route_id": route.route_id,
                    "provider_id": route.provider_id,
                    "capability_id": route.capability_id,
                    "label": route.label,
                    "description": route.description,
                    "target_kind": route.target_kind,
                    "target_env_var": route.target_env_var,
                    "auth_token_env_var": route.auth_token_env_var,
                    "auth_token_secret_name": route.auth_token_secret_name,
                    "allowed_job_kinds": [item.value for item in route.allowed_job_kinds],
                    "allowed_export_modes": list(route.allowed_export_modes),
                    "gateway_contract_id": route.gateway_contract_id,
                    "gateway_policy_id": route.gateway_policy_id,
                    "estimated_cost_usd": route.estimated_cost_usd,
                    "priority": route.priority,
                    "notes": list(route.notes),
                }
                for route in list_external_capability_routes()
            ],
            "data_handling_rules": [
                {
                    "id": rule.id,
                    "label": rule.label,
                    "description": rule.description,
                    "export_modes": list(rule.export_modes),
                    "redaction_required": rule.redaction_required,
                    "allowed_handoff_targets": list(rule.allowed_handoff_targets),
                    "retention_policy_ids": list(rule.retention_policy_ids),
                    "recoverable": rule.recoverable,
                }
                for rule in list_data_handling_rules()
            ],
            "surfaces": surfaces,
            "global_rules": [
                "External operator-facing execution must converge on BuildJob or ReviewJob.",
                "Task remains the planning/dependency layer, not a substitute for product job state.",
                "JobRunner remains infrastructure-only and should not become a parallel product job model.",
                "AgentLoop remains an ephemeral queue and must hand off durable work to Task or product jobs.",
                "Environment profiles define the safe execution boundary for review, build, acquisition, and export flows.",
                "Operating environment profiles define the higher-level local/operator/enterprise posture over those flow-level execution boundaries.",
                "External capability use must stay behind an approval-gated gateway contract with explicit auth, timeout, retry, rate-limit, cost, and denial recording.",
                "Evidence export modes must follow explicit retention, redaction, and handoff rules before broader enterprise rollout.",
            ],
            "convergence_plan": [
                "Keep BuildJob and ReviewJob as canonical product records.",
                "Treat Task as upstream planning metadata that may link to product jobs.",
                "Treat JobRunner and AgentLoop as supporting operate/runtime surfaces, not competing product models.",
            ],
        }
