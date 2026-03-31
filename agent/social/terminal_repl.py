"""
Agent Life Space — Terminal REPL

Interactive terminal interface to the agent. Runs alongside Telegram bot,
API server, and cron — all sharing the same agent instance.

Usage:
    python -m agent  (with AGENT_TERMINAL=1 or --terminal flag)

The REPL processes messages through the same TelegramHandler pipeline,
so /commands, dispatcher, brain, memory — everything works identically.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from agent.core.identity import get_agent_identity

logger = structlog.get_logger(__name__)


class TerminalREPL:
    """Async terminal REPL that feeds into the agent's message handler."""

    def __init__(
        self,
        handler_callback: Any,
        owner_user_id: int = 0,
    ) -> None:
        self._handler = handler_callback
        self._owner_user_id = owner_user_id
        self._running = False

    async def start(self) -> None:
        """Run the interactive REPL loop."""
        self._running = True
        identity = get_agent_identity()
        agent_name = identity.agent_name.lower().replace(" ", "")

        print(f"\n  {identity.agent_name} v{self._get_version()} — Terminal")
        print("  Type a message or /command. 'exit' to quit.\n")

        while self._running:
            try:
                # Read input in a thread to avoid blocking the event loop
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self._read_input, agent_name,
                )
            except (EOFError, KeyboardInterrupt):
                print("\nExiting terminal.")
                break

            if line is None:
                break

            text = line.strip()
            if not text:
                continue
            if text.lower() in ("exit", "quit", "q"):
                print("Exiting terminal.")
                break

            try:
                response = await self._handler(
                    text,
                    user_id=self._owner_user_id,
                    chat_id=self._owner_user_id,
                    username="owner",
                    chat_type="terminal",
                    is_owner=True,
                )
                # Strip Telegram markdown for cleaner terminal output
                cleaned = self._strip_markdown(response)
                print(f"\n{cleaned}\n")
            except Exception as e:
                print(f"\n  Error: {e}\n")

    async def stop(self) -> None:
        self._running = False

    @staticmethod
    def _read_input(prompt_name: str) -> str | None:
        """Blocking stdin read — runs in executor thread."""
        try:
            return input(f"  {prompt_name}> ")
        except (EOFError, KeyboardInterrupt):
            return None

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove basic Telegram markdown for terminal display."""
        return text.replace("*", "").replace("`", "").replace("_", "")

    @staticmethod
    def _get_version() -> str:
        try:
            from agent import __version__
            return __version__
        except Exception:
            return "?"
