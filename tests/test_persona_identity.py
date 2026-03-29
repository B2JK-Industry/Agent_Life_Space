from __future__ import annotations

import json

from agent.core.identity import (
    capture_owner_identity_from_telegram,
    get_agent_identity,
    get_identity_onboarding_warnings,
)
from agent.core.persona import (
    get_agent_prompt,
    get_simple_prompt,
    get_system_prompt,
)


def test_persona_uses_runtime_identity(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_NAME", "Sofia")
    monkeypatch.setenv("AGENT_SERVER_NAME", "sofia-node")
    monkeypatch.setenv("AGENT_OWNER_NAME", "Kilian")
    monkeypatch.setenv("AGENT_OWNER_FULL_NAME", "Kilian Novak")
    monkeypatch.setenv("AGENT_DEFAULT_LANGUAGE", "english")

    system_prompt = get_system_prompt()
    agent_prompt = get_agent_prompt()
    simple_prompt = get_simple_prompt()

    assert "You are Sofia" in system_prompt
    assert "sofia-node" in system_prompt
    assert "Kilian Novak" not in system_prompt
    assert "Owner:" not in system_prompt
    assert "Respond in english" in system_prompt
    assert "Respond in english" in agent_prompt
    assert "Respond in english" in simple_prompt


def test_persona_defaults_to_user_language_when_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_DEFAULT_LANGUAGE", raising=False)

    system_prompt = get_system_prompt()

    assert "Respond in the user's language" in system_prompt


def test_identity_uses_saved_owner_profile_when_env_missing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_OWNER_NAME", raising=False)
    monkeypatch.delenv("AGENT_OWNER_FULL_NAME", raising=False)

    profile = capture_owner_identity_from_telegram(
        telegram_user_id=12345,
        telegram_username="kilian_tg",
        telegram_first_name="Kilian",
        telegram_last_name="Novak",
    )
    identity = get_agent_identity()

    assert profile["owner_name"] == "Kilian"
    assert profile["owner_full_name"] == "Kilian Novak"
    assert identity.owner_name == "Kilian"
    assert identity.owner_full_name == "Kilian Novak"


def test_explicit_owner_env_overrides_saved_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    capture_owner_identity_from_telegram(
        telegram_user_id=12345,
        telegram_username="kilian_tg",
        telegram_first_name="Kilian",
        telegram_last_name="Novak",
    )
    monkeypatch.setenv("AGENT_OWNER_NAME", "Operator")
    monkeypatch.setenv("AGENT_OWNER_FULL_NAME", "Operator Prime")

    identity = get_agent_identity()

    assert identity.owner_name == "Operator"
    assert identity.owner_full_name == "Operator Prime"


def test_placeholder_owner_env_allows_saved_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    capture_owner_identity_from_telegram(
        telegram_user_id=12345,
        telegram_username="kilian_tg",
        telegram_first_name="Kilian",
        telegram_last_name="Novak",
    )
    monkeypatch.setenv("AGENT_OWNER_NAME", "your-name")
    monkeypatch.setenv("AGENT_OWNER_FULL_NAME", "your-full-name")

    identity = get_agent_identity()

    assert identity.owner_name == "Kilian"
    assert identity.owner_full_name == "Kilian Novak"


def test_capture_owner_identity_persists_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))

    capture_owner_identity_from_telegram(
        telegram_user_id=42,
        telegram_username="kilian",
        telegram_first_name="",
        telegram_last_name="",
    )

    profile_path = tmp_path / "identity" / "owner_profile.json"
    stored = json.loads(profile_path.read_text(encoding="utf-8"))
    assert stored["owner_name"] == "kilian"
    assert stored["telegram_user_id"] == 42


def test_default_agent_name_is_generic_project_name(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_NAME", raising=False)

    identity = get_agent_identity()

    assert identity.agent_name == "Agent Life Space"


def test_identity_onboarding_warnings_flag_missing_setup(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_NAME", raising=False)
    monkeypatch.delenv("AGENT_SERVER_NAME", raising=False)
    monkeypatch.delenv("AGENT_OWNER_NAME", raising=False)
    monkeypatch.delenv("AGENT_OWNER_FULL_NAME", raising=False)

    warnings = get_identity_onboarding_warnings()

    assert any("AGENT_NAME" in warning for warning in warnings)
    assert any("AGENT_SERVER_NAME" in warning for warning in warnings)
    assert any("Owner identity" in warning for warning in warnings)
