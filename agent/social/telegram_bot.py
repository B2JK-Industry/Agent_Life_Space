"""
Agent Life Space — Telegram Bot Interface

Allows Daniel to communicate with the agent via Telegram.

Features:
    - Send text messages → agent processes them
    - /status — show agent status
    - /health — show system health
    - /tasks — show pending tasks
    - /memory — query agent memory
    - /budget — show finance status
    - /propose — propose a task for the agent

Security:
    - Only responds to authorized user IDs (whitelist)
    - Bot token stored in vault (encrypted)
    - All messages logged with audit trail
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

# Telegram Bot API base URL
TG_API = "https://api.telegram.org/bot{token}"


class TelegramBot:
    """
    Telegram bot for agent communication.
    Uses raw HTTP API (aiohttp) — no heavy framework dependency.
    """

    def __init__(
        self,
        token: str,
        allowed_user_ids: list[int] | None = None,
        owner_name: str = "owner",
    ) -> None:
        if not token or not token.strip():
            msg = "Telegram bot token cannot be empty"
            raise ValueError(msg)

        self._token = token
        self._base_url = TG_API.format(token=token)
        self._allowed_users = set(allowed_user_ids or [])
        self._owner_name = owner_name
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        _project_root = os.environ.get("AGENT_PROJECT_ROOT", str(Path.home() / "agent-life-space"))
        self._update_id_file = Path(_project_root) / ".last_update_id"
        self._last_update_id = self._load_last_update_id()
        self._handlers: dict[str, Any] = {}
        self._message_callback: Any = None
        self._bot_username: str = ""
        self._bot_id: int = 0

    def _load_last_update_id(self) -> int:
        """Load last processed update ID from disk."""
        try:
            if self._update_id_file.exists():
                return int(self._update_id_file.read_text().strip())
        except (ValueError, OSError):
            pass
        return 0

    def _save_last_update_id(self) -> None:
        """Persist last update ID so restarts don't reprocess old messages."""
        try:
            self._update_id_file.write_text(str(self._last_update_id))
        except OSError:
            pass

    async def start(self) -> None:
        """Start polling for messages."""
        if self._running:
            return

        self._session = aiohttp.ClientSession()
        self._running = True

        # Verify bot token and store identity
        me = await self._api_call("getMe")
        if me:
            self._bot_username = me.get("username", "")
            self._bot_id = me.get("id", 0)
            logger.info(
                "telegram_bot_started",
                username=self._bot_username,
                bot_id=self._bot_id,
            )
        else:
            logger.error("telegram_bot_token_invalid")
            self._running = False
            return

        # Start polling loop
        while self._running:
            try:
                await self._poll_updates()
            except Exception:
                logger.exception("telegram_poll_error")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the bot."""
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("telegram_bot_stopped")

    def on_message(self, callback: Any) -> None:
        """Register a callback for incoming messages."""
        self._message_callback = callback

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "Markdown",
    ) -> dict[str, Any] | None:
        """Send a message. Falls back to plain text if Markdown fails."""
        if len(text) > 4096:
            chunks = [text[i : i + 4096] for i in range(0, len(text), 4096)]
            result = None
            for chunk in chunks:
                result = await self._send_with_fallback(chat_id, chunk, parse_mode)
            return result

        return await self._send_with_fallback(chat_id, text, parse_mode)

    async def _send_with_fallback(
        self, chat_id: int, text: str, parse_mode: str
    ) -> dict[str, Any] | None:
        """Try Markdown first, fall back to plain text on parse error."""
        result = await self._api_call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
        )
        if result is None:
            # Markdown parse error — retry without formatting
            result = await self._api_call(
                "sendMessage",
                chat_id=chat_id,
                text=text,
            )
        return result

    async def _poll_updates(self) -> None:
        """Long-poll for new messages."""
        updates = await self._api_call(
            "getUpdates",
            offset=self._last_update_id + 1,
            timeout=30,
        )

        if not updates:
            return

        for update in updates:
            self._last_update_id = update["update_id"]
            self._save_last_update_id()
            message = update.get("message")
            if message:
                # Process in background — don't block polling
                asyncio.create_task(self._handle_message(message))

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Process an incoming message (private or group)."""
        chat_id = message["chat"]["id"]
        chat_type = message["chat"].get("type", "private")
        user_id = message.get("from", {}).get("id", 0)
        text = message.get("text", "")
        raw_username = message.get("from", {}).get("username", "")
        first_name = message.get("from", {}).get("first_name", "")
        # Ak user_id je v allowed_users → je to owner
        # Inak použi Telegram username alebo first_name
        if self._allowed_users and user_id in self._allowed_users:
            username = self._owner_name
        else:
            username = raw_username or first_name or "unknown"

        # In groups, only respond if mentioned or replied to
        if chat_type in ("group", "supergroup"):
            bot_mentioned = f"@{self._bot_username}" in text if self._bot_username else False
            is_reply_to_bot = (
                message.get("reply_to_message", {}).get("from", {}).get("id") == self._bot_id
            )
            if not bot_mentioned and not is_reply_to_bot:
                return  # Ignore non-directed group messages
            # Strip the @mention from text
            if self._bot_username:
                text = text.replace(f"@{self._bot_username}", "").strip()

        if not text:
            return

        # Security: check if user is authorized
        # V skupinách povoliť aj newhitelistovaným (boti, iní ľudia)
        # ale len ak je John explicitne @mentioned
        is_group = chat_type in ("group", "supergroup")
        if self._allowed_users and user_id not in self._allowed_users:
            if not is_group:
                # Privátna správa od neautorizovaného → blokovať
                logger.warning(
                    "telegram_unauthorized",
                    user_id=user_id,
                    username=username,
                    chat_type=chat_type,
                )
                await self.send_message(
                    chat_id, "Unauthorized. This bot only responds to its owner."
                )
                return
            # V skupine od neautorizovaného → povoliť (bol @mentioned)
            logger.info(
                "telegram_group_interaction",
                user_id=user_id,
                username=username,
            )

        logger.info(
            "telegram_message_received",
            user_id=user_id,
            username=username,
            text_length=len(text),
        )

        # Route to callback — include username and chat_type
        if self._message_callback:
            try:
                response = await self._message_callback(
                    text, user_id, chat_id,
                    username=username, chat_type=chat_type,
                )
                if response:
                    await self.send_message(chat_id, str(response))
            except Exception as e:
                logger.error("telegram_callback_error", error=str(e))
                await self.send_message(chat_id, f"Error: {e!s}")

    async def _api_call(self, method: str, **params: Any) -> Any:
        """Make a Telegram Bot API call."""
        if not self._session:
            return None

        url = f"{self._base_url}/{method}"
        try:
            async with self._session.post(url, json=params, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return data.get("result")
                else:
                    logger.error(
                        "telegram_api_error",
                        method=method,
                        error=data.get("description"),
                    )
                    return None
        except Exception as e:
            logger.error("telegram_api_exception", method=method, error=str(e))
            return None
