"""
Regression tests for the deterministic Telegram intent layer.

These tests bake in the desired behaviour for the family of common
Telegram requests that must NOT fall through to the generic LLM/
provider flow:

  • presence pings
  • version queries
  • skills / capability / limits introspection
  • comparison vs unknown external systems
  • memory horizon / memory usage
  • autonomy / complex-task introspection
  • self-update question vs imperative
  • natural-language web open / read
  • weather report scheduler intent (grounded handler — no pre-bake)
  • CLI raw structured error normalization

Each test covers one explicit behaviour and asserts both the
*absence* of a provider call (where applicable) and the *content* of
the deterministic reply.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.brain import telegram_intents
from agent.social.channel import IncomingMessage

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
async def brain():
    """Create AgentBrain backed by a real (but throwaway) orchestrator."""
    from agent.core.agent import AgentOrchestrator
    from agent.core.brain import AgentBrain

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, "agent")
        for sub in ("memory", "tasks", "finance", "projects", "logs", "data"):
            os.makedirs(os.path.join(data_dir, sub), exist_ok=True)

        agent = AgentOrchestrator(data_dir=data_dir, watchdog_interval=60.0)
        await agent.initialize()
        b = AgentBrain(agent=agent, owner_chat_id=123)
        try:
            yield b
        finally:
            await agent.stop()


def _msg(text: str, *, is_owner: bool = True, is_group: bool = False) -> IncomingMessage:
    return IncomingMessage(
        text=text,
        sender_id="1",
        sender_name="owner",
        channel_type="telegram",
        chat_id="123",
        is_owner=is_owner,
        is_group=is_group,
    )


def _patch_provider(monkeypatch) -> MagicMock:
    """Replace the provider factory with a strict mock that fails on any call."""
    fake = MagicMock()
    fake.supports_tools.return_value = False
    fake.generate = AsyncMock(side_effect=AssertionError(
        "Provider must NOT be called for deterministic intents",
    ))
    monkeypatch.setattr(
        "agent.core.llm_provider.get_provider", lambda: fake,
    )
    return fake


# ─────────────────────────────────────────────
# 1. Pure detection (unit-level)
# ─────────────────────────────────────────────


class TestIntentDetection:
    """detect_intent must classify each canonical phrase."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("si tu?", telegram_intents.PRESENCE),
            ("ahoj", telegram_intents.PRESENCE),
            ("are you there?", telegram_intents.PRESENCE),
            ("ako veľmi živý si?", telegram_intents.PRESENCE),
            ("aké máš skills?", telegram_intents.SKILLS),
            ("what skills do you have?", telegram_intents.SKILLS),
            ("čoho si schopný?", telegram_intents.CAPABILITY),
            ("what can you do?", telegram_intents.CAPABILITY),
            ("na akej verzii teraz bežíš?", telegram_intents.VERSION),
            ("what version?", telegram_intents.VERSION),
            ("vieš si nasadiť novú verziu?", telegram_intents.SELF_UPDATE_QUESTION),
            ("nasad novú verziu u seba", telegram_intents.SELF_UPDATE_IMPERATIVE),
            ("update yourself", telegram_intents.SELF_UPDATE_IMPERATIVE),
            ("aktualizuj sa", telegram_intents.SELF_UPDATE_IMPERATIVE),
            ("otvor obolo.tech", telegram_intents.WEB_OPEN),
            ("open https://example.com", telegram_intents.WEB_OPEN),
            ("v čom si iný ako openclaw?", telegram_intents.COMPARISON),
            ("how are you different from openclaw?", telegram_intents.COMPARISON),
            ("v čom si lepší ako iní agenti?", telegram_intents.COMPARISON),
            ("čo nevieš?", telegram_intents.LIMITS),
            ("what can't you do?", telegram_intents.LIMITS),
            ("vieš tie spomienky aj používať?", telegram_intents.MEMORY_USAGE),
            ("koľko odpovedí dozadu si pamätáš?", telegram_intents.MEMORY_HORIZON),
            ("how far back do you remember?", telegram_intents.MEMORY_HORIZON),
            ("akú veľkú autonómiu máš?", telegram_intents.AUTONOMY),
            ("how autonomous are you?", telegram_intents.AUTONOMY),
            ("aký komplexný task ti môžem dať?", telegram_intents.COMPLEX_TASK),
            ("what complex task can I give you?", telegram_intents.COMPLEX_TASK),
            ("každé ráno mi pošli počasie v Bratislave", telegram_intents.WEATHER_REPORT_SETUP),
            ("every morning send me weather in Prague", telegram_intents.WEATHER_REPORT_SETUP),
            ("set up daily weather report for Košice", telegram_intents.WEATHER_REPORT_SETUP),
        ],
    )
    def test_detect(self, text: str, expected: str) -> None:
        match = telegram_intents.detect_intent(text)
        assert match is not None, f"intent missed for: {text!r}"
        assert match.intent == expected, (
            f"wrong intent for {text!r}: {match.intent} != {expected}"
        )

    def test_unrelated_returns_none(self) -> None:
        match = telegram_intents.detect_intent(
            "random unrelated question that should not match anything",
        )
        assert match is None

    def test_comparison_subject_extraction(self) -> None:
        match = telegram_intents.detect_intent("v čom si iný ako openclaw?")
        assert match is not None
        assert "openclaw" in match.payload.get("subject", "").lower()

    def test_weather_city_extraction(self) -> None:
        match = telegram_intents.detect_intent(
            "každé ráno mi pošli počasie v Bratislave",
        )
        assert match is not None
        assert "bratislav" in match.payload.get("city", "").lower()

    def test_web_url_normalization(self) -> None:
        match = telegram_intents.detect_intent("otvor obolo.tech")
        assert match is not None
        assert match.payload.get("url", "").startswith("https://")
        assert "obolo.tech" in match.payload["url"]


# ─────────────────────────────────────────────
# 2. Brain integration — provider must NOT be called
# ─────────────────────────────────────────────


class TestBrainBypassesProviderForIntents:
    """Each deterministic intent reaches a final reply without
    invoking the LLM provider."""

    @pytest.mark.asyncio
    async def test_presence(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("si tu?"))
        assert result is not None
        assert "I'm here" in result or "✅" in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_version(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        from agent import __version__

        result = await brain.process(_msg("na akej verzii teraz bežíš?"))
        assert __version__ in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_skills(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("aké máš skills?"))
        assert "Skills" in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_capability(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("čoho si schopný?"))
        assert "capability" in result.lower() or "agent" in result.lower()
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_limits(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("čo nevieš?"))
        assert "limit" in result.lower() or "do not" in result.lower() or "not do" in result.lower()
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_update_question_does_nothing(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        # Patch run_self_update so this test fails LOUDLY if the
        # capability is accidentally executed instead of explained.
        with patch("agent.core.self_update.run_self_update") as run_mock:
            run_mock.side_effect = AssertionError(
                "Self-update must NOT run for a question intent",
            )
            result = await brain.process(_msg("vieš si nasadiť novú verziu?"))
        assert "owner-only" in result.lower() or "fast-forward" in result.lower()
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_update_imperative_calls_run(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        from agent.core.self_update import SelfUpdateResult

        run_mock = AsyncMock(return_value=SelfUpdateResult(
            status="up_to_date",
            message="Already up to date on `main` (1234567).",
            branch="main",
            before_sha="1234567abcdef",
            after_sha="1234567abcdef",
        ))
        with patch("agent.core.self_update.run_self_update", run_mock):
            result = await brain.process(_msg("nasad novú verziu u seba"))
        run_mock.assert_awaited_once()
        assert "Already up to date" in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_update_denied_for_non_owner(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        with patch("agent.core.self_update.run_self_update") as run_mock:
            run_mock.side_effect = AssertionError(
                "Self-update must NOT run for non-owner",
            )
            result = await brain.process(
                _msg("nasad novú verziu u seba", is_owner=False, is_group=True),
            )
        assert "owner" in result.lower()
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_update_dirty_worktree(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        from agent.core.self_update import SelfUpdateResult

        with patch(
            "agent.core.self_update.run_self_update",
            AsyncMock(return_value=SelfUpdateResult(
                status="dirty",
                message=(
                    "Self-update refused: the working tree has uncommitted changes."
                ),
            )),
        ):
            result = await brain.process(_msg("nasad novú verziu u seba"))
        assert "uncommitted" in result.lower() or "dirty" in result.lower() or "refused" in result.lower()
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_update_no_change(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        from agent.core.self_update import SelfUpdateResult

        with patch(
            "agent.core.self_update.run_self_update",
            AsyncMock(return_value=SelfUpdateResult(
                status="up_to_date",
                message="Already up to date on `main` (abc1234). Nothing to pull.",
                branch="main",
                before_sha="abc1234",
                after_sha="abc1234",
            )),
        ):
            result = await brain.process(_msg("nasad novú verziu u seba"))
        assert "Already up to date" in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_web_open_uses_internal_fetch(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)

        scrape_mock = AsyncMock(return_value={
            "url": "https://obolo.tech",
            "status": 200,
            "text": "Welcome to obolo!",
            "length": 17,
        })
        with patch("agent.core.web.WebAccess") as web_class:
            web_inst = web_class.return_value
            web_inst.scrape_text = scrape_mock
            web_inst.close = AsyncMock()
            result = await brain.process(_msg("otvor obolo.tech"))

        scrape_mock.assert_awaited_once()
        assert "obolo.tech" in result
        assert "Welcome to obolo!" in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_web_open_network_error_is_friendly(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)

        scrape_mock = AsyncMock(return_value={
            "error": "Connection refused",
            "url": "https://obolo.tech",
        })
        with patch("agent.core.web.WebAccess") as web_class:
            web_inst = web_class.return_value
            web_inst.scrape_text = scrape_mock
            web_inst.close = AsyncMock()
            result = await brain.process(_msg("otvor obolo.tech"))

        assert "refused" in result.lower() or "could not" in result.lower()
        # Must NOT echo raw JSON.
        assert "errormaxturns" not in result
        assert '"error"' not in result
        assert '"is_error"' not in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_comparison_unknown_subject_failsafe(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("v čom si iný ako openclaw?"))

        # Must NOT claim verified facts about openclaw.
        lowered = result.lower()
        assert "openclaw" in lowered
        assert ("do not have a verified" in lowered) or ("internal source of truth" in lowered)
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_comparison_other_agents_balanced(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("v čom si lepší ako iní agenti?"))

        lowered = result.lower()
        # Must contain a "where I'm not" / "honest" framing — not a marketing dump.
        assert "honest" in lowered or "not better" in lowered or "where i" in lowered
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_memory_usage_grounded(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("vieš tie spomienky aj používať?"))

        # Distinguishes the three subsystems.
        assert "Memory store" in result
        assert "knowledge" in result.lower()
        assert "skills" in result.lower()
        # Forbidden fabricated paths.
        assert ".claude/projects" not in result
        assert "/home/" not in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_memory_horizon_grounded(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("koľko odpovedí dozadu si pamätáš?"))

        lowered = result.lower()
        assert "horizon" in lowered or "turn" in lowered
        # No invented user-local paths.
        assert ".claude/projects" not in result
        assert "/home/" not in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_autonomy_mode_aware(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        monkeypatch.setenv("AGENT_SANDBOX_ONLY", "1")
        result = await brain.process(_msg("akú veľkú autonómiu máš?"))

        lowered = result.lower()
        # Must contain explicit guardrails / mode awareness.
        assert "sandbox" in lowered or "guardrail" in lowered or "approval" in lowered
        # Must NOT be a marketing dump.
        assert "i can do anything" not in lowered
        # Must mention non-actions.
        assert "never" in lowered or "by design" in lowered
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_complex_task_grounded_examples(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("aký komplexný task ti môžem dať?"))

        # Concrete commands should be cited, not vague promises.
        assert "/build" in result
        assert "/review" in result
        # Must mention refusal modes too (balanced).
        assert "refuse" in result.lower() or "refusal" in result.lower() or "won't" in result.lower() or "will refuse" in result.lower()
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_weather_setup_does_not_pre_bake(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        result = await brain.process(
            _msg("každé ráno mi pošli počasie v Bratislave"),
        )

        lowered = result.lower()
        # Must mention the build pipeline as the path.
        assert "/build" in result or "build" in lowered
        # Must explicitly NOT claim auto-install.
        assert "self-install" in lowered or "self-deploy" in lowered or "phase" in lowered
        fake.generate.assert_not_called()


# ─────────────────────────────────────────────
# 3. Short follow-ups still get caught
# ─────────────────────────────────────────────


class TestShortFollowupBypassFix:
    """Short follow-up messages with prior history must still hit
    the deterministic intent layer (it runs BEFORE the short-followup
    skip in the dispatcher)."""

    @pytest.mark.asyncio
    async def test_short_followup_skills(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        # Seed history.
        chat_conv = brain._get_chat_conversation("123")
        chat_conv.append({"role": "user", "content": "hi", "sender": "owner"})
        chat_conv.append({"role": "assistant", "content": "hello"})

        result = await brain.process(_msg("aké máš skills?"))
        assert "Skills" in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_followup_presence(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        chat_conv = brain._get_chat_conversation("123")
        chat_conv.append({"role": "user", "content": "earlier", "sender": "owner"})
        chat_conv.append({"role": "assistant", "content": "earlier reply"})

        result = await brain.process(_msg("si tu?"))
        assert "I'm here" in result or "✅" in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_followup_memory_horizon(self, brain, monkeypatch):
        fake = _patch_provider(monkeypatch)
        chat_conv = brain._get_chat_conversation("123")
        chat_conv.append({"role": "user", "content": "earlier", "sender": "owner"})
        chat_conv.append({"role": "assistant", "content": "earlier reply"})

        result = await brain.process(_msg("koľko si pamätáš?"))
        # No fabricated path.
        assert ".claude/projects" not in result
        fake.generate.assert_not_called()


# ─────────────────────────────────────────────
# 4. Error normalization
# ─────────────────────────────────────────────


class TestErrorNormalization:
    """CLI raw structured errors must never reach the user."""

    def test_max_turns_string(self):
        from agent.core.error_normalize import normalize_user_error

        out = normalize_user_error("errormaxturns: tool_use exceeded the limit")
        assert "tool-use turn limit" in out
        assert "errormaxturns" not in out.lower() or "limit" in out

    def test_max_turns_json(self):
        from agent.core.error_normalize import normalize_user_error

        out = normalize_user_error(
            '{"is_error": true, "stop_reason": "max_turns", "result": "boom"}',
        )
        assert "turn limit" in out

    def test_plain_text_passthrough(self):
        from agent.core.error_normalize import normalize_user_error

        out = normalize_user_error("Sure, here is the answer.")
        assert out == "Sure, here is the answer."

    def test_empty(self):
        from agent.core.error_normalize import normalize_user_error

        assert normalize_user_error(None) == ""
        assert normalize_user_error("") == ""

    def test_brain_normalizes_provider_error(self, monkeypatch):
        """The brain's failure path runs response.error through the normalizer."""
        # Smoke test the normalize_user_error helper directly with a
        # payload that contains the noise substrings the brain might
        # see from the CLI.
        from agent.core.error_normalize import normalize_user_error

        raw = '{"is_error": true, "stop_reason": "max_turns", "tool_use": {}, "session_id": "abc"}'
        out = normalize_user_error(raw)
        assert "max_turns" not in out
        assert "session_id" not in out
        assert "tool_use" not in out
