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
            # Memory list (production complaint)
            ("ake su tvoje spomienky ?", telegram_intents.MEMORY_LIST),
            ("aké sú tvoje spomienky?", telegram_intents.MEMORY_LIST),
            ("aké máš spomienky?", telegram_intents.MEMORY_LIST),
            ("what are your memories?", telegram_intents.MEMORY_LIST),
            ("list your memories", telegram_intents.MEMORY_LIST),
            ("show me your memories", telegram_intents.MEMORY_LIST),
            # Context recall (production complaint)
            ("prečo si začal s touto temou ?", telegram_intents.CONTEXT_RECALL),
            ("preco si zacal s touto temou?", telegram_intents.CONTEXT_RECALL),
            ("o čom sme sa bavili?", telegram_intents.CONTEXT_RECALL),
            ("čo sme riešili?", telegram_intents.CONTEXT_RECALL),
            ("why did you start this topic?", telegram_intents.CONTEXT_RECALL),
            ("what were we talking about?", telegram_intents.CONTEXT_RECALL),
            ("remind me what i said", telegram_intents.CONTEXT_RECALL),
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
    async def test_self_update_schedules_restart_when_flagged(self, brain, monkeypatch):
        """When run_self_update returns should_self_restart=True the
        brain must schedule the graceful restart task."""
        fake = _patch_provider(monkeypatch)
        from agent.core.self_update import SelfUpdateResult

        run_mock = AsyncMock(return_value=SelfUpdateResult(
            status="updated",
            message="Fast-forwarded `main` from abc1234 to def5678 (5 commits).",
            branch="main",
            before_sha="abc1234567",
            after_sha="def5678901",
            fetched_commits=5,
            should_self_restart=True,
        ))

        # Replace _schedule_graceful_restart with a recording stub so
        # we never actually call os._exit during a test.
        scheduled = {"called": False}

        def fake_schedule() -> None:
            scheduled["called"] = True

        brain._schedule_graceful_restart = fake_schedule  # type: ignore[method-assign]

        with patch("agent.core.self_update.run_self_update", run_mock):
            result = await brain.process(_msg("nasad novú verziu u seba"))

        run_mock.assert_awaited_once()
        assert "Fast-forwarded" in result
        assert scheduled["called"] is True
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_update_does_not_schedule_restart_when_not_flagged(
        self, brain, monkeypatch,
    ):
        """When the result has should_self_restart=False (default),
        the brain must NOT schedule a restart even on success."""
        fake = _patch_provider(monkeypatch)
        from agent.core.self_update import SelfUpdateResult

        run_mock = AsyncMock(return_value=SelfUpdateResult(
            status="updated",
            message="Fast-forwarded `main` from abc1234 to def5678 (5 commits).",
            branch="main",
            should_self_restart=False,
        ))
        scheduled = {"called": False}

        def fake_schedule() -> None:
            scheduled["called"] = True

        brain._schedule_graceful_restart = fake_schedule  # type: ignore[method-assign]

        with patch("agent.core.self_update.run_self_update", run_mock):
            result = await brain.process(_msg("nasad novú verziu u seba"))

        assert "Fast-forwarded" in result
        assert scheduled["called"] is False
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_schedule_graceful_restart_uses_running_loop(
        self, brain, monkeypatch,
    ):
        """The scheduler creates an asyncio task on the running loop
        and registers a strong reference so it isn't GC'd."""
        # Patch os._exit so we never actually exit the test process.
        monkeypatch.setattr("os._exit", lambda code=0: None)
        # Patch agent.stop to a no-op so the drain finishes fast.
        brain._agent.stop = AsyncMock()  # type: ignore[method-assign]
        # Set the grace period to 0 so the test doesn't sleep.
        monkeypatch.setenv("AGENT_SELF_RESTART_GRACE_S", "0")

        # Call the scheduler directly.
        brain._schedule_graceful_restart()
        # The set should have one task registered.
        assert hasattr(brain, "_pending_shutdown_tasks")
        assert len(brain._pending_shutdown_tasks) == 1
        # Wait for the scheduled task to finish.
        import asyncio as _asyncio
        for task in list(brain._pending_shutdown_tasks):
            try:
                await _asyncio.wait_for(task, timeout=2.0)
            except TimeoutError:
                task.cancel()
                raise
        # After completion the task should have been removed.
        assert len(brain._pending_shutdown_tasks) == 0
        # And agent.stop should have been awaited.
        brain._agent.stop.assert_awaited()

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
    async def test_memory_list(self, brain, monkeypatch):
        """The 'aké sú tvoje spomienky' intent reads the memory store
        and produces a deterministic listing — no provider call."""
        fake = _patch_provider(monkeypatch)
        # Seed one memory so the listing has something to show.
        from agent.memory.store import MemoryEntry, MemoryType

        await brain._agent.memory.store(MemoryEntry(
            content="test memory entry from regression test",
            memory_type=MemoryType.SEMANTIC,
            tags=["regression"],
            source="test",
            importance=0.5,
        ))
        result = await brain.process(_msg("ake su tvoje spomienky ?"))
        assert "memory" in result.lower() or "spomien" in result.lower() or "Recent memory" in result
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_recall_with_history(self, brain, monkeypatch):
        """'prečo si začal s touto temou' reads the in-RAM chat tail
        and lists the prior turns deterministically."""
        fake = _patch_provider(monkeypatch)
        # Seed history.
        chat_conv = brain._get_chat_conversation("123")
        chat_conv.append({"role": "user", "content": "tell me about coffee", "sender": "owner"})
        chat_conv.append({"role": "assistant", "content": "Coffee is a beverage."})
        chat_conv.append({"role": "user", "content": "is it healthy?", "sender": "owner"})
        chat_conv.append({"role": "assistant", "content": "Moderate consumption is fine."})

        result = await brain.process(_msg("prečo si začal s touto temou ?"))
        assert "coffee" in result.lower() or "beverage" in result.lower()
        fake.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_recall_no_history(self, brain, monkeypatch):
        """With no prior turns the handler explains the situation
        instead of fabricating one."""
        fake = _patch_provider(monkeypatch)
        result = await brain.process(_msg("o čom sme sa bavili?"))
        assert "don't have" in result.lower() or "no earlier" in result.lower() or "fresh" in result.lower()
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

    def test_legacy_chyba_payload_normalized(self):
        """Regression: the production payload that was leaking through
        the legacy _handle_text path. Must NOT survive verbatim."""
        from agent.core.error_normalize import normalize_user_error

        raw = (
            '{"type":"result","subtype":"errormaxturns","durationms":2868,'
            '"durationapims":2812,"iserror":true,"numturns":2,'
            '"stopreason":"tooluse","sessionid":"e7ec9cd8-3907-4661-afca"}'
        )
        out = normalize_user_error(raw)
        # No raw JSON / internal fields.
        assert "errormaxturns" not in out
        assert "sessionid" not in out
        assert "stopreason" not in out
        assert "durationms" not in out
        assert "iserror" not in out
        assert '"type"' not in out
        # Has a friendly explanation.
        assert "turn limit" in out.lower() or "tool-use" in out.lower()

    @pytest.mark.parametrize(
        "raw",
        [
            "CLI timeout after 180s",
            "CLI timeout after 60s",
            "timeout after 30s",
            "timed out after 120 seconds",
            "Cli Timeout After 90s",  # case-insensitive
            "read timed out",
            "deadline exceeded",
            "asyncio.TimeoutError",
            "TimeoutError",
            "request_timeout=60",
            "request_timeout: 180",
        ],
    )
    def test_plain_cli_timeout_normalized(self, raw):
        """Plain CLI timeouts (and the wider timeout family) must
        become a short user-facing sentence, not a raw error string."""
        from agent.core.error_normalize import normalize_user_error

        out = normalize_user_error(raw)
        assert "took too long" in out.lower()
        assert "shorten" in out.lower() or "shortening" in out.lower()
        # None of the raw technical phrasings survive verbatim.
        assert "cli timeout" not in out.lower()
        assert "asyncio.timeouterror" not in out.lower()
        assert "deadline exceeded" not in out.lower()
        assert "request_timeout" not in out.lower()


# ─────────────────────────────────────────────
# 5. Paraphrased self-update question fallback
# ─────────────────────────────────────────────


class TestParaphrasedSelfUpdateQuestion:
    """Regression: paraphrased capability questions about self-update
    must hit the deterministic explanation path instead of leaking into
    a 180s CLI timeout in generic chat flow."""

    @pytest.mark.parametrize(
        "text",
        [
            # User's actual production message that hit the timeout.
            "vraj maš novu verziu kde si schopny si aj nasadit nove veci k sebe je to tak ?",
            "je pravda že sa už vieš sám aktualizovať?",
            "už si vieš nasadiť nové veci k sebe?",
            "máš capability aktualizovať sám seba?",
            "vieš si k sebe nasadiť novú verziu alebo nie?",
        ],
    )
    def test_paraphrase_detected_as_self_update_question(self, text):
        """The heuristic must catch paraphrased capability questions."""
        match = telegram_intents.detect_intent(text)
        assert match is not None, f"intent missed for paraphrase: {text!r}"
        assert match.intent == telegram_intents.SELF_UPDATE_QUESTION

    @pytest.mark.parametrize(
        "text",
        [
            # Imperatives must NOT regress to question intent.
            "nasad novú verziu u seba",
            "update yourself",
            "aktualizuj sa",
            "deploy latest",
            # Download + deploy combos (the operator's natural phrasing).
            "stiahni si novu verziu a nasad to",
            "stiahni si novú verziu a nasaď to",
            "stiahni si najnovšiu verziu a nasaď ju",
            "stiahni najnovsi kod a nasad ho",
            "stiahni si update a nasaď",
            "stiahni a nasaď",
            # Standalone download against the agent itself.
            "stiahni novú verziu",
            "stiahni si novú verziu",
            "stiahni si najnovšiu verziu",
            "stiahnite novú verziu",
            "stiahni najnovšiu verziu",
            # English equivalents.
            "pull and deploy",
            "download and deploy latest",
            "download the latest version",
            "download newest version",
            "pull the latest and deploy",
            "fetch and install",
            "pull latest and restart",
            # Free-form heuristic paraphrases (the operator types
            # whatever they think of, the heuristic catches it).
            "stiahni si nový kód z githubu a nahoď ho",
            "vezmi si najnovšiu verziu a nasaď",
            "spusti deploy",
            "spustite deploy nového kódu",
            "nahoď to čo je na main",
            "nahoď to čo je na github",
            "aktualizuj sa na najnovšiu verziu",
            "naťahaj nový kód a aktualizuj sa",
            "natahaj novy kod a nasad",
            "vezmi z githubu posledný kód",
            "vezmi nový kód z hlavnej vetvy",
            "stiahni github a nasaď",
            "stiahni github update",
            "stiahni si update",
            "stiahni si novinky",
            "stiahni z gitu nový kód",
            # Bare git invocations.
            "git pull",
            "git pull a reštart",
            "git pull and restart",
            "git fetch",
            "pull from github",
            "pull from github and restart",
            "pull from main",
            # English free-form.
            "grab the latest code",
            "get the latest version",
            "redeploy with the new code",
            "release the latest",
            "install the latest update",
            "update to latest",
            "fetch new version",
        ],
    )
    def test_imperative_still_routes_to_execution(self, text):
        match = telegram_intents.detect_intent(text)
        assert match is not None
        assert match.intent == telegram_intents.SELF_UPDATE_IMPERATIVE, (
            f"{text!r} got {match.intent!r}, expected self_update_imperative"
        )

    @pytest.mark.parametrize(
        "text",
        [
            # Negative regression: unrelated questions / ambiguous
            # phrasings must NOT be misclassified as self-update.
            "random unrelated question",
            "môžem ti dať novú verziu prompt-u?",
            "aktualizujme tento súbor prosím",
            "vieš mi ukázať starú verziu kódu?",
            "vieš si stiahnuť ten obrázok?",
            "are you there?",
            "what version?",
            "aké máš skills?",
        ],
    )
    def test_unrelated_questions_not_misclassified(self, text):
        match = telegram_intents.detect_intent(text)
        if match is not None:
            assert match.intent != telegram_intents.SELF_UPDATE_QUESTION

    @pytest.mark.parametrize(
        "text",
        [
            # Things that *contain* "stiahni" / "download" / "pull"
            # but are NOT about the agent updating itself. The
            # heuristic must reject these via the non-self-target
            # exclusion list.
            "stiahni mi obrázok z internetu",
            "stiahni si tento film",
            "stiahni mi pdf z tej stránky",
            "stiahni si tú fotku",
            "stiahni mi mp3 z youtube",
            "download this video for me",
            "pull the milk from the fridge",
            "get me a coffee",
            "grab me the file",
            "aktualizujme tento súbor prosím",
            "aktualizuj toto pdf",
        ],
    )
    def test_unrelated_download_not_misclassified(self, text):
        match = telegram_intents.detect_intent(text)
        if match is not None:
            assert match.intent != telegram_intents.SELF_UPDATE_IMPERATIVE, (
                f"{text!r} should not be classified as self_update_imperative"
            )

    @pytest.mark.asyncio
    async def test_brain_routes_paraphrase_without_provider(self, brain, monkeypatch):
        """The user's actual production message: must reach the
        deterministic explanation path with no provider call and no
        self_update execution."""
        fake = _patch_provider(monkeypatch)
        with patch("agent.core.self_update.run_self_update") as run_mock:
            run_mock.side_effect = AssertionError(
                "Self-update must NOT execute for a paraphrased question",
            )
            result = await brain.process(_msg(
                "vraj maš novu verziu kde si schopny si aj nasadit "
                "nove veci k sebe je to tak ?",
            ))
        # Capability explanation, not an execution result.
        assert "owner-only" in result.lower() or "fast-forward" in result.lower()
        fake.generate.assert_not_called()


# ─────────────────────────────────────────────
# 6. Plain CLI timeout reaches the user as friendly text
# ─────────────────────────────────────────────


class TestBrainTimeoutNormalization:
    """When the CLI provider returns a plain ``CLI timeout after 180s``
    error, the brain must surface a normalized friendly sentence — not
    the raw timeout string."""

    @pytest.mark.asyncio
    async def test_provider_timeout_is_normalized(self, brain, monkeypatch):
        from agent.core.llm_provider import GenerateResponse

        # Avoid the deterministic intent layer so we exercise the
        # generic provider failure path. "explain entropy" classifies
        # as chat/factual, hits the provider, returns a timeout error.
        fake = MagicMock()
        fake.supports_tools.return_value = False
        fake.generate = AsyncMock(return_value=GenerateResponse(
            error="CLI timeout after 180s",
            success=False,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=180000,
        ))
        monkeypatch.setattr(
            "agent.core.llm_provider.get_provider", lambda: fake,
        )
        # Disable the Telegram+CLI+sandbox guard so we reach the
        # provider call (the question is conversational, not programming).
        monkeypatch.setenv("LLM_BACKEND", "cli")
        monkeypatch.setenv("AGENT_SANDBOX_ONLY", "0")

        result = await brain.process(_msg(
            "Vysvetli mi prosím entropiu v termodynamike v troch vetách.",
        ))

        # Must NOT contain the raw CLI noise.
        assert "CLI timeout" not in result
        assert "180s" not in result or "timeout after 180s" in result.lower()
        # Must contain the friendly sentence.
        assert "took too long" in result.lower() or "thinking" in result.lower()
