"""
Security invariant tests.

These tests enforce the security model defined in docs/SECURITY_MODEL.md.
If any of these fail, the security boundary has been violated.
"""

from __future__ import annotations

from pathlib import Path

AGENT_DIR = Path(__file__).parent.parent / "agent"


class TestSandboxDefault:
    """Host file access must be blocked by default."""

    def test_sandbox_only_defaults_to_one(self):
        """AGENT_SANDBOX_ONLY must default to '1' in llm_provider.py."""
        code = (AGENT_DIR / "core" / "llm_provider.py").read_text()
        # Find the AGENT_SANDBOX_ONLY default value
        assert 'AGENT_SANDBOX_ONLY", "1"' in code, (
            "AGENT_SANDBOX_ONLY must default to '1' (sandbox-only mode)"
        )

    def test_skip_permissions_is_guarded(self):
        """--dangerously-skip-permissions must be behind a sandbox check."""
        code = (AGENT_DIR / "core" / "llm_provider.py").read_text()
        # The flag must appear inside a conditional block (if allow_file_access)
        # and there must be a sandbox_only check before it
        assert "AGENT_SANDBOX_ONLY" in code, (
            "llm_provider.py must check AGENT_SANDBOX_ONLY before allowing skip-permissions"
        )
        assert 'sandbox_only != "0"' in code or 'sandbox_only == "1"' in code, (
            "Skip-permissions must be blocked unless AGENT_SANDBOX_ONLY=0"
        )


class TestToolPolicyEnforcement:
    """All sensitive tools must be in the capability manifest."""

    def test_all_executor_tools_in_manifest(self):
        """Every tool handler in ToolExecutor must have a manifest entry."""
        from unittest.mock import MagicMock

        from agent.core.tool_executor import ToolExecutor
        from agent.core.tool_policy import TOOL_CAPABILITIES

        agent = MagicMock()
        executor = ToolExecutor(agent=agent)

        for tool_name in executor._handlers:
            assert tool_name in TOOL_CAPABILITIES, (
                f"Tool '{tool_name}' has no capability manifest entry"
            )

    def test_high_risk_tools_are_owner_only(self):
        """HIGH risk tools must be owner-only."""
        from agent.core.tool_policy import TOOL_CAPABILITIES, ToolRiskLevel

        for name, cap in TOOL_CAPABILITIES.items():
            if cap.risk_level == ToolRiskLevel.HIGH:
                assert cap.owner_only, (
                    f"HIGH risk tool '{name}' must be owner_only"
                )
                assert cap.safe_mode_blocked, (
                    f"HIGH risk tool '{name}' must be safe_mode_blocked"
                )

    def test_external_side_effects_are_owner_only(self):
        """Tools with EXTERNAL side effects must be owner-only."""
        from agent.core.tool_policy import TOOL_CAPABILITIES, SideEffectClass

        for name, cap in TOOL_CAPABILITIES.items():
            if cap.side_effect == SideEffectClass.EXTERNAL:
                assert cap.owner_only, (
                    f"Tool '{name}' has EXTERNAL side effects but is not owner_only"
                )

    def test_read_only_tools_have_no_side_effects(self):
        """Read-only tools must declare NONE side effects."""
        from agent.core.tool_policy import TOOL_CAPABILITIES, SideEffectClass

        read_only = ["query_memory", "list_tasks", "check_health", "get_status", "search_knowledge"]
        for name in read_only:
            cap = TOOL_CAPABILITIES[name]
            assert cap.side_effect == SideEffectClass.NONE, (
                f"Read-only tool '{name}' has side_effect={cap.side_effect.value}"
            )


class TestPersonaCentralization:
    """Agent identity must come from one source."""

    def test_no_duplicate_persona_in_brain(self):
        """brain.py must not define its own prompts."""
        code = (AGENT_DIR / "core" / "brain.py").read_text()
        assert "Som John. Autonómna bytosť" not in code, (
            "brain.py must import prompts from persona.py, not define them inline"
        )

    def test_persona_module_exists(self):
        """persona.py must exist and contain SYSTEM_PROMPT."""
        persona_path = AGENT_DIR / "core" / "persona.py"
        assert persona_path.exists(), "agent/core/persona.py must exist"
        code = persona_path.read_text()
        assert "SYSTEM_PROMPT" in code
        assert "AGENT_PROMPT" in code
        assert "SIMPLE_PROMPT" in code


class TestSecurityModelDocument:
    """Security model document must exist and be current."""

    def test_security_model_exists(self):
        doc = Path(__file__).parent.parent / "docs" / "SECURITY_MODEL.md"
        assert doc.exists(), "docs/SECURITY_MODEL.md must exist"

    def test_security_model_covers_key_sections(self):
        doc = Path(__file__).parent.parent / "docs" / "SECURITY_MODEL.md"
        content = doc.read_text()
        required_sections = [
            "Execution Boundaries",
            "Tool Policy",
            "Memory Security",
            "Vault",
            "Finance",
            "NIKDY nesmie robiť",
            "Známe limity",
        ]
        for section in required_sections:
            assert section in content, (
                f"SECURITY_MODEL.md must contain section: {section}"
            )


class TestNoHardcodedPaths:
    """No hardcoded paths to agent directory."""

    def test_no_hardcoded_agent_home(self):
        """No ~/agent-life-space hardcoded paths in Python files."""
        for py_file in AGENT_DIR.rglob("*.py"):
            code = py_file.read_text()
            assert "~/agent-life-space" not in code, (
                f"{py_file.relative_to(AGENT_DIR.parent)} contains hardcoded ~/agent-life-space"
            )
