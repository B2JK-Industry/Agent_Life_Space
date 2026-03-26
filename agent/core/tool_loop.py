"""
Agent Life Space — Tool Use Loop

Multi-turn conversation s LLM kde agent volá vlastné funkcie.

Flow:
    1. Pošli messages + tools do LLM
    2. Ak LLM vráti tool_use bloky → vykonaj cez ToolExecutor
    3. Pridaj tool_result správy
    4. Pošli naspäť do LLM
    5. Opakuj kým LLM odpovie len textom (žiadne tool_use)
    6. Max turns limit (default 10)

Funguje s Anthropic API aj OpenAI API (oba podporujú function calling).
CLI backend toto nepotrebuje (má vlastné nástroje).
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from agent.core.llm_provider import GenerateRequest, LLMProvider
from agent.core.tool_executor import ToolExecutor
from agent.core.tools import AGENT_TOOLS

logger = structlog.get_logger(__name__)


class ToolUseLoop:
    """
    Multi-turn tool use conversation loop.
    LLM calls tools, we execute them, send results back, repeat.
    """

    def __init__(
        self,
        provider: LLMProvider,
        tool_executor: ToolExecutor,
        max_turns: int = 10,
    ) -> None:
        self._provider = provider
        self._executor = tool_executor
        self._max_turns = max_turns
        self._total_tokens = 0
        self._total_cost = 0.0

    async def run(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        model: str = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        timeout: int = 180,
    ) -> ToolLoopResult:
        """
        Run the tool use loop until LLM responds with text only
        or max_turns is reached.
        """
        start = time.monotonic()
        tools = tools or AGENT_TOOLS
        conversation = list(messages)  # Copy to avoid mutation
        turn = 0
        tool_calls_made: list[dict] = []

        while turn < self._max_turns:
            turn += 1

            response = await self._provider.generate(GenerateRequest(
                messages=conversation,
                system=system,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                timeout=timeout,
            ))

            self._total_tokens += response.input_tokens + response.output_tokens
            self._total_cost += response.cost_usd

            if not response.success:
                return ToolLoopResult(
                    text=response.error,
                    success=False,
                    turns=turn,
                    tool_calls=tool_calls_made,
                    total_tokens=self._total_tokens,
                    total_cost=self._total_cost,
                    latency_ms=int((time.monotonic() - start) * 1000),
                )

            # If no tool calls — we're done, LLM responded with text
            if not response.tool_calls:
                return ToolLoopResult(
                    text=response.text,
                    success=True,
                    turns=turn,
                    tool_calls=tool_calls_made,
                    total_tokens=self._total_tokens,
                    total_cost=self._total_cost,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    model=response.model,
                )

            # Process tool calls
            # Add assistant message with tool_use blocks
            assistant_content: list[dict] = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
            conversation.append({"role": "assistant", "content": assistant_content})

            # Execute each tool and build tool_result messages
            tool_results: list[dict] = []
            for tc in response.tool_calls:
                logger.info("tool_call", name=tc["name"], turn=turn)
                tool_calls_made.append(tc)

                result = await self._executor.execute(tc["name"], tc["input"])

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": _format_tool_result(result),
                })

            conversation.append({"role": "user", "content": tool_results})

        # Max turns reached
        return ToolLoopResult(
            text="Dosiahol som maximálny počet krokov. Tu je posledný stav.",
            success=True,
            turns=turn,
            tool_calls=tool_calls_made,
            total_tokens=self._total_tokens,
            total_cost=self._total_cost,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_tokens": self._total_tokens,
            "total_cost": round(self._total_cost, 4),
        }


class ToolLoopResult:
    """Result of a multi-turn tool use conversation."""

    def __init__(
        self,
        text: str = "",
        success: bool = True,
        turns: int = 0,
        tool_calls: list[dict] | None = None,
        total_tokens: int = 0,
        total_cost: float = 0.0,
        latency_ms: int = 0,
        model: str = "",
    ) -> None:
        self.text = text
        self.success = success
        self.turns = turns
        self.tool_calls = tool_calls or []
        self.total_tokens = total_tokens
        self.total_cost = total_cost
        self.latency_ms = latency_ms
        self.model = model


def _format_tool_result(result: dict[str, Any]) -> str:
    """Format tool result dict as string for LLM consumption."""
    import orjson

    try:
        return orjson.dumps(result, option=orjson.OPT_INDENT_2).decode()
    except Exception:
        return str(result)
