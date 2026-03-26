"""
Tests pre agent/core/llm_client.py — Anthropic API fallback klient.

Pokrýva:
    - Cost estimation (_estimate_cost)
    - CumulativeUsage tracking
    - AnthropicClient.chat() — success, timeout, error
    - AnthropicClient.get_usage() — formát výstupu
    - LLMCallResult dataclass
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.core.llm_client import (
    AnthropicClient,
    CumulativeUsage,
    LLMCallResult,
    _estimate_cost,
)

# --- _estimate_cost ---


class TestEstimateCost:
    def test_opus_pricing(self):
        cost = _estimate_cost("claude-opus-4-6", 1_000_000, 1_000_000)
        assert cost == 90.0  # 15 + 75

    def test_sonnet_pricing(self):
        cost = _estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost == 18.0  # 3 + 15

    def test_haiku_pricing(self):
        cost = _estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        assert cost == 4.8  # 0.8 + 4.0

    def test_unknown_model_uses_sonnet_default(self):
        cost = _estimate_cost("unknown-model", 1_000_000, 1_000_000)
        assert cost == 18.0  # defaults to Sonnet

    def test_zero_tokens(self):
        cost = _estimate_cost("claude-sonnet-4-6", 0, 0)
        assert cost == 0.0

    def test_small_request(self):
        # 1000 input, 500 output on Haiku
        cost = _estimate_cost("claude-haiku-4-5-20251001", 1000, 500)
        assert cost == pytest.approx(0.0028, abs=0.001)

    def test_cost_is_rounded(self):
        cost = _estimate_cost("claude-sonnet-4-6", 333, 777)
        # Should be rounded to 6 decimal places
        assert isinstance(cost, float)
        assert len(str(cost).split(".")[-1]) <= 6


# --- LLMCallResult ---


class TestLLMCallResult:
    def test_success_result(self):
        r = LLMCallResult(
            text="Ahoj",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            latency_ms=500,
        )
        assert r.success is True
        assert r.text == "Ahoj"
        assert r.error == ""

    def test_error_result(self):
        r = LLMCallResult(
            text="",
            model="claude-sonnet-4-6",
            success=False,
            error="Timeout after 30s",
        )
        assert r.success is False
        assert r.error == "Timeout after 30s"

    def test_defaults(self):
        r = LLMCallResult(text="x", model="m")
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.cost_usd == 0.0
        assert r.latency_ms == 0


# --- CumulativeUsage ---


class TestCumulativeUsage:
    def test_defaults(self):
        u = CumulativeUsage()
        assert u.total_requests == 0
        assert u.total_cost_usd == 0.0
        assert u.errors == 0


# --- AnthropicClient ---


class TestAnthropicClient:
    def test_init_no_key_warns(self):
        """Client should not crash without API key."""
        with patch.dict("os.environ", {}, clear=True):
            client = AnthropicClient(api_key="")
            assert client._api_key == ""

    def test_init_with_key(self):
        client = AnthropicClient(api_key="sk-test-123")
        assert client._api_key == "sk-test-123"

    def test_get_usage_format(self):
        client = AnthropicClient(api_key="test")
        usage = client.get_usage()
        assert "total_requests" in usage
        assert "total_input_tokens" in usage
        assert "total_output_tokens" in usage
        assert "total_tokens" in usage
        assert "total_cost_usd" in usage
        assert "errors" in usage
        assert "avg_cost_per_request" in usage

    def test_get_usage_initial_values(self):
        client = AnthropicClient(api_key="test")
        usage = client.get_usage()
        assert usage["total_requests"] == 0
        assert usage["total_cost_usd"] == 0.0
        assert usage["avg_cost_per_request"] == 0.0

    @pytest.mark.asyncio
    async def test_chat_success(self):
        """Mock successful API call."""
        client = AnthropicClient(api_key="test")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Odpoveď od Clauda")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_response.model = "claude-sonnet-4-6"

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = mock_response
        client._client = mock_anthropic

        result = await client.chat("Ahoj", model="claude-sonnet-4-6")

        assert result.success is True
        assert result.text == "Odpoveď od Clauda"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.cost_usd > 0
        assert result.latency_ms >= 0

        # Usage should be updated
        assert client.usage.total_requests == 1
        assert client.usage.total_input_tokens == 100

    @pytest.mark.asyncio
    async def test_chat_timeout(self):
        """Timeout should return error result."""
        client = AnthropicClient(api_key="test")

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.side_effect = TimeoutError("slow")
        client._client = mock_anthropic

        result = await client.chat("test", timeout=1)

        assert result.success is False
        assert "Timeout" in result.error or "slow" in result.error
        assert client.usage.errors == 1

    @pytest.mark.asyncio
    async def test_chat_api_error(self):
        """API error should return error result."""
        client = AnthropicClient(api_key="test")

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.side_effect = RuntimeError("API down")
        client._client = mock_anthropic

        result = await client.chat("test")

        assert result.success is False
        assert "API down" in result.error
        assert client.usage.errors == 1

    @pytest.mark.asyncio
    async def test_cumulative_usage_tracks(self):
        """Multiple calls should accumulate usage."""
        client = AnthropicClient(api_key="test")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="OK")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_response.model = "claude-sonnet-4-6"

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = mock_response
        client._client = mock_anthropic

        await client.chat("1")
        await client.chat("2")
        await client.chat("3")

        assert client.usage.total_requests == 3
        assert client.usage.total_input_tokens == 300
        assert client.usage.total_output_tokens == 150
