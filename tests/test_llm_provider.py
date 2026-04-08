"""
Tests for LLM Provider abstraction layer.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest

from agent.core.llm_provider import (
    AnthropicProvider,
    ClaudeCliProvider,
    GenerateRequest,
    GenerateResponse,
    OpenAiProvider,
    clear_provider_cache,
    estimate_cost,
    get_provider,
    is_authentication_error,
)


class TestGenerateRequest:
    def test_defaults(self):
        req = GenerateRequest(messages=[{"role": "user", "content": "hi"}])
        assert req.temperature == 0.0
        assert req.max_tokens == 4096
        assert req.timeout == 180
        assert req.max_turns == 1
        assert req.allow_file_access is False
        assert req.tools is None

    def test_custom_values(self):
        req = GenerateRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o",
            temperature=0.7,
            tools=[{"name": "test"}],
            allow_file_access=True,
            cwd="/tmp",
        )
        assert req.model == "gpt-4o"
        assert req.temperature == 0.7
        assert req.tools == [{"name": "test"}]


class TestGenerateResponse:
    def test_success(self):
        resp = GenerateResponse(text="hello", model="test", success=True)
        assert resp.text == "hello"
        assert resp.success

    def test_failure(self):
        resp = GenerateResponse(error="timeout", success=False)
        assert not resp.success
        assert resp.error == "timeout"


class TestEstimateCost:
    def test_sonnet_pricing(self):
        cost = estimate_cost("claude-sonnet-4-6", 1000, 500)
        assert cost > 0
        assert cost < 0.02

    def test_unknown_model_uses_default(self):
        cost = estimate_cost("unknown-model", 1000, 500)
        assert cost > 0


class TestAuthenticationErrorDetection:
    def test_detects_provider_auth_failures(self):
        assert is_authentication_error("Invalid authentication credentials")
        assert is_authentication_error("ANTHROPIC_API_KEY not set")
        assert is_authentication_error("Failed to authenticate. API Error: 401")

    def test_ignores_non_auth_errors(self):
        assert not is_authentication_error("CLI timeout after 30s")


class TestClaudeCliProvider:
    def test_build_prompt_user_only(self):
        req = GenerateRequest(
            messages=[{"role": "user", "content": "hello"}],
        )
        prompt = ClaudeCliProvider._build_prompt(req)
        assert "hello" in prompt

    def test_build_prompt_with_system(self):
        req = GenerateRequest(
            messages=[{"role": "user", "content": "hello"}],
            system="You are helpful",
        )
        prompt = ClaudeCliProvider._build_prompt(req)
        assert "You are helpful" in prompt
        assert "hello" in prompt

    def test_parse_cli_success(self):
        import orjson
        data = orjson.dumps({
            "result": "Hello world",
            "is_error": False,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.001,
        })
        resp = ClaudeCliProvider._parse_cli_response(data.decode(), "test", 100)
        assert resp.success
        assert resp.text == "Hello world"
        assert resp.input_tokens == 100

    def test_parse_cli_error(self):
        import orjson
        data = orjson.dumps({"result": "Error msg", "is_error": True})
        resp = ClaudeCliProvider._parse_cli_response(data.decode(), "test", 100)
        assert not resp.success

    def test_parse_cli_invalid_json(self):
        resp = ClaudeCliProvider._parse_cli_response("not json", "test", 100)
        assert not resp.success

    @pytest.mark.asyncio
    async def test_sandbox_only_downgrades_file_access(self):
        """When SANDBOX_ONLY=1 AND we're in interactive mode (i.e. an
        operator can answer permission prompts), file access is
        downgraded and --dangerously-skip-permissions is NOT added.

        Opus runs in conversational mode in this scenario.
        """
        provider = ClaudeCliProvider()
        with patch.dict(os.environ, {
            "AGENT_SANDBOX_ONLY": "1",
            # Force interactive opt-out so we test the legacy
            # "downgrade and let the operator answer prompts" path.
            "AGENT_CLI_AUTO_APPROVE": "0",
        }):
            with patch("agent.core.llm_provider.subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["claude"],
                    returncode=0,
                    stdout='{"result":"ok","is_error":false,"usage":{"input_tokens":10,"output_tokens":5}}',
                    stderr="",
                )
                req = GenerateRequest(
                    messages=[{"role": "user", "content": "write code"}],
                    allow_file_access=True,
                )
                resp = await provider.generate(req)
                assert resp.success
                called_args = mock_run.call_args.args[0]
                assert "--dangerously-skip-permissions" not in called_args

    @pytest.mark.asyncio
    async def test_headless_mode_auto_approves_with_sandbox_lockdown(self):
        """Regression: when the agent runs as a daemon (no TTY) the
        CLI must add --dangerously-skip-permissions automatically,
        otherwise every tool-use call from the LLM blocks forever
        waiting for an operator who cannot click "allow". Sandbox
        mode is preserved by passing --disallowed-tools so the LLM
        can read but never mutate the host filesystem."""
        provider = ClaudeCliProvider()
        with patch.dict(os.environ, {
            "AGENT_SANDBOX_ONLY": "1",
            "AGENT_CLI_AUTO_APPROVE": "1",  # explicit headless opt-in
        }):
            with patch("agent.core.llm_provider.subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["claude"],
                    returncode=0,
                    stdout='{"result":"ok","is_error":false,"usage":{"input_tokens":10,"output_tokens":5}}',
                    stderr="",
                )
                req = GenerateRequest(
                    messages=[{"role": "user", "content": "kto si?"}],
                    allow_file_access=False,  # default sandbox conversation
                )
                resp = await provider.generate(req)
                assert resp.success
                called_args = mock_run.call_args.args[0]
                # Auto-approve must be enabled.
                assert "--dangerously-skip-permissions" in called_args
                # Sandbox lockdown — destructive tools must be blocked.
                assert "--disallowed-tools" in called_args
                idx = called_args.index("--disallowed-tools")
                blocked = called_args[idx + 1]
                assert "Bash" in blocked
                assert "Edit" in blocked
                assert "Write" in blocked

    @pytest.mark.asyncio
    async def test_explicit_opt_out_disables_auto_approve(self):
        """Operators can hard-disable headless auto-approve via
        AGENT_CLI_AUTO_APPROVE=0 even when stdin is not a TTY."""
        provider = ClaudeCliProvider()
        with patch.dict(os.environ, {
            "AGENT_SANDBOX_ONLY": "1",
            "AGENT_CLI_AUTO_APPROVE": "0",
        }):
            with patch("agent.core.llm_provider.subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["claude"],
                    returncode=0,
                    stdout='{"result":"ok","is_error":false,"usage":{"input_tokens":10,"output_tokens":5}}',
                    stderr="",
                )
                req = GenerateRequest(
                    messages=[{"role": "user", "content": "hi"}],
                    allow_file_access=False,
                )
                await provider.generate(req)
                called_args = mock_run.call_args.args[0]
                assert "--dangerously-skip-permissions" not in called_args
                assert "--disallowed-tools" not in called_args


class TestAnthropicProvider:
    def test_no_api_key_returns_error(self):
        provider = AnthropicProvider(api_key="")
        assert provider._api_key == ""

    @pytest.mark.asyncio
    async def test_missing_key_fails(self):
        provider = AnthropicProvider(api_key="")
        resp = await provider.generate(GenerateRequest(
            messages=[{"role": "user", "content": "hi"}],
        ))
        assert not resp.success
        assert "ANTHROPIC_API_KEY" in resp.error

    def test_supports_tools(self):
        provider = AnthropicProvider(api_key="test")
        assert provider.supports_tools()


class TestOpenAiProvider:
    def test_supports_tools(self):
        provider = OpenAiProvider(api_key="test")
        assert provider.supports_tools()


class TestGetProvider:
    def setup_method(self):
        clear_provider_cache()

    def test_default_is_cli(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_BACKEND", None)
            os.environ.pop("LLM_PROVIDER", None)
            clear_provider_cache()
            provider = get_provider()
            assert isinstance(provider, ClaudeCliProvider)

    def test_api_anthropic(self):
        clear_provider_cache()
        provider = get_provider(backend="api", provider="anthropic")
        assert isinstance(provider, AnthropicProvider)

    def test_api_openai(self):
        clear_provider_cache()
        provider = get_provider(backend="api", provider="openai")
        assert isinstance(provider, OpenAiProvider)

    def test_api_local(self):
        clear_provider_cache()
        provider = get_provider(backend="api", provider="local")
        assert isinstance(provider, OpenAiProvider)

    def test_unknown_backend_raises(self):
        clear_provider_cache()
        with pytest.raises(ValueError, match="Unknown LLM backend"):
            get_provider(backend="magic")

    def test_unknown_provider_raises(self):
        clear_provider_cache()
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_provider(backend="api", provider="magic")

    def test_caching(self):
        clear_provider_cache()
        p1 = get_provider(backend="api", provider="anthropic")
        p2 = get_provider(backend="api", provider="anthropic")
        assert p1 is p2

    def test_cache_distinguishes_different_base_url(self):
        """Two get_provider() calls with different base_url kwargs MUST
        return different instances. Otherwise switching between two
        OpenAI-compatible endpoints (e.g. local vs remote) silently
        keeps the first one."""
        clear_provider_cache()
        p_one = get_provider(
            backend="api", provider="local", base_url="http://one.test"
        )
        p_two = get_provider(
            backend="api", provider="local", base_url="http://two.test"
        )
        assert p_one is not p_two, (
            "Provider cache returned the same instance for two different "
            "base_url values — kwargs must be part of the cache key"
        )

    def test_cache_distinguishes_different_api_key(self):
        """Different api_key kwargs must also produce different instances."""
        clear_provider_cache()
        p_one = get_provider(
            backend="api", provider="anthropic", api_key="sk-one"
        )
        p_two = get_provider(
            backend="api", provider="anthropic", api_key="sk-two"
        )
        assert p_one is not p_two

    def test_cache_returns_same_instance_for_identical_kwargs(self):
        """Sanity: same kwargs still produce the same cached instance."""
        clear_provider_cache()
        p1 = get_provider(
            backend="api", provider="local", base_url="http://one.test"
        )
        p2 = get_provider(
            backend="api", provider="local", base_url="http://one.test"
        )
        assert p1 is p2
