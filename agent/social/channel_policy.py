"""
Agent Life Space — Channel Policy

Per-channel capability restrictions and response classification.

Different channels have different trust levels:
    - telegram (private): full access for owner
    - telegram (group): safe mode for non-owners
    - agent_api: restricted, no host access
    - cli: full local access
    - webhook: read-only

Response classification:
    - SAFE: can be sent to any channel
    - PRIVATE: owner-only channels
    - INTERNAL: never sent externally
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ResponseClass(str, Enum):
    """How sensitive is this response?"""

    SAFE = "safe"          # Can be sent to any channel
    PRIVATE = "private"    # Owner-only channels
    INTERNAL = "internal"  # Never sent externally (logs, debug)


class ChannelTrustLevel(str, Enum):
    """Trust level of a communication channel."""

    FULL = "full"              # Owner in private chat — everything allowed
    RESTRICTED = "restricted"  # Agent API, webhooks — limited actions
    SAFE_MODE = "safe_mode"    # Non-owner in group — read-only


@dataclass(frozen=True)
class ChannelCapabilities:
    """What a channel is allowed to do."""

    trust_level: ChannelTrustLevel
    can_execute_code: bool = False
    can_access_host: bool = False
    can_create_tasks: bool = False
    can_web_fetch: bool = False
    can_see_health: bool = True
    can_see_memory: bool = True
    can_approve_actions: bool = False
    max_response_class: ResponseClass = ResponseClass.SAFE


# Per-channel capability config
CHANNEL_CAPABILITIES: dict[str, ChannelCapabilities] = {
    "telegram_owner": ChannelCapabilities(
        trust_level=ChannelTrustLevel.FULL,
        can_execute_code=True,
        can_access_host=True,
        can_create_tasks=True,
        can_web_fetch=True,
        can_approve_actions=True,
        max_response_class=ResponseClass.PRIVATE,
    ),
    "telegram_group": ChannelCapabilities(
        trust_level=ChannelTrustLevel.SAFE_MODE,
        can_execute_code=False,
        can_access_host=False,
        can_create_tasks=False,
        can_web_fetch=False,
        can_approve_actions=False,
        max_response_class=ResponseClass.SAFE,
    ),
    "agent_api": ChannelCapabilities(
        trust_level=ChannelTrustLevel.RESTRICTED,
        can_execute_code=False,
        can_access_host=False,
        can_create_tasks=False,
        can_web_fetch=True,
        can_approve_actions=False,
        max_response_class=ResponseClass.SAFE,
    ),
    "cli": ChannelCapabilities(
        trust_level=ChannelTrustLevel.FULL,
        can_execute_code=True,
        can_access_host=True,
        can_create_tasks=True,
        can_web_fetch=True,
        can_approve_actions=True,
        max_response_class=ResponseClass.PRIVATE,
    ),
}


def get_channel_capabilities(
    channel_type: str,
    is_owner: bool = False,
    is_group: bool = False,
) -> ChannelCapabilities:
    """Resolve channel capabilities based on context."""
    if channel_type == "telegram":
        if is_owner and not is_group:
            return CHANNEL_CAPABILITIES["telegram_owner"]
        return CHANNEL_CAPABILITIES["telegram_group"]

    return CHANNEL_CAPABILITIES.get(
        channel_type,
        # Default: restricted for unknown channels
        ChannelCapabilities(
            trust_level=ChannelTrustLevel.RESTRICTED,
            max_response_class=ResponseClass.SAFE,
        ),
    )


def classify_response(text: str) -> ResponseClass:
    """
    Classify response sensitivity based on content.
    Deterministic — no LLM involved.
    """
    text_lower = text.lower()

    # Internal patterns — never send externally
    internal_patterns = [
        "api_key", "private_key", "secret", "password",
        "vault_key", "token=", "bearer ",
        "/home/", "/root/", "/etc/",
    ]
    if any(p in text_lower for p in internal_patterns):
        return ResponseClass.INTERNAL

    # Private patterns — owner-only
    private_patterns = [
        "wallet", "balance", "eth:", "btc:",
        "budget", "proposal", "approve",
        "cpu_percent", "memory_percent", "disk_percent",
    ]
    if any(p in text_lower for p in private_patterns):
        return ResponseClass.PRIVATE

    return ResponseClass.SAFE


def can_send_response(
    response_class: ResponseClass,
    channel_caps: ChannelCapabilities,
) -> bool:
    """Check if a response can be sent on this channel."""
    if response_class == ResponseClass.INTERNAL:
        return False  # Never send internal responses
    if response_class == ResponseClass.PRIVATE:
        return channel_caps.max_response_class in (
            ResponseClass.PRIVATE, ResponseClass.INTERNAL
        )
    return True  # SAFE can go anywhere
