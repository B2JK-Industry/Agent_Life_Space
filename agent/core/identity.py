"""
Runtime identity and language configuration for Agent Life Space.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass(frozen=True)
class AgentIdentity:
    agent_name: str
    owner_name: str
    owner_full_name: str
    server_name: str
    project_name: str
    default_language: str


def get_agent_identity() -> AgentIdentity:
    owner_name = _env("AGENT_OWNER_NAME", "owner")
    owner_full_name = _env("AGENT_OWNER_FULL_NAME", owner_name)
    return AgentIdentity(
        agent_name=_env("AGENT_NAME", "John"),
        owner_name=owner_name,
        owner_full_name=owner_full_name,
        server_name=_env("AGENT_SERVER_NAME", "a self-hosted server"),
        project_name=_env("AGENT_PROJECT_NAME", "Agent Life Space"),
        default_language=_env("AGENT_DEFAULT_LANGUAGE", ""),
    )


def get_response_language_instruction() -> str:
    language = get_agent_identity().default_language
    if language:
        return (
            f"Respond in {language} unless the user explicitly asks to switch "
            "languages."
        )
    return (
        "Respond in the user's language. If the user explicitly asks to switch "
        "languages, follow that request."
    )
