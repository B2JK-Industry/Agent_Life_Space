"""
Architecture invariant tests for cross-domain boundary enforcement (T8-E1-S3).

These tests enforce extraction-grade invariants that prevent accidental coupling
between bounded contexts and ensure service-extraction readiness.

Invariants enforced:
1. Import graph boundaries — bounded contexts only import from allowed modules
2. Execution mode contract — ReviewJob and BuildJob respect declared modes
3. Gateway boundary — external calls must go through the gateway
4. Cross-domain isolation — review cannot mutate build state and vice versa
5. Shared control plane — all bounded contexts use shared primitives
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_AGENT_ROOT = _PROJECT_ROOT / "agent"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _collect_imports(module_dir: Path) -> dict[str, set[str]]:
    """Collect all imports from Python files in a directory.

    Returns {relative_file: set_of_imported_module_roots}.
    """
    result: dict[str, set[str]] = {}
    for py_file in module_dir.rglob("*.py"):
        rel = str(py_file.relative_to(_AGENT_ROOT))
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "agent" and len(parts) > 1:
                        imports.add(parts[1])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("agent."):
                    parts = node.module.split(".")
                    if len(parts) > 1:
                        imports.add(parts[1])
        result[rel] = imports
    return result


# ─────────────────────────────────────────────
# 1. Import graph boundary enforcement
# ─────────────────────────────────────────────

class TestImportGraphBoundaries:
    """Ensure bounded contexts only import from allowed modules."""

    # Allowed imports for each bounded context.
    # review/ may import from: control/ (shared primitives), review/ (self), core/ (identity, paths)
    # build/ may import from: control/ (shared primitives), build/ (self), review/ (post-build review), core/ (identity, paths)
    # control/ may import from: control/ (self), build/models (for BuildJobType), review/models (for ReviewJobType), finance/ (budget checks in intake)
    REVIEW_ALLOWED = {"control", "review", "core"}
    BUILD_ALLOWED = {"control", "build", "review", "core"}
    CONTROL_FORBIDDEN = {"social", "brain", "tasks", "projects"}

    def test_review_context_import_boundaries(self):
        """agent/review/ must not import from build/, social/, brain/, tasks/, finance/, projects/."""
        review_dir = _AGENT_ROOT / "review"
        if not review_dir.exists():
            pytest.skip("No review directory")
        imports = _collect_imports(review_dir)
        violations = []
        for file, imported in imports.items():
            forbidden = imported - self.REVIEW_ALLOWED
            if forbidden:
                violations.append(f"{file} imports: {forbidden}")
        assert not violations, (
            "Review bounded context has forbidden imports:\n"
            + "\n".join(violations)
        )

    def test_build_context_import_boundaries(self):
        """agent/build/ must not import from social/, brain/, tasks/, finance/, projects/."""
        build_dir = _AGENT_ROOT / "build"
        if not build_dir.exists():
            pytest.skip("No build directory")
        imports = _collect_imports(build_dir)
        violations = []
        for file, imported in imports.items():
            forbidden = imported - self.BUILD_ALLOWED
            if forbidden:
                violations.append(f"{file} imports: {forbidden}")
        assert not violations, (
            "Build bounded context has forbidden imports:\n"
            + "\n".join(violations)
        )

    def test_control_plane_does_not_import_social_or_brain(self):
        """agent/control/ must not import from social/, brain/, tasks/, projects/.

        Note: finance/ is allowed (intake.py does budget checks during qualification).
        """
        control_dir = _AGENT_ROOT / "control"
        if not control_dir.exists():
            pytest.skip("No control directory")
        imports = _collect_imports(control_dir)
        violations = []
        for file, imported in imports.items():
            forbidden = imported & self.CONTROL_FORBIDDEN
            if forbidden:
                violations.append(f"{file} imports: {forbidden}")
        assert not violations, (
            "Control plane has forbidden imports:\n"
            + "\n".join(violations)
        )


# ─────────────────────────────────────────────
# 2. Execution mode contract enforcement
# ─────────────────────────────────────────────

class TestExecutionModeContracts:
    """Ensure execution mode declarations are consistent."""

    def test_review_policy_is_read_only(self):
        """ReviewExecutionPolicy enforces read-only access (no workspace mutation)."""
        from agent.control.policy import select_review_execution_policy

        policy = select_review_execution_policy(review_type="repo_audit")
        assert policy.allow_host_read is True
        # Review must NOT have workspace mutation
        assert not hasattr(policy, "allow_workspace_mutation") or not getattr(
            policy, "allow_workspace_mutation", False
        )

    def test_build_policy_is_workspace_bound(self):
        """BuildExecutionPolicy requires workspace for mutation."""
        from agent.control.policy import select_build_execution_policy

        policy = select_build_execution_policy(build_type="implementation")
        assert policy.workspace_required is True
        assert policy.allow_workspace_mutation is True

    def test_execution_modes_enum_is_complete(self):
        """ExecutionMode must include both READ_ONLY_HOST and WORKSPACE_BOUND."""
        from agent.control.models import ExecutionMode

        modes = {mode.value for mode in ExecutionMode}
        assert "read_only_host" in modes
        assert "workspace_bound" in modes


# ─────────────────────────────────────────────
# 3. Gateway boundary enforcement
# ─────────────────────────────────────────────

class TestGatewayBoundary:
    """Ensure external capability calls go through the gateway."""

    def test_no_direct_aiohttp_in_review(self):
        """Review code must not use aiohttp directly — use gateway."""
        review_dir = _AGENT_ROOT / "review"
        if not review_dir.exists():
            pytest.skip("No review directory")
        for py_file in review_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "aiohttp.ClientSession" not in content, (
                f"{py_file.name} uses aiohttp directly; should use gateway"
            )

    def test_no_direct_aiohttp_in_build(self):
        """Build code must not use aiohttp directly — use gateway."""
        build_dir = _AGENT_ROOT / "build"
        if not build_dir.exists():
            pytest.skip("No build directory")
        for py_file in build_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "aiohttp.ClientSession" not in content, (
                f"{py_file.name} uses aiohttp directly; should use gateway"
            )

    def test_gateway_is_only_external_http_surface(self):
        """Only gateway.py, core/, social/, and brain/ should contain aiohttp usage.

        brain/ uses aiohttp for web fetch tooling (tool_router), which is
        a legitimate internal HTTP surface. The gateway boundary applies to
        external *capability* calls (delivery, provider APIs), not all HTTP.
        """
        allowed_dirs = {"control", "core", "social", "brain"}
        for module_dir in _AGENT_ROOT.iterdir():
            if not module_dir.is_dir() or module_dir.name in allowed_dirs:
                continue
            if module_dir.name.startswith("__"):
                continue
            for py_file in module_dir.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8")
                if "aiohttp" in content and "import aiohttp" not in content:
                    continue
                assert "import aiohttp" not in content, (
                    f"{module_dir.name}/{py_file.name} imports aiohttp; "
                    "only control/, core/, social/, and brain/ may use HTTP"
                )


# ─────────────────────────────────────────────
# 4. Cross-domain isolation
# ─────────────────────────────────────────────

class TestCrossDomainIsolation:
    """Ensure review and build don't access each other's storage."""

    def test_review_does_not_import_build_storage(self):
        """Review code must not import BuildStorage."""
        review_dir = _AGENT_ROOT / "review"
        if not review_dir.exists():
            pytest.skip("No review directory")
        for py_file in review_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "BuildStorage" not in content, (
                f"{py_file.name} references BuildStorage — cross-domain violation"
            )

    def test_build_does_not_import_review_storage(self):
        """Build code must not import ReviewStorage (except through service for post-build review)."""
        build_dir = _AGENT_ROOT / "build"
        if not build_dir.exists():
            pytest.skip("No build directory")
        for py_file in build_dir.rglob("*.py"):
            if py_file.name == "service.py":
                continue  # service.py may call ReviewService for post-build review
            content = py_file.read_text(encoding="utf-8")
            assert "ReviewStorage" not in content, (
                f"{py_file.name} references ReviewStorage — cross-domain violation"
            )

    def test_review_does_not_import_build_service(self):
        """Review code must not import BuildService."""
        review_dir = _AGENT_ROOT / "review"
        if not review_dir.exists():
            pytest.skip("No review directory")
        for py_file in review_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "BuildService" not in content, (
                f"{py_file.name} references BuildService — cross-domain violation"
            )


# ─────────────────────────────────────────────
# 5. Shared control plane contract enforcement
# ─────────────────────────────────────────────

class TestSharedControlPlaneContracts:
    """Ensure bounded contexts use shared primitives from control/."""

    def test_jobkind_enum_covers_all_domains(self):
        """JobKind must cover review, build, operate, delivery."""
        from agent.control.models import JobKind

        required = {"review", "build", "operate", "delivery"}
        actual = {kind.value for kind in JobKind}
        assert required <= actual, f"Missing JobKind values: {required - actual}"

    def test_artifact_kind_covers_review_and_build(self):
        """ArtifactKind must cover both review and build artifacts."""
        from agent.control.models import ArtifactKind

        review_kinds = {
            "review_report", "finding_list", "diff_analysis",
            "security_report", "executive_summary",
        }
        build_kinds = {
            "patch", "diff", "verification_report",
            "acceptance_report", "delivery_bundle",
        }
        actual = {kind.value for kind in ArtifactKind}
        missing_review = review_kinds - actual
        missing_build = build_kinds - actual
        assert not missing_review, f"Missing review artifact kinds: {missing_review}"
        assert not missing_build, f"Missing build artifact kinds: {missing_build}"

    def test_trace_record_kinds_are_comprehensive(self):
        """TraceRecordKind must cover all control plane domains."""
        from agent.control.models import TraceRecordKind

        required = {
            "qualification", "budget", "capability", "delivery",
            "review_policy", "execution", "gateway", "quality",
            "release", "cost_accuracy", "telemetry",
        }
        actual = {kind.value for kind in TraceRecordKind}
        missing = required - actual
        assert not missing, f"Missing TraceRecordKind values: {missing}"

    def test_no_parallel_job_models_outside_control(self):
        """No bounded context should redefine JobKind.

        Note: core/job_runner.py has its own infrastructure-level JobStatus
        which is a separate concern from the product-level JobStatus in
        control/models.py. The invariant here is that *product-facing*
        bounded contexts (review/, build/) don't define parallel models.
        """
        product_contexts = {"review", "build"}
        for module_name in product_contexts:
            module_dir = _AGENT_ROOT / module_name
            if not module_dir.exists():
                continue
            for py_file in module_dir.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8")
                lines = content.split("\n")
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("class JobKind") and "Enum" in stripped:
                        raise AssertionError(
                            f"{module_name}/{py_file.name} defines a parallel "
                            "JobKind enum — use agent.control.models.JobKind"
                        )


# ─────────────────────────────────────────────
# 6. Gateway multi-provider contract
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# 6. Deployment safety invariants (converted from CI shell checks)
# ─────────────────────────────────────────────

class TestDeploymentSafetyInvariants:
    """Invariants previously enforced by shell grep in CI.

    These ensure no deployment-unsafe patterns creep into production code.
    """

    def test_no_duplicate_persona_definitions(self):
        """Persona prompts must come from persona.py only."""
        persona_marker = "Som John"
        for py_file in _AGENT_ROOT.rglob("*.py"):
            if py_file.name == "persona.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(content.split("\n"), start=1):
                if "# noqa" in line:
                    continue
                assert persona_marker not in line, (
                    f"{py_file.relative_to(_AGENT_ROOT)}:{i} contains persona definition — "
                    "prompts must come from agent/core/persona.py"
                )

    def test_no_hardcoded_agent_paths(self):
        """No hardcoded ~/agent-life-space paths in production code."""
        marker = "~/agent-life-space"
        for py_file in _AGENT_ROOT.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(content.split("\n"), start=1):
                assert marker not in line, (
                    f"{py_file.relative_to(_AGENT_ROOT)}:{i} has hardcoded path '{marker}' — "
                    "use agent.core.paths instead"
                )

    def test_centralized_path_resolver(self):
        """Path.home() / 'agent-life-space' only allowed in paths.py."""
        pattern = "agent-life-space"
        for py_file in _AGENT_ROOT.rglob("*.py"):
            if py_file.name == "paths.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(content.split("\n"), start=1):
                if "Path.home()" in line and pattern in line:
                    raise AssertionError(
                        f"{py_file.relative_to(_AGENT_ROOT)}:{i} uses Path.home() with "
                        f"'{pattern}' — must use agent.core.paths centralized resolver"
                    )

    def test_sandbox_default_safe(self):
        """AGENT_SANDBOX_ONLY default must be '1' (safe)."""
        llm_provider = _AGENT_ROOT / "core" / "llm_provider.py"
        if not llm_provider.exists():
            pytest.skip("llm_provider.py not found")
        content = llm_provider.read_text(encoding="utf-8")
        assert 'AGENT_SANDBOX_ONLY' in content, "AGENT_SANDBOX_ONLY not found in llm_provider.py"
        # Find the line with the default and verify it's "1"
        for line in content.split("\n"):
            if "AGENT_SANDBOX_ONLY" in line and '"1"' in line:
                return
        raise AssertionError(
            "AGENT_SANDBOX_ONLY default is not '1' — sandbox must be enabled by default"
        )


# ─────────────────────────────────────────────
# 7. Gateway multi-provider contract
# ─────────────────────────────────────────────

class TestMultiProviderContract:
    """Ensure multi-provider gateway infrastructure is consistent."""

    def test_seller_capabilities_in_obolos_provider(self):
        """Obolos provider declares seller_publish_v1 and wallet_topup_v1."""
        from agent.control.policy import get_external_capability_provider

        provider = get_external_capability_provider("obolos.tech")
        assert "seller_publish_v1" in provider.capability_ids
        assert "wallet_topup_v1" in provider.capability_ids

    def test_seller_routes_exist(self):
        """Seller-side routes are configured."""
        from agent.control.policy import get_external_capability_route

        publish = get_external_capability_route("obolos_seller_publish_primary")
        assert publish is not None
        assert publish.capability_id == "seller_publish_v1"
        assert publish.request_mode == "obolos_seller_publish_v1"

        topup = get_external_capability_route("obolos_wallet_topup_primary")
        assert topup is not None
        assert topup.capability_id == "wallet_topup_v1"
        assert topup.request_mode == "obolos_wallet_topup_v1"

    def test_list_providers_for_capability(self):
        """list_providers_for_capability returns providers that have the capability."""
        from agent.control.policy import list_providers_for_capability

        providers = list_providers_for_capability("marketplace_catalog_v1")
        assert len(providers) >= 1
        assert any(p.id == "obolos.tech" for p in providers)

    def test_list_providers_for_unknown_capability(self):
        """Unknown capability returns empty list."""
        from agent.control.policy import list_providers_for_capability

        providers = list_providers_for_capability("nonexistent_v99")
        assert providers == []

    def test_resolve_capability_across_providers(self):
        """resolve_capability_across_providers returns routes from all providers."""
        from agent.control.policy import resolve_capability_across_providers

        routes = resolve_capability_across_providers(
            capability_id="marketplace_catalog_v1",
            job_kind="operate",
        )
        assert len(routes) >= 1
        assert routes[0].capability_id == "marketplace_catalog_v1"

    def test_capability_map_in_catalog(self):
        """Catalog includes capability-to-providers mapping."""
        from agent.control.gateway import ExternalGatewayService

        svc = ExternalGatewayService()
        catalog = svc.describe_capability_catalog()
        cap_map = catalog.get("capability_map", {})
        assert isinstance(cap_map, dict)
        assert "marketplace_catalog_v1" in cap_map
        assert "obolos.tech" in cap_map["marketplace_catalog_v1"]
        # Seller capabilities
        assert "seller_publish_v1" in cap_map
        assert "wallet_topup_v1" in cap_map
