"""
Tests for LLM Provider abstraction layer.
"""

from __future__ import annotations

import os
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
    async def test_sandbox_only_blocks_file_access(self):
        provider = ClaudeCliProvider()
        with patch.dict(os.environ, {"AGENT_SANDBOX_ONLY": "1"}):
            req = GenerateRequest(
                messages=[{"role": "user", "content": "write code"}],
                allow_file_access=True,
            )
            resp = await provider.generate(req)
            assert not resp.success
            assert "AGENT_SANDBOX_ONLY" in resp.error


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
