"""
Agent Life Space — Runtime Model

Explicit coexistence rules for the wider runtime model.
Makes the relationship between product jobs, planning tasks,
infrastructure jobs, and conversational loop items visible.
"""

from __future__ import annotations

from agent.control.policy import list_environment_profiles


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
            "surfaces": surfaces,
            "global_rules": [
                "External operator-facing execution must converge on BuildJob or ReviewJob.",
                "Task remains the planning/dependency layer, not a substitute for product job state.",
                "JobRunner remains infrastructure-only and should not become a parallel product job model.",
                "AgentLoop remains an ephemeral queue and must hand off durable work to Task or product jobs.",
                "Environment profiles define the safe execution boundary for review, build, acquisition, and export flows.",
            ],
            "convergence_plan": [
                "Keep BuildJob and ReviewJob as canonical product records.",
                "Treat Task as upstream planning metadata that may link to product jobs.",
                "Treat JobRunner and AgentLoop as supporting operate/runtime surfaces, not competing product models.",
            ],
        }
