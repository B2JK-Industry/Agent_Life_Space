"""
Runtime identity and language configuration for Agent Life Space.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent.core.paths import get_project_root

_PLACEHOLDER_OWNER_VALUES = frozenset(
    {
        "",
        "owner",
        "unknown",
        "user",
        "your-name",
        "your-full-name",
        "your owner name",
        "your full name",
    }
)

_PLACEHOLDER_AGENT_NAME_VALUES = frozenset(
    {
        "",
        "your-agent-name",
        "your agent name",
        "agent",
        "agent life space",
    }
)

_PLACEHOLDER_SERVER_VALUES = frozenset(
    {
        "",
        "your-server-name",
        "your server name",
        "a self-hosted server",
        "self-hosted server",
    }
)


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _is_placeholder_owner(value: str | None) -> bool:
    normalized = _clean(value).lower()
    return normalized in _PLACEHOLDER_OWNER_VALUES


def _is_placeholder_agent_name(value: str | None) -> bool:
    normalized = _clean(value).lower()
    return normalized in _PLACEHOLDER_AGENT_NAME_VALUES


def _is_placeholder_server_name(value: str | None) -> bool:
    normalized = _clean(value).lower()
    return normalized in _PLACEHOLDER_SERVER_VALUES


def _derive_owner_name(value: str | None) -> str:
    cleaned = _clean(value).lstrip("@")
    if not cleaned:
        return ""
    return cleaned.split()[0]


def _get_identity_profile_path() -> Path:
    configured = _clean(os.environ.get("AGENT_IDENTITY_PROFILE_PATH"))
    if configured:
        return Path(configured).expanduser()

    data_dir = _clean(os.environ.get("AGENT_DATA_DIR"))
    if data_dir:
        return Path(data_dir).expanduser() / "identity" / "owner_profile.json"

    return Path(get_project_root()) / ".agent_runtime" / "identity" / "owner_profile.json"


def get_identity_profile_path() -> Path:
    """Public identity profile path for runtime status and self-host checks."""
    return _get_identity_profile_path()


def _load_identity_profile() -> dict[str, object]:
    path = _get_identity_profile_path()
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_identity_profile(profile: dict[str, object]) -> None:
    path = _get_identity_profile_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(profile, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _resolve_owner_identity() -> tuple[str, str]:
    profile = _load_identity_profile()

    env_owner_name = _clean(os.environ.get("AGENT_OWNER_NAME"))
    env_owner_full_name = _clean(os.environ.get("AGENT_OWNER_FULL_NAME"))
    stored_owner_name = _clean(str(profile.get("owner_name", "")))
    stored_owner_full_name = _clean(str(profile.get("owner_full_name", "")))

    explicit_owner_name = "" if _is_placeholder_owner(env_owner_name) else env_owner_name
    explicit_owner_full_name = (
        "" if _is_placeholder_owner(env_owner_full_name) else env_owner_full_name
    )
    stored_owner_name = "" if _is_placeholder_owner(stored_owner_name) else stored_owner_name
    stored_owner_full_name = (
        "" if _is_placeholder_owner(stored_owner_full_name) else stored_owner_full_name
    )

    owner_name = (
        explicit_owner_name
        or stored_owner_name
        or _derive_owner_name(explicit_owner_full_name)
        or _derive_owner_name(stored_owner_full_name)
        or "owner"
    )
    owner_full_name = (
        explicit_owner_full_name
        or stored_owner_full_name
        or explicit_owner_name
        or stored_owner_name
        or owner_name
    )
    return owner_name, owner_full_name


def _resolve_agent_name(project_name: str) -> str:
    configured = _clean(os.environ.get("AGENT_NAME"))
    if _is_placeholder_agent_name(configured):
        return project_name
    return configured or project_name


def _resolve_server_name() -> str:
    configured = _clean(os.environ.get("AGENT_SERVER_NAME"))
    if _is_placeholder_server_name(configured):
        return "a self-hosted server"
    return configured or "a self-hosted server"


def capture_owner_identity_from_telegram(
    *,
    telegram_user_id: int,
    telegram_username: str = "",
    telegram_first_name: str = "",
    telegram_last_name: str = "",
) -> dict[str, object]:
    """Persist owner identity inferred from an authorized Telegram account."""
    profile = _load_identity_profile()
    username = _clean(telegram_username).lstrip("@")
    first_name = _clean(telegram_first_name)
    last_name = _clean(telegram_last_name)
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    fallback_owner_name, fallback_owner_full_name = _resolve_owner_identity()

    owner_name = first_name or username or fallback_owner_name
    owner_full_name = full_name or username or fallback_owner_full_name or owner_name

    profile.update(
        {
            "owner_name": owner_name,
            "owner_full_name": owner_full_name,
            "telegram_user_id": telegram_user_id,
            "telegram_username": username,
            "telegram_first_name": first_name,
            "telegram_last_name": last_name,
            "source": "telegram_authorized_owner",
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    _save_identity_profile(profile)
    return profile


@dataclass(frozen=True)
class AgentIdentity:
    agent_name: str
    owner_name: str
    owner_full_name: str
    server_name: str
    project_name: str
    default_language: str


def get_agent_identity() -> AgentIdentity:
    owner_name, owner_full_name = _resolve_owner_identity()
    project_name = _env("AGENT_PROJECT_NAME", "Agent Life Space")
    return AgentIdentity(
        agent_name=_resolve_agent_name(project_name),
        owner_name=owner_name,
        owner_full_name=owner_full_name,
        server_name=_resolve_server_name(),
        project_name=project_name,
        default_language=_env("AGENT_DEFAULT_LANGUAGE", ""),
    )


def get_identity_onboarding_warnings() -> list[str]:
    """Return non-blocking setup warnings for still-generic identity defaults."""
    profile = _load_identity_profile()
    warnings: list[str] = []

    if _is_placeholder_agent_name(os.environ.get("AGENT_NAME")):
        warnings.append(
            "AGENT_NAME is not configured; the runtime is still using the generic agent name."
        )
    if _is_placeholder_server_name(os.environ.get("AGENT_SERVER_NAME")):
        warnings.append(
            "AGENT_SERVER_NAME is not configured; the runtime is still using the generic server label."
        )

    stored_owner_name = _clean(str(profile.get("owner_name", "")))
    stored_owner_full_name = _clean(str(profile.get("owner_full_name", "")))
    has_owner_profile = not (
        _is_placeholder_owner(stored_owner_name)
        and _is_placeholder_owner(stored_owner_full_name)
    )
    if (
        _is_placeholder_owner(os.environ.get("AGENT_OWNER_NAME"))
        and _is_placeholder_owner(os.environ.get("AGENT_OWNER_FULL_NAME"))
        and not has_owner_profile
    ):
        warnings.append(
            "Owner identity is not configured yet. Set AGENT_OWNER_NAME/AGENT_OWNER_FULL_NAME "
            "or let the authorized Telegram owner teach it on first message."
        )

    return warnings


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
