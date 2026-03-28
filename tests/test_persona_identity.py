from __future__ import annotations

from agent.core.persona import (
    get_agent_prompt,
    get_simple_prompt,
    get_system_prompt,
)


def test_persona_uses_runtime_identity(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OWNER_NAME", "Kilian")
    monkeypatch.setenv("AGENT_OWNER_FULL_NAME", "Kilian Novak")
    monkeypatch.setenv("AGENT_DEFAULT_LANGUAGE", "english")

    system_prompt = get_system_prompt()
    agent_prompt = get_agent_prompt()
    simple_prompt = get_simple_prompt()

    assert "Kilian Novak" in system_prompt
    assert "Daniel Babjak" not in system_prompt
    assert "Respond in english" in system_prompt
    assert "Respond in english" in agent_prompt
    assert "Respond in english" in simple_prompt


def test_persona_defaults_to_user_language_when_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_DEFAULT_LANGUAGE", raising=False)

    system_prompt = get_system_prompt()

    assert "Respond in the user's language" in system_prompt
