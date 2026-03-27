"""
Agent Life Space — Builder Capability Catalog

Honest, explicit capability definitions for the builder product.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.build.models import BuildJobType, VerificationKind


@dataclass(frozen=True)
class BuildCapability:
    """Declared builder capability with explicit scope and defaults."""

    id: str
    build_type: BuildJobType
    label: str
    summary: str
    supported_target_patterns: list[str] = field(default_factory=list)
    verification_defaults: list[VerificationKind] = field(default_factory=list)
    supports_resume: bool = True
    review_after_build_default: bool = True


_CATALOG: dict[BuildJobType, BuildCapability] = {
    BuildJobType.IMPLEMENTATION: BuildCapability(
        id="impl_core",
        build_type=BuildJobType.IMPLEMENTATION,
        label="Implementation",
        summary="General code changes with test/lint/typecheck verification.",
        supported_target_patterns=["*.py", "*.ts", "*.tsx", "*.js", "*.jsx"],
        verification_defaults=[
            VerificationKind.TEST,
            VerificationKind.LINT,
            VerificationKind.TYPECHECK,
        ],
    ),
    BuildJobType.INTEGRATION: BuildCapability(
        id="integration_flow",
        build_type=BuildJobType.INTEGRATION,
        label="Integration",
        summary="Cross-module changes with the same verification loop plus post-build review.",
        supported_target_patterns=["*.py", "*.ts", "*.tsx", "*.yml", "*.yaml"],
        verification_defaults=[
            VerificationKind.TEST,
            VerificationKind.LINT,
            VerificationKind.TYPECHECK,
        ],
    ),
    BuildJobType.DEVOPS: BuildCapability(
        id="devops_safe",
        build_type=BuildJobType.DEVOPS,
        label="DevOps",
        summary="Config/automation changes with deterministic verification when available.",
        supported_target_patterns=["Dockerfile", "*.yml", "*.yaml", "*.sh", "*.tf"],
        verification_defaults=[
            VerificationKind.LINT,
            VerificationKind.TEST,
        ],
    ),
    BuildJobType.TESTING: BuildCapability(
        id="testing_focus",
        build_type=BuildJobType.TESTING,
        label="Testing",
        summary="Test-focused changes with the same workspace and review discipline.",
        supported_target_patterns=["tests/**", "test_*.py", "*.spec.ts", "*.test.ts"],
        verification_defaults=[
            VerificationKind.TEST,
            VerificationKind.LINT,
        ],
    ),
}


def get_capability(build_type: BuildJobType) -> BuildCapability:
    """Resolve the declared capability for a build type."""
    return _CATALOG[build_type]


def list_capabilities() -> list[BuildCapability]:
    """Return all declared builder capabilities."""
    return list(_CATALOG.values())
