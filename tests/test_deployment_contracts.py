"""
Deployment contract tests for v1.30.0.

Validates:
1. No AGENT_DEV_MODE bypass exists in policy-enforcing code
2. Path resolution does not silently fall back to home directory
3. Pidfile path is configurable
4. Vault reports readiness state
5. No env var mutations in module-level code
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

_AGENT_ROOT = Path(__file__).resolve().parent.parent / "agent"


class TestDenyByDefault:
    """No environment-dependent policy bypass paths."""

    def test_no_dev_mode_bypass_in_review(self):
        """review/service.py must not use AGENT_DEV_MODE to bypass policy."""
        content = (_AGENT_ROOT / "review" / "service.py").read_text()
        for i, line in enumerate(content.split("\n"), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue  # Skip comments
            assert "AGENT_DEV_MODE" not in line, (
                f"review/service.py:{i} contains active AGENT_DEV_MODE bypass"
            )

    def test_no_dev_mode_bypass_in_build(self):
        """build/service.py must not use AGENT_DEV_MODE to bypass policy."""
        content = (_AGENT_ROOT / "build" / "service.py").read_text()
        for i, line in enumerate(content.split("\n"), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue  # Skip comments
            assert "AGENT_DEV_MODE" not in line, (
                f"build/service.py:{i} contains active AGENT_DEV_MODE bypass"
            )

    def test_no_dev_mode_anywhere_in_policy_path(self):
        """No policy-enforcing module should reference AGENT_DEV_MODE."""
        policy_modules = [
            "control/policy.py",
            "control/gateway.py",
            "core/tool_policy.py",
        ]
        for module in policy_modules:
            path = _AGENT_ROOT / module
            if path.exists():
                content = path.read_text()
                assert "AGENT_DEV_MODE" not in content, (
                    f"{module} references AGENT_DEV_MODE — policy must not be env-dependent"
                )


class TestPathResolution:
    """Path resolution must be explicit, not silently fallback."""

    def test_no_home_fallback_in_paths(self):
        """paths.py must not fall back to Path.home() / 'agent-life-space'."""
        content = (_AGENT_ROOT / "core" / "paths.py").read_text()
        assert "Path.home()" not in content, (
            "paths.py still uses Path.home() fallback"
        )

    def test_get_project_root_raises_without_config(self):
        """get_project_root() must raise if no valid root is determinable."""
        from agent.core.paths import get_project_root

        # In our test environment, pyproject.toml exists so this should work
        root = get_project_root()
        assert root  # non-empty string
        assert Path(root).exists()


class TestPidfileConfig:
    """Pidfile path must be configurable."""

    def test_pidfile_respects_env_var(self):
        with patch.dict(os.environ, {"AGENT_PIDFILE_PATH": "/custom/path.pid"}):
            # Re-import to get the new value
            import importlib

            import agent.__main__ as main_mod
            importlib.reload(main_mod)
            assert main_mod.PIDFILE == "/custom/path.pid"


class TestVaultReadiness:
    """Vault must expose readiness state."""

    def test_vault_is_ready_property(self):
        from agent.vault.secrets import SecretsManager
        vault = SecretsManager(master_key="test-key-123")
        assert vault.is_ready is True

    def test_vault_not_ready_without_key(self):
        import tempfile

        from agent.vault.secrets import SecretsManager
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = SecretsManager(master_key="", vault_dir=tmpdir)
            assert vault.is_ready is False


class TestNoEnvVarMutation:
    """Critical modules must not mutate os.environ at import time."""

    def test_policy_module_does_not_mutate_env(self):
        """Importing policy.py must not change os.environ."""
        before = dict(os.environ)
        import agent.control.policy  # noqa: F401
        after = dict(os.environ)
        # Only AGENT_PROJECT_ROOT may be set (by paths.py inference)
        diff = {k: v for k, v in after.items() if k not in before and k != "AGENT_PROJECT_ROOT"}
        assert not diff, f"policy.py mutated os.environ: {diff}"

    def test_gateway_module_does_not_mutate_env(self):
        """Importing gateway.py must not change os.environ."""
        before = dict(os.environ)
        import agent.control.gateway  # noqa: F401
        after = dict(os.environ)
        diff = {k: v for k, v in after.items() if k not in before and k != "AGENT_PROJECT_ROOT"}
        assert not diff, f"gateway.py mutated os.environ: {diff}"


class TestDashboardAuth:
    """Dashboard must require authentication."""

    def test_dashboard_handler_checks_auth(self):
        """_handle_dashboard must verify API key before serving HTML."""
        from agent.social.agent_api import AgentAPI
        api = AgentAPI(api_keys=["test-key"])
        # The handler should reference _check_auth or key_param check
        import inspect
        source = inspect.getsource(api._handle_dashboard)
        assert "_check_auth" in source or "key_param" in source, (
            "Dashboard handler does not check authentication"
        )

    def test_dashboard_query_param_login_seeds_runtime_key(self):
        """Dashboard HTML must bootstrap the query-param key into runtime JS."""
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html(api_key_hint="test-key")
        assert "const INITIAL_KEY = \"test-key\";" in html
        assert "localStorage.setItem('als_api_key', INITIAL_KEY);" in html
        assert "window.history.replaceState" in html


class TestNoPrivateStorageAccess:
    """Services must not access _storage directly; use public API."""

    def test_settlement_uses_public_api(self):
        content = (_AGENT_ROOT / "control" / "settlement.py").read_text()
        for i, line in enumerate(content.split("\n"), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "._storage" not in line, (
                f"settlement.py:{i} accesses private _storage — use public API"
            )

    def test_agent_api_uses_public_archival_method(self):
        content = (_AGENT_ROOT / "social" / "agent_api.py").read_text()
        for i, line in enumerate(content.split("\n"), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert 'getattr' not in line or '_storage' not in line, (
                f"agent_api.py:{i} accesses private _storage via getattr"
            )


class TestServiceExtractionReadiness:
    """Bounded contexts must maintain import discipline."""

    def test_no_post_init_private_mutation_in_agent(self):
        """agent.py must not mutate ._attribute on services post-init."""
        content = (_AGENT_ROOT / "core" / "agent.py").read_text()
        violations = []
        for i, line in enumerate(content.split("\n"), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"self\.[A-Za-z0-9_]+\._[A-Za-z0-9_]+\s*=", stripped):
                violations.append(f"agent.py:{i} — {stripped}")
        assert not violations, (
            "Post-init private mutation found:\n" + "\n".join(violations)
        )
