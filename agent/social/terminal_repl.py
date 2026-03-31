"""
Agent Life Space — Terminal REPL

Interactive terminal interface to the agent. Runs alongside Telegram bot,
API server, and cron — all sharing the same agent instance.

Features:
- Shared conversation: messages from Telegram are shown in terminal and vice versa
- Logs redirected to file to keep terminal clean
- Same brain/dispatcher/memory as Telegram
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import structlog

from agent.core.identity import get_agent_identity

logger = structlog.get_logger(__name__)

# Global reference so Telegram handler can push messages here
_active_repl: TerminalREPL | None = None


def get_active_repl() -> TerminalREPL | None:
    """Get the active terminal REPL instance (if running)."""
    return _active_repl


def redirect_logs_to_file() -> str:
    """Redirect all structlog/logging output to a file when terminal is active."""
    from agent.core.paths import get_project_root

    log_dir = os.path.join(get_project_root(), "agent", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "agent.log")

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    for h in root.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            root.removeHandler(h)
    root.addHandler(file_handler)

    return log_path


class TerminalREPL:
    """Async terminal REPL with cross-channel message display."""

    def __init__(
        self,
        handler_callback: Any,
        owner_user_id: int = 0,
    ) -> None:
        self._handler = handler_callback
        self._owner_user_id = owner_user_id
        self._running = False
        self._agent_name = ""

    def show_remote_message(self, channel: str, sender: str, text: str, response: str) -> None:
        """Display a message from another channel (e.g. Telegram) in the terminal."""
        if not self._running:
            return
        cleaned_text = self._strip_markdown(text)
        cleaned_response = self._strip_markdown(response)
        print(f"\n  [{channel}] {sender}: {cleaned_text}")
        print(f"  {self._agent_name}: {cleaned_response}\n")
        # Re-show prompt
        print(f"  {self._agent_name}> ", end="", flush=True)

    async def start(self) -> None:
        """Run the interactive REPL loop."""
        global _active_repl
        self._running = True
        _active_repl = self

        identity = get_agent_identity()
        self._agent_name = identity.agent_name.lower().replace(" ", "")

        log_path = redirect_logs_to_file()

        print(f"\n  {identity.agent_name} v{self._get_version()} — Terminal")
        print(f"  Logs: {log_path}")
        print("  Telegram messages will appear here. Type or /command. 'exit' to quit.\n")

        while self._running:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self._read_input, self._agent_name,
                )
            except (EOFError, KeyboardInterrupt):
                print("\n  Exiting.")
                break

            if line is None:
                break

            text = line.strip()
            if not text:
                continue
            if text.lower() in ("exit", "quit", "q"):
                print("  Exiting.")
                break

            try:
                print("  ...")
                response = await self._handler(
                    text,
                    user_id=self._owner_user_id,
                    chat_id=self._owner_user_id,
                    username="owner",
                    chat_type="terminal",
                    is_owner=True,
                )
                cleaned = self._strip_markdown(response)
                print(f"\n  {cleaned}\n")
            except Exception as e:
                print(f"\n  Error: {e}\n")

        _active_repl = None
        self._running = False

    async def stop(self) -> None:
        global _active_repl
        self._running = False
        _active_repl = None

    @staticmethod
    def _read_input(prompt_name: str) -> str | None:
        try:
            return input(f"  {prompt_name}> ")
        except (EOFError, KeyboardInterrupt):
            return None

    @staticmethod
    def _strip_markdown(text: str) -> str:
        return text.replace("*", "").replace("`", "").replace("_", "")

    @staticmethod
    def _get_version() -> str:
        try:
            from agent import __version__
            return __version__
        except Exception:
            return "?"
