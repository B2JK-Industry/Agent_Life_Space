"""
Agent Life Space — LLM Client (API fallback)

Priamy klient pre Anthropic API. Alternativa ku Claude Code CLI.

Aktualne pouzivame CLI (Max subscription). Tento modul je pripraveny
ako fallback ak sa niekedy prejde na API platby.

Pouzitie:
    from agent.core.llm_client import AnthropicClient
    client = AnthropicClient(api_key="sk-...")
    result = await client.chat("Ahoj", model="claude-sonnet-4-6")
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LLMCallResult:
    """Vysledok jedneho LLM volania."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    success: bool = True
    error: str = ""


# Pricing per 1M tokens (March 2026 — update as needed)
_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_1M, output_per_1M)
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD based on token counts."""
    pricing = _PRICING.get(model, (3.0, 15.0))  # default to Sonnet pricing
    input_cost = (input_tokens / 1_000_000) * pricing[0]
    output_cost = (output_tokens / 1_000_000) * pricing[1]
    return round(input_cost + output_cost, 6)


@dataclass
class CumulativeUsage:
    """Kumulativna spotreba od startu."""

    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    errors: int = 0


class AnthropicClient:
    """
    Priamy klient pre Anthropic API.

    Pouzitie:
        client = AnthropicClient()
        result = await client.chat("Ahoj", model="claude-sonnet-4-6")
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            logger.warning("anthropic_api_key_missing",
                           hint="Set ANTHROPIC_API_KEY env var or pass api_key")
        self._client: Any = None
        self.usage = CumulativeUsage()

    def _get_client(self) -> Any:
        """Lazy init Anthropic client."""
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    async def chat(
        self,
        prompt: str,
        *,
        model: str = "claude-sonnet-4-6",
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        timeout: int = 180,
    ) -> LLMCallResult:
        """
        Posli spravu do Anthropic API a vrat odpoved.

        Toto je hlavna metoda — nahradzuje subprocess.run(["claude", ...]).
        """
        start = time.monotonic()

        try:
            client = self._get_client()

            messages = [{"role": "user", "content": prompt}]

            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system

            # Run sync SDK call in thread pool (anthropic SDK is sync by default)
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.messages.create,
                    **kwargs,
                ),
                timeout=timeout,
            )

            latency_ms = int((time.monotonic() - start) * 1000)

            # Extract text from response
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = _estimate_cost(model, input_tokens, output_tokens)

            # Update cumulative usage
            self.usage.total_requests += 1
            self.usage.total_input_tokens += input_tokens
            self.usage.total_output_tokens += output_tokens
            self.usage.total_cost_usd += cost

            logger.info(
                "llm_call",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=round(cost, 4),
                latency_ms=latency_ms,
            )

            return LLMCallResult(
                text=text.strip(),
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
            )

        except TimeoutError:
            latency_ms = int((time.monotonic() - start) * 1000)
            self.usage.errors += 1
            logger.error("llm_timeout", model=model, timeout=timeout)
            return LLMCallResult(
                text="",
                model=model,
                latency_ms=latency_ms,
                success=False,
                error=f"Timeout after {timeout}s",
            )

        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            self.usage.errors += 1
            logger.error("llm_error", model=model, error=str(e))
            return LLMCallResult(
                text="",
                model=model,
                latency_ms=latency_ms,
                success=False,
                error=str(e),
            )

    def get_usage(self) -> dict[str, Any]:
        """Vrat kumulativnu spotrebu."""
        u = self.usage
        return {
            "total_requests": u.total_requests,
            "total_input_tokens": u.total_input_tokens,
            "total_output_tokens": u.total_output_tokens,
            "total_tokens": u.total_input_tokens + u.total_output_tokens,
            "total_cost_usd": round(u.total_cost_usd, 4),
            "errors": u.errors,
            "avg_cost_per_request": round(
                u.total_cost_usd / max(u.total_requests, 1), 4
            ),
        }
