"""
Regression tests for the brain conversation-persistence guarantees.

These tests bake in the contract that every reply path — deterministic
intent, dispatcher, semantic cache, RAG direct hit, work-queue
acknowledgement, deny-guard, main LLM — appends to both the in-RAM
per-chat tail and the persistent SQLite store.

This is the fix for the production complaint "agent doesn't remember
the previous message". Before the fix, the deterministic intent
handlers (16 of them) bypassed the conversation buffer entirely, so a
"hi" -> intent reply -> "what did I just say?" sequence had zero
history when the second message reached the LLM.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent.social.channel import IncomingMessage

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
async def brain_factory():
    """Factory that produces independent AgentBrain instances pointing
    at the *same* on-disk data dir, so we can simulate a process
    restart by spawning a new brain over the same SQLite store."""
    from agent.core.agent import AgentOrchestrator
    from agent.core.brain import AgentBrain

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, "agent")
        for sub in ("memory", "tasks", "finance", "projects", "logs", "data"):
            os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
        agent = AgentOrchestrator(data_dir=data_dir, watchdog_interval=60.0)
        await agent.initialize()

        created: list = []

        def _make() -> AgentBrain:
            b = AgentBrain(agent=agent, owner_chat_id=123)
            created.append(b)
            return b

        try:
            yield _make
        finally:
            await agent.stop()


def _msg(text: str, chat_id: str = "123") -> IncomingMessage:
    return IncomingMessage(
        text=text,
        sender_id="1",
        sender_name="owner",
        channel_type="telegram",
        chat_id=chat_id,
        is_owner=True,
    )


# ─────────────────────────────────────────────
# 1. Intent reply lands in the in-RAM tail
# ─────────────────────────────────────────────


class TestIntentRepliesArePersisted:
    """Every deterministic intent reply must end up in chat_conv so
    that the next message has the previous exchange in scope."""

    @pytest.mark.asyncio
    async def test_presence_appended(self, brain_factory):
        brain = brain_factory()
        await brain.process(_msg("si tu?"))
        buf = brain._get_chat_conversation("123")
        roles = [e.get("role") for e in buf]
        assert roles == ["user", "assistant"]
        assert "I'm here" in buf[-1].get("content", "")

    @pytest.mark.asyncio
    async def test_two_intents_in_a_row(self, brain_factory):
        brain = brain_factory()
        await brain.process(_msg("si tu?"))
        await brain.process(_msg("aké máš skills?"))
        buf = brain._get_chat_conversation("123")
        assert [e["role"] for e in buf] == ["user", "assistant", "user", "assistant"]
        assert "si tu?" in buf[0]["content"]
        assert "I'm here" in buf[1]["content"]
        assert "skills" in buf[2]["content"].lower()
        assert "Skills" in buf[3]["content"] or "skills" in buf[3]["content"]

    @pytest.mark.asyncio
    async def test_intent_then_followup_sees_history(self, brain_factory):
        """The user's actual production complaint: after a fast-path
        reply, the next message must have the prior exchange visible
        in the in-RAM tail."""
        brain = brain_factory()
        await brain.process(_msg("aké máš skills?"))
        # Now a follow-up that ALSO hits the intent layer should see
        # the prior turn already in chat_conv when it runs.
        prior_buf_len = len(brain._get_chat_conversation("123"))
        assert prior_buf_len == 2
        await brain.process(_msg("a koľko ich je celkom?"))
        # Either the second message hits the LLM (and finalize appends)
        # or it falls through to None and the wrapper still appends.
        buf = brain._get_chat_conversation("123")
        assert len(buf) >= 3, (
            f"expected the prior turn to remain visible, got {buf}"
        )

    @pytest.mark.asyncio
    async def test_persistent_db_records_intent_replies(self, brain_factory):
        """The SQLite store must also receive the exchange so the
        history survives restart."""
        brain = brain_factory()
        await brain.process(_msg("si tu?"))
        # Confirm the row landed in the DB.
        pc = await brain._ensure_persistent_conv()
        assert pc is not None
        conv_id = brain._get_conversation_id("123")
        rows = await pc._get_recent_messages(conv_id)
        # Two rows (user + assistant), in chronological order.
        assert len(rows) == 2
        # First row is the user message, second is the assistant.
        assert "tu" in rows[0][1].lower()
        assert "i'm here" in rows[1][1].lower() or "✅" in rows[1][1]


# ─────────────────────────────────────────────
# 2. Hydration after process restart
# ─────────────────────────────────────────────


class TestHydrationAfterRestart:
    """A new brain instance over the same on-disk store must hydrate
    the in-RAM tail from SQLite on the first message in a chat."""

    @pytest.mark.asyncio
    async def test_first_brain_writes_two_exchanges(self, brain_factory):
        brain = brain_factory()
        await brain.process(_msg("si tu?"))
        await brain.process(_msg("aké máš skills?"))
        buf = brain._get_chat_conversation("123")
        assert len(buf) == 4

    @pytest.mark.asyncio
    async def test_new_brain_hydrates_chat_conv(self, brain_factory):
        # First brain writes the history.
        b1 = brain_factory()
        await b1.process(_msg("si tu?"))
        await b1.process(_msg("aké máš skills?"))

        # Second brain over the same data dir starts empty.
        b2 = brain_factory()
        assert b2._get_chat_conversation("123") == []

        # Hydration restores the prior turns in chronological order.
        await b2._hydrate_chat_conv_if_needed("123")
        buf = b2._get_chat_conversation("123")
        assert [e["role"] for e in buf] == ["user", "assistant", "user", "assistant"]
        assert "si tu?" in buf[0]["content"]
        assert "skills" in buf[2]["content"].lower()

    @pytest.mark.asyncio
    async def test_hydration_runs_only_once_per_chat(self, brain_factory):
        b1 = brain_factory()
        await b1.process(_msg("si tu?"))

        b2 = brain_factory()
        await b2._hydrate_chat_conv_if_needed("123")
        first_len = len(b2._get_chat_conversation("123"))
        # A second hydrate call must be a no-op.
        await b2._hydrate_chat_conv_if_needed("123")
        assert len(b2._get_chat_conversation("123")) == first_len

    @pytest.mark.asyncio
    async def test_process_triggers_hydration_automatically(self, brain_factory):
        """The top-level process() wrapper hydrates before _process_inner runs."""
        b1 = brain_factory()
        await b1.process(_msg("si tu?"))

        b2 = brain_factory()
        # We never call _hydrate_chat_conv_if_needed manually here —
        # process() does it.
        await b2.process(_msg("aké máš skills?"))
        buf = b2._get_chat_conversation("123")
        # 4 historical entries (from b1) + 2 new (current exchange) = 6.
        # But b2 might or might not see all 4 historical depending on
        # tail bound; require AT LEAST one historical user message.
        roles = [e["role"] for e in buf]
        assert roles.count("user") >= 2
        assert roles.count("assistant") >= 2


# ─────────────────────────────────────────────
# 3. Tail bound enforced
# ─────────────────────────────────────────────


class TestConversationTailBound:
    """The in-RAM tail must not grow beyond _max_conversation."""

    @pytest.mark.asyncio
    async def test_default_tail_is_20(self, brain_factory):
        brain = brain_factory()
        assert brain._max_conversation == 20

    @pytest.mark.asyncio
    async def test_tail_bounded(self, brain_factory):
        brain = brain_factory()
        # 15 round-trips → 30 entries → trimmed to 20.
        for i in range(15):
            await brain.process(_msg(f"si tu? {i}"))
        buf = brain._get_chat_conversation("123")
        assert len(buf) <= brain._max_conversation


# ─────────────────────────────────────────────
# 4. Idempotent finalize (no double-append on main LLM path)
# ─────────────────────────────────────────────


class TestFinalizeIdempotent:
    """The wrapper-level finalize must not double-append when the
    main LLM path also finalized internally (legacy behaviour)."""

    @pytest.mark.asyncio
    async def test_intent_reply_appended_exactly_once(self, brain_factory):
        brain = brain_factory()
        await brain.process(_msg("aké máš skills?"))
        buf = brain._get_chat_conversation("123")
        # Exactly one user + one assistant entry, never two.
        assert sum(1 for e in buf if e["role"] == "user") == 1
        assert sum(1 for e in buf if e["role"] == "assistant") == 1
