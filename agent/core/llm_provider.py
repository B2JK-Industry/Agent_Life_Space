"""
Agent Life Space — LLM Provider Abstraction

Provider-agnostic interface pre LLM volania.
Jedno rozhranie, viacero backendov:

    1. ClaudeCliProvider  — Claude Code CLI (subprocess, Max subscription)
    2. AnthropicProvider  — Anthropic API (priamy SDK)
    3. OpenAiProvider     — OpenAI-compatible API (GPT, local models, Ollama)

Použitie:
    provider = get_provider()  # respects LLM_BACKEND + LLM_PROVIDER env vars
    response = await provider.generate(GenerateRequest(
        messages=[{"role": "user", "content": "Ahoj"}],
        model="claude-sonnet-4-6",
    ))

Konfigurácia cez env vars:
    LLM_BACKEND=cli|api        (default: cli)
    LLM_PROVIDER=anthropic|openai|local  (default: anthropic)
    ANTHROPIC_API_KEY=sk-...   (pre anthropic API)
    OPENAI_API_KEY=sk-...      (pre openai API)
    OPENAI_BASE_URL=http://... (pre local/custom endpoint)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import orjson
import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────
# Request / Response dataclasses
# ─────────────────────────────────────────────

@dataclass
class GenerateRequest:
    """Provider-agnostic LLM request."""

    messages: list[dict[str, str]]
    system: str = ""
    model: str = ""  # provider-specific model ID, resolved by get_provider if empty
    max_tokens: int = 4096
    temperature: float = 0.0
    tools: list[dict[str, Any]] | None = None  # function calling definitions
    json_mode: bool = False
    timeout: int = 180

    # CLI-specific (no-op for API providers)
    max_turns: int = 1
    allow_file_access: bool = False
    cwd: str = ""


@dataclass
class GenerateResponse:
    """Provider-agnostic LLM response."""

    text: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    success: bool = True
    error: str = ""
    raw: Any = None  # provider-specific raw response


# ─────────────────────────────────────────────
# Pricing (March 2026)
# ─────────────────────────────────────────────

_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_1M, output_per_1M)
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "o3": (10.0, 40.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD."""
    pricing = _PRICING.get(model, (3.0, 15.0))
    return round(
        (input_tokens / 1_000_000) * pricing[0]
        + (output_tokens / 1_000_000) * pricing[1],
        6,
    )


# ─────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────

class LLMProvider(ABC):
    """Abstract LLM provider. All backends implement this."""

    @abstractmethod
    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Generate a response. Must be async."""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def supports_tools(self) -> bool:
        """Does this provider support function calling?"""
        return False

    def supports_streaming(self) -> bool:
        """Does this provider support streaming?"""
        return False


# ─────────────────────────────────────────────
# 1. Claude CLI Provider
# ─────────────────────────────────────────────

class ClaudeCliProvider(LLMProvider):
    """
    Claude Code CLI subprocess backend.
    Uses Max subscription (no API cost).
    Supports file access and multi-turn tool use.
    """

    def __init__(self, claude_bin: str = "") -> None:
        self._claude_bin = claude_bin or os.path.expanduser("~/.local/bin/claude")

    def supports_tools(self) -> bool:
        return True  # CLI supports tool use natively

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        start = time.monotonic()

        # SECURITY: Warn if file access without sandbox enforcement
        if request.allow_file_access:
            sandbox_only = os.environ.get("AGENT_SANDBOX_ONLY", "0") == "1"
            if sandbox_only:
                return GenerateResponse(
                    error="AGENT_SANDBOX_ONLY=1: file access requires Docker sandbox. "
                          "CLI --dangerously-skip-permissions is disabled.",
                    success=False,
                    model=request.model,
                )
            logger.info("cli_file_access_granted",
                        hint="CLI runs on host FS. Set AGENT_SANDBOX_ONLY=1 to enforce Docker.")

        # Build prompt from messages
        prompt = self._build_prompt(request)

        # Build CLI args
        cli_args = [
            self._claude_bin,
            "--print",
            "--output-format", "json",
        ]
        if request.model:
            cli_args.extend(["--model", request.model])
        if request.max_turns > 1:
            cli_args.extend(["--max-turns", str(request.max_turns)])
        if request.allow_file_access:
            cli_args.append("--dangerously-skip-permissions")

        # Environment
        env = os.environ.copy()
        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

        cwd = request.cwd or None

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cli_args,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=request.timeout,
                env=env,
                cwd=cwd,
            )

            latency_ms = int((time.monotonic() - start) * 1000)

            if result.returncode != 0:
                return GenerateResponse(
                    error=result.stderr[:500] or result.stdout[:500],
                    latency_ms=latency_ms,
                    success=False,
                    model=request.model,
                )

            return self._parse_cli_response(result.stdout, request.model, latency_ms)

        except subprocess.TimeoutExpired:
            return GenerateResponse(
                error=f"CLI timeout after {request.timeout}s",
                latency_ms=int((time.monotonic() - start) * 1000),
                success=False,
                model=request.model,
            )
        except Exception as e:
            return GenerateResponse(
                error=str(e),
                latency_ms=int((time.monotonic() - start) * 1000),
                success=False,
                model=request.model,
            )

    @staticmethod
    def _build_prompt(request: GenerateRequest) -> str:
        """Build a single prompt string from messages + system."""
        parts = []
        if request.system:
            parts.append(request.system)
        for msg in request.messages:
            if msg["role"] == "user":
                parts.append(msg["content"])
            elif msg["role"] == "assistant":
                parts.append(f"[Predchádzajúca odpoveď]: {msg['content']}")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_cli_response(
        stdout: str, model: str, latency_ms: int
    ) -> GenerateResponse:
        """Parse Claude CLI JSON output."""
        try:
            data = orjson.loads(stdout)
        except Exception:
            return GenerateResponse(
                error="Failed to parse CLI JSON response",
                latency_ms=latency_ms,
                success=False,
                model=model,
            )

        if data.get("is_error"):
            return GenerateResponse(
                text=data.get("result", ""),
                error=data.get("result", "Unknown error"),
                latency_ms=latency_ms,
                success=False,
                model=model,
            )

        text = data.get("result", "").strip()
        if not text:
            # Try subresults
            subresults = data.get("subresults", [])
            if subresults:
                texts = [s.get("result", "") for s in subresults if s.get("result")]
                if texts:
                    text = texts[-1].strip()

        usage = data.get("usage", {})
        input_tokens = (
            usage.get("input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
        )
        output_tokens = usage.get("output_tokens", 0)

        return GenerateResponse(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=data.get("total_cost_usd", 0),
            latency_ms=latency_ms,
            success=True,
            raw=data,
        )


# ─────────────────────────────────────────────
# 2. Anthropic API Provider
# ─────────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    """
    Direct Anthropic SDK provider.
    Supports tool_use / function calling.
    """

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def supports_tools(self) -> bool:
        return True

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        if not self._api_key:
            return GenerateResponse(
                error="ANTHROPIC_API_KEY not set",
                success=False,
                model=request.model,
            )

        start = time.monotonic()
        model = request.model or "claude-sonnet-4-6"

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": request.messages,
        }
        if request.system:
            kwargs["system"] = request.system
        if request.tools:
            kwargs["tools"] = request.tools

        try:
            client = self._get_client()
            response = await asyncio.wait_for(
                asyncio.to_thread(client.messages.create, **kwargs),
                timeout=request.timeout,
            )

            latency_ms = int((time.monotonic() - start) * 1000)

            # Extract text and tool calls
            text_parts = []
            tool_calls = []
            for block in response.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

            return GenerateResponse(
                text="\n".join(text_parts).strip(),
                model=response.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=estimate_cost(model, input_tokens, output_tokens),
                tool_calls=tool_calls,
                latency_ms=latency_ms,
                success=True,
                raw=response,
            )

        except TimeoutError:
            return GenerateResponse(
                error=f"API timeout after {request.timeout}s",
                latency_ms=int((time.monotonic() - start) * 1000),
                success=False,
                model=model,
            )
        except Exception as e:
            return GenerateResponse(
                error=str(e),
                latency_ms=int((time.monotonic() - start) * 1000),
                success=False,
                model=model,
            )


# ─────────────────────────────────────────────
# 3. OpenAI-compatible API Provider
# ─────────────────────────────────────────────

class OpenAiProvider(LLMProvider):
    """
    OpenAI-compatible API provider.
    Works with: OpenAI, Ollama, vLLM, LiteLLM, any OpenAI-compatible endpoint.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL", "")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import openai
            kwargs: dict[str, Any] = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def supports_tools(self) -> bool:
        return True

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        start = time.monotonic()
        model = request.model or "gpt-4o"

        # Build OpenAI messages format
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(request.messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": messages,
        }

        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        if request.tools:
            # Convert to OpenAI function calling format
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": tool,
                }
                for tool in request.tools
            ]

        try:
            client = self._get_client()
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.chat.completions.create,
                    **kwargs,
                ),
                timeout=request.timeout,
            )

            latency_ms = int((time.monotonic() - start) * 1000)
            choice = response.choices[0]

            # Extract tool calls
            tool_calls = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": orjson.loads(tc.function.arguments),
                    })

            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0

            return GenerateResponse(
                text=choice.message.content or "",
                model=response.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=estimate_cost(model, input_tokens, output_tokens),
                tool_calls=tool_calls,
                latency_ms=latency_ms,
                success=True,
                raw=response,
            )

        except TimeoutError:
            return GenerateResponse(
                error=f"API timeout after {request.timeout}s",
                latency_ms=int((time.monotonic() - start) * 1000),
                success=False,
                model=model,
            )
        except Exception as e:
            return GenerateResponse(
                error=str(e),
                latency_ms=int((time.monotonic() - start) * 1000),
                success=False,
                model=model,
            )


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────

_provider_cache: dict[str, LLMProvider] = {}


def get_provider(
    backend: str | None = None,
    provider: str | None = None,
    **kwargs: Any,
) -> LLMProvider:
    """
    Get or create an LLM provider instance.

    Config priority: explicit args > env vars > defaults.

    Env vars:
        LLM_BACKEND=cli|api     (default: cli)
        LLM_PROVIDER=anthropic|openai|local  (default: anthropic)
    """
    backend = backend or os.environ.get("LLM_BACKEND", "cli")
    provider_name = provider or os.environ.get("LLM_PROVIDER", "anthropic")

    cache_key = f"{backend}:{provider_name}"
    if cache_key in _provider_cache:
        return _provider_cache[cache_key]

    if backend == "cli":
        instance: LLMProvider = ClaudeCliProvider(**kwargs)
    elif backend == "api":
        if provider_name == "anthropic":
            instance = AnthropicProvider(**kwargs)
        elif provider_name in ("openai", "local"):
            instance = OpenAiProvider(**kwargs)
        else:
            msg = f"Unknown LLM provider: {provider_name}. Use: anthropic, openai, local"
            raise ValueError(msg)
    else:
        msg = f"Unknown LLM backend: {backend}. Use: cli, api"
        raise ValueError(msg)

    _provider_cache[cache_key] = instance
    logger.info("llm_provider_created", backend=backend, provider=provider_name)
    return instance


def clear_provider_cache() -> None:
    """Clear cached providers (for testing)."""
    _provider_cache.clear()
