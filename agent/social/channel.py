"""
Agent Life Space — Communication Channel Abstraction

Každý komunikačný kanál (Telegram, Discord, API, CLI, email...)
implementuje Channel interface. Agent brain je nezávislý od kanálu.

Použitie:
    class MyChannel(Channel):
        async def start(self): ...
        async def stop(self): ...
        async def send(self, message): ...

    registry = ChannelRegistry(brain)
    registry.register(TelegramChannel(...))
    registry.register(ApiChannel(...))
    await registry.start_all()
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class IncomingMessage:
    """Channel-agnostic incoming message."""

    text: str
    sender_id: str
    sender_name: str
    channel_type: str  # "telegram", "discord", "api", "cli", "email"
    chat_id: str
    is_owner: bool = False
    is_group: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    """Channel-agnostic outgoing message."""

    text: str
    chat_id: str
    parse_mode: str = ""
    reply_to: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


MessageCallback = Callable[[IncomingMessage], Coroutine[Any, Any, str]]


class Channel(ABC):
    """Abstract communication channel."""

    @abstractmethod
    async def start(self) -> None:
        """Start listening for messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel."""

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> bool:
        """Send a message. Returns True if sent successfully."""

    @abstractmethod
    def on_message(self, callback: MessageCallback) -> None:
        """Register callback for incoming messages."""

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """Channel type identifier (telegram, discord, api, etc.)."""


class ChannelRegistry:
    """
    Manages multiple communication channels.
    Routes incoming messages to AgentBrain, outgoing to correct channel.
    """

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}
        self._message_callback: MessageCallback | None = None

    def register(self, channel: Channel) -> None:
        """Register a channel."""
        self._channels[channel.channel_type] = channel
        if self._message_callback:
            channel.on_message(self._message_callback)
        logger.info("channel_registered", type=channel.channel_type)

    def on_message(self, callback: MessageCallback) -> None:
        """Set the message handler for all channels."""
        self._message_callback = callback
        for channel in self._channels.values():
            channel.on_message(callback)

    async def start_all(self) -> None:
        """Start all registered channels."""
        for channel in self._channels.values():
            try:
                await channel.start()
                logger.info("channel_started", type=channel.channel_type)
            except Exception as e:
                logger.error("channel_start_failed", type=channel.channel_type, error=str(e))

    async def stop_all(self) -> None:
        """Stop all channels."""
        for channel in self._channels.values():
            try:
                await channel.stop()
            except Exception:
                pass

    async def broadcast(self, text: str, exclude: str = "") -> None:
        """Send message to all channels (except excluded)."""
        for ch_type, channel in self._channels.items():
            if ch_type == exclude:
                continue
            try:
                await channel.send(OutgoingMessage(text=text, chat_id=""))
            except Exception:
                pass

    def get_channel(self, channel_type: str) -> Channel | None:
        return self._channels.get(channel_type)

    @property
    def active_channels(self) -> list[str]:
        return list(self._channels.keys())
