"""
Tests for persistent runtime LLM controls.
"""

from __future__ import annotations

from pathlib import Path

from agent.control.llm_runtime import (
    LlmRuntimeControlService,
    load_llm_runtime_state,
    resolve_llm_runtime_state,
)
from agent.core.llm_provider import clear_provider_cache, get_provider
from agent.core.models import get_model


class TestLlmRuntimeControlService:
    def test_defaults_follow_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "agent"))
        monkeypatch.setenv("LLM_BACKEND", "cli")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")

        summary = resolve_llm_runtime_state()

        assert summary["enabled"] is True
        assert summary["effective_backend"] == "cli"
        assert summary["effective_provider"] == "anthropic"
        assert summary["follows_env"] is True

    def test_update_persists_backend_and_provider_override(self, tmp_path):
        service = LlmRuntimeControlService(data_dir=tmp_path / "agent")

        updated = service.update_state(
            enabled=True,
            backend="api",
            provider="openai",
            note="switch for operator test",
            updated_by="pytest",
        )

        stored = load_llm_runtime_state(tmp_path / "agent")
        assert updated["effective_backend"] == "api"
        assert updated["effective_provider"] == "openai"
        assert updated["follows_env"] is False
        assert stored.backend_override == "api"
        assert stored.provider_override == "openai"
        assert stored.updated_by == "pytest"
        assert Path(updated["state_path"]).exists()

    async def test_detach_forces_provider_failure(self, monkeypatch, tmp_path):
        data_dir = tmp_path / "agent"
        monkeypatch.setenv("AGENT_DATA_DIR", str(data_dir))
        service = LlmRuntimeControlService(data_dir=data_dir)
        service.update_state(enabled=False, updated_by="pytest", note="detach")
        clear_provider_cache()

        provider = get_provider()
        from agent.core.llm_provider import GenerateRequest

        response = await provider.generate(GenerateRequest(
            messages=[{"role": "user", "content": "hi"}],
        ))

        assert response.success is False
        assert "detached by operator" in response.error

    def test_model_resolution_uses_runtime_api_override(self, monkeypatch, tmp_path):
        data_dir = tmp_path / "agent"
        monkeypatch.setenv("AGENT_DATA_DIR", str(data_dir))
        monkeypatch.setenv("LLM_BACKEND", "cli")
        service = LlmRuntimeControlService(data_dir=data_dir)
        service.update_state(enabled=True, backend="api", provider="openai", updated_by="pytest")

        model = get_model("programming")

        assert model.model_id == "o3"


class TestBrainHonoursRuntimeBackendOverride:
    """Regression: AgentBrain used to read ``os.environ['LLM_BACKEND']``
    directly when deciding whether to take the api/tool-loop branch or
    the cli/generate branch. That meant /api/operator/llm could flip
    the provider but the brain still ran the env-default execution
    path. Verify the brain now consults the resolver."""

    def test_brain_module_does_not_read_llm_backend_env_directly(self):
        """Static guard: ``os.environ.get("LLM_BACKEND"`` must not appear
        in brain.py. The bug was that the tool-loop branch decision
        ignored the operator override entirely."""
        from pathlib import Path

        import agent.core.brain as brain_module

        source = Path(brain_module.__file__).read_text()
        assert 'os.environ.get("LLM_BACKEND"' not in source, (
            "agent/core/brain.py must not read LLM_BACKEND from env "
            "directly — use resolve_llm_runtime_state() so operator "
            "overrides are honoured."
        )
        assert "resolve_llm_runtime_state" in source, (
            "agent/core/brain.py must import resolve_llm_runtime_state "
            "to pick the effective backend."
        )

    def test_resolver_returns_api_when_override_set(self, monkeypatch, tmp_path):
        """End-to-end check on the resolver that brain.py now uses:
        env says cli, operator override says api/openai → resolver
        must return api so the brain takes the tool-loop branch."""
        data_dir = tmp_path / "agent"
        monkeypatch.setenv("AGENT_DATA_DIR", str(data_dir))
        monkeypatch.setenv("LLM_BACKEND", "cli")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")

        service = LlmRuntimeControlService(data_dir=data_dir)
        service.update_state(
            enabled=True,
            backend="api",
            provider="openai",
            updated_by="pytest",
        )

        # This is exactly what brain.py now does on every LLM call.
        summary = resolve_llm_runtime_state(environ={
            "LLM_BACKEND": "cli",
            "LLM_PROVIDER": "anthropic",
            "AGENT_DATA_DIR": str(data_dir),
        })
        assert summary["effective_backend"] == "api"
        assert summary["effective_provider"] == "openai"
