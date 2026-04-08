"""
Tests for AgentBrain — channel-agnostic message processing.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.identity import get_agent_identity
from agent.core.response_quality import QualityAssessment
from agent.social.channel import IncomingMessage


@pytest.fixture
async def brain():
    """Create AgentBrain with mocked agent."""
    from agent.core.agent import AgentOrchestrator

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, "agent")
        for sub in ("memory", "tasks", "finance", "projects", "logs", "data"):
            os.makedirs(os.path.join(data_dir, sub), exist_ok=True)

        agent = AgentOrchestrator(data_dir=data_dir, watchdog_interval=60.0)
        await agent.initialize()

        from agent.core.brain import AgentBrain
        brain = AgentBrain(agent=agent, owner_chat_id=123)
        yield brain
        await agent.stop()


class TestAgentBrainBasics:
    """Brain processes messages correctly."""

    @pytest.mark.asyncio
    async def test_empty_message(self, brain):
        msg = IncomingMessage(
            text="", sender_id="1", sender_name="owner",
            channel_type="telegram", chat_id="123",
        )
        result = await brain.process(msg)
        assert "Prázdna" in result

    @pytest.mark.asyncio
    async def test_identity_handled_internally(self, brain):
        """Identity questions bypass LLM."""
        msg = IncomingMessage(
            text="kto si?", sender_id="1", sender_name="owner",
            channel_type="telegram", chat_id="123", is_owner=True,
        )
        result = await brain.process(msg)
        assert get_agent_identity().agent_name in result

    @pytest.mark.asyncio
    async def test_status_handled_internally(self, brain):
        """Status questions bypass LLM."""
        msg = IncomingMessage(
            text="aký je tvoj stav?", sender_id="1", sender_name="owner",
            channel_type="telegram", chat_id="123", is_owner=True,
        )
        result = await brain.process(msg)
        assert result is not None
        assert len(result) > 0


class TestBrainPerChat:
    """Conversation context is per-chat, not global."""

    @pytest.mark.asyncio
    async def test_separate_chat_buffers(self, brain):
        """Two chats have independent conversation buffers."""
        conv1 = brain._get_chat_conversation("chat_100")
        conv2 = brain._get_chat_conversation("chat_200")

        conv1.append({"role": "user", "content": "message in chat 1"})

        assert len(conv1) == 1
        assert len(conv2) == 0  # Chat 2 is independent

    @pytest.mark.asyncio
    async def test_conversation_id_includes_chat_id(self, brain):
        """Session IDs are per-chat."""
        id1 = brain._get_conversation_id("100")
        id2 = brain._get_conversation_id("200")

        assert "100" in id1
        assert "200" in id2
        assert id1 != id2


class TestBrainSecurity:
    """Brain enforces security regardless of channel."""

    @pytest.mark.asyncio
    async def test_non_owner_group_cant_use_work_queue(self, brain):
        """Non-owner in group can't queue work."""
        brain._work_loop = MagicMock()
        brain._work_loop.add_work = MagicMock(return_value=3)

        msg = IncomingMessage(
            text="1. task one\n2. task two\n3. task three",
            sender_id="999", sender_name="stranger",
            channel_type="telegram", chat_id="456",
            is_owner=False, is_group=True,
        )
        result = await brain.process(msg)
        assert "owner" in result.lower()
        brain._work_loop.add_work.assert_not_called()

    @pytest.mark.asyncio
    async def test_owner_can_use_work_queue(self, brain):
        """Owner can queue work."""
        brain._work_loop = MagicMock()
        brain._work_loop.add_work = MagicMock(return_value=2)

        msg = IncomingMessage(
            text="1. task one\n2. task two",
            sender_id="1", sender_name="owner",
            channel_type="telegram", chat_id="123",
            is_owner=True,
        )
        result = await brain.process(msg)
        assert "2" in result
        brain._work_loop.add_work.assert_called_once()


class TestBrainUsageTracking:
    """Brain tracks token usage."""

    def test_initial_usage_zero(self, brain):
        usage = brain.get_usage()
        assert usage["total_requests"] == 0
        assert usage["total_cost_usd"] == 0


class TestExplicitWorkQueueDetector:
    """Regression: the multi-task detector must NOT fire on echoed
    agent text or generic numbered text. It must ONLY fire on explicit
    operator intent (header line, clean list without surrounding prose,
    or single-line ``urob: a, b, c`` shortcut)."""

    def _make_brain(self):
        from agent.core.brain import AgentBrain
        b = AgentBrain.__new__(AgentBrain)
        b._conversations = {}
        return b

    def test_short_yes_does_not_trigger(self):
        brain = self._make_brain()
        assert brain._detect_explicit_work_queue("ano", []) == []

    def test_echoed_agent_recommendation_does_not_trigger(self):
        """Real-world failure: user pasted the agent's '1. git pull
        2. pip install 3. restart' back to it; the legacy detector
        spawned 3 work-loop jobs. The new detector must suppress."""
        brain = self._make_brain()
        chat = [{
            "role": "assistant",
            "content": (
                "1. git pull origin main\n"
                "2. pip install -r requirements.txt\n"
                "3. Reštartujem sa"
            ),
        }]
        echo = (
            "1. git pull origin main\n"
            "2. pip install -r requirements.txt\n"
            "3. Reštartujem sa"
        )
        assert brain._detect_explicit_work_queue(echo, chat) == []

    def test_explicit_intent_header_with_newlines_triggers(self):
        brain = self._make_brain()
        items = brain._detect_explicit_work_queue(
            "urob:\n1. test A\n2. test B\n3. test C",
            [],
        )
        assert items == ["test A", "test B", "test C"]

    def test_clean_numbered_list_without_echo_triggers(self):
        brain = self._make_brain()
        items = brain._detect_explicit_work_queue(
            "1. úloha A\n2. úloha B",
            [],
        )
        assert items == ["úloha A", "úloha B"]

    def test_single_line_urob_with_colon_and_commas(self):
        brain = self._make_brain()
        items = brain._detect_explicit_work_queue("urob: A, B, C", [])
        assert items == ["A", "B", "C"]

    def test_legacy_urob_without_colon_still_works(self):
        brain = self._make_brain()
        items = brain._detect_explicit_work_queue("urob A, B, C", [])
        assert items == ["A", "B", "C"]

    def test_numbered_with_surrounding_prose_does_not_trigger(self):
        brain = self._make_brain()
        items = brain._detect_explicit_work_queue(
            "No tak skús toto:\n1. step\n2. step\nA potom mi povedz výsledok.",
            [],
        )
        assert items == []

    def test_quoted_block_does_not_trigger(self):
        brain = self._make_brain()
        items = brain._detect_explicit_work_queue(
            "> 1. step A\n> 2. step B",
            [],
        )
        assert items == []

    def test_header_without_colon_with_numbered_lines(self):
        brain = self._make_brain()
        items = brain._detect_explicit_work_queue(
            "urob\n1. step A\n2. step B",
            [],
        )
        assert items == ["step A", "step B"]

    def test_clean_list_matching_prior_assistant_reply_is_echo(self):
        brain = self._make_brain()
        chat = [{
            "role": "assistant",
            "content": "1. git pull origin main\n2. pip install -r requirements.txt",
        }]
        items = brain._detect_explicit_work_queue(
            "1. git pull origin main\n2. pip install -r requirements.txt",
            chat,
        )
        assert items == []


class TestTelegramCliProgrammingDenyGuard:
    """Regression: Telegram + CLI backend + sandbox-only must NOT enter
    the Claude CLI permission prompt flow because there is no operator
    clicking 'Allow' from Telegram. The brain must fail-closed with a
    clear operator message before the LLM call is made."""

    @pytest.mark.asyncio
    async def test_telegram_programming_cli_sandbox_denied(self, brain, monkeypatch):
        """Programming task from Telegram on the CLI backend with
        AGENT_SANDBOX_ONLY=1 must NOT call provider.generate."""
        monkeypatch.setenv("LLM_BACKEND", "cli")
        monkeypatch.setenv("AGENT_SANDBOX_ONLY", "1")
        monkeypatch.delenv("AGENT_DATA_DIR", raising=False)

        fake_provider = MagicMock()
        fake_provider.supports_tools.return_value = True
        fake_provider.generate = AsyncMock()
        monkeypatch.setattr(
            "agent.core.llm_provider.get_provider", lambda: fake_provider,
        )

        msg = IncomingMessage(
            text="naprogramuj python script ktorý spočíta primes",
            sender_id="1",
            sender_name="owner",
            channel_type="telegram",
            chat_id="123",
            is_owner=True,
        )
        result = await brain.process(msg)

        # Provider must NOT have been called.
        fake_provider.generate.assert_not_called()

        # Operator must see a clear, deterministic message.
        assert "Telegram" in result
        assert "API backend" in result or "AGENT_SANDBOX_ONLY" in result

    @pytest.mark.asyncio
    async def test_telegram_non_programming_still_works_on_cli(self, brain, monkeypatch):
        """Conversational tasks (non-programming) on the CLI backend
        must still reach the provider — guard is task-specific."""
        from agent.core.llm_provider import GenerateResponse

        monkeypatch.setenv("LLM_BACKEND", "cli")
        monkeypatch.setenv("AGENT_SANDBOX_ONLY", "1")

        fake_provider = MagicMock()
        fake_provider.supports_tools.return_value = False
        fake_provider.generate = AsyncMock(
            return_value=GenerateResponse(
                text="Som agent, žijem.",
                success=True,
                input_tokens=5,
                output_tokens=3,
                cost_usd=0.0,
                latency_ms=100,
            ),
        )
        monkeypatch.setattr(
            "agent.core.llm_provider.get_provider", lambda: fake_provider,
        )

        msg = IncomingMessage(
            text="ahoj",
            sender_id="1",
            sender_name="owner",
            channel_type="telegram",
            chat_id="123",
            is_owner=True,
        )
        result = await brain.process(msg)

        # Provider WAS called for the conversational reply.
        assert fake_provider.generate.await_count >= 1
        assert result is not None

    @pytest.mark.asyncio
    async def test_telegram_programming_api_backend_uses_tool_loop(self, brain, monkeypatch):
        """Programming task from Telegram on the API backend must
        still reach ToolUseLoop normally — guard is CLI-only."""
        from agent.core.tool_loop import ToolLoopResult

        monkeypatch.setenv("LLM_BACKEND", "api")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("AGENT_SANDBOX_ONLY", "1")

        fake_provider = MagicMock()
        fake_provider.supports_tools.return_value = True
        fake_loop = MagicMock()
        fake_loop.run = AsyncMock(return_value=ToolLoopResult(
            text="Hotovo, navrhol som riešenie.",
            success=True,
            turns=1,
            total_tokens=20,
            total_input_tokens=15,
            total_output_tokens=5,
            total_cost=0.0,
            model="claude-sonnet-4-6",
        ))
        brain._tool_executor = MagicMock()
        monkeypatch.setattr(
            "agent.core.llm_provider.get_provider", lambda: fake_provider,
        )

        with patch("agent.core.tool_loop.ToolUseLoop", return_value=fake_loop):
            msg = IncomingMessage(
                text="naprogramuj python script ktorý spočíta primes",
                sender_id="1",
                sender_name="owner",
                channel_type="telegram",
                chat_id="123",
                is_owner=True,
            )
            result = await brain.process(msg)

        # Tool loop WAS called — API path is unaffected by the guard.
        fake_loop.run.assert_called_once()
        assert "Hotovo" in result

    @pytest.mark.asyncio
    async def test_telegram_programming_cli_with_host_optin_passes_guard(self, brain, monkeypatch):
        """If AGENT_SANDBOX_ONLY=0, the operator has explicitly opted
        in to host file access. The CLI runs with --dangerously-skip-
        permissions in this state, so there is no interactive prompt
        and the guard must NOT block the request."""
        from agent.core.llm_provider import GenerateResponse

        monkeypatch.setenv("LLM_BACKEND", "cli")
        monkeypatch.setenv("AGENT_SANDBOX_ONLY", "0")  # explicit host opt-in

        fake_provider = MagicMock()
        fake_provider.supports_tools.return_value = False
        fake_provider.generate = AsyncMock(
            return_value=GenerateResponse(
                text="def is_prime(n): return ...",
                success=True,
                input_tokens=10,
                output_tokens=20,
                cost_usd=0.0,
                latency_ms=200,
            ),
        )
        monkeypatch.setattr(
            "agent.core.llm_provider.get_provider", lambda: fake_provider,
        )

        msg = IncomingMessage(
            text="naprogramuj python script ktorý spočíta primes",
            sender_id="1",
            sender_name="owner",
            channel_type="telegram",
            chat_id="123",
            is_owner=True,
        )
        result = await brain.process(msg)

        # Provider WAS called — host opt-in unblocks the path.
        assert fake_provider.generate.await_count >= 1
        assert result is not None
        # Result is not the deny message.
        assert "interaktívneho povolenia" not in result

    @pytest.mark.asyncio
    async def test_non_telegram_programming_cli_passes_guard(self, brain, monkeypatch):
        """The guard targets Telegram specifically — other channels
        (e.g. agent_api) have their own enforcement and must not be
        affected. We test agent_api here because it does NOT enter
        cli_allow_file_access mode (restricted channels list)."""
        from agent.core.llm_provider import GenerateResponse

        monkeypatch.setenv("LLM_BACKEND", "cli")
        monkeypatch.setenv("AGENT_SANDBOX_ONLY", "1")

        fake_provider = MagicMock()
        fake_provider.supports_tools.return_value = False
        fake_provider.generate = AsyncMock(
            return_value=GenerateResponse(
                text="Reply",
                success=True,
                input_tokens=5,
                output_tokens=3,
                cost_usd=0.0,
                latency_ms=100,
            ),
        )
        monkeypatch.setattr(
            "agent.core.llm_provider.get_provider", lambda: fake_provider,
        )

        msg = IncomingMessage(
            text="naprogramuj python skript",
            sender_id="bot",
            sender_name="agent",
            channel_type="agent_api",
            chat_id="api_1",
        )
        result = await brain.process(msg)

        # agent_api channel is unaffected by the Telegram-specific guard.
        # It may still hit other restrictions (cli_allow_file_access=False
        # because agent_api is in restricted_channels) but the brain
        # MUST reach the LLM call, not return the Telegram deny message.
        assert "Telegram" not in (result or "")


class TestShortFollowupGetsHistory:
    """Regression: previously the simple/factual/greeting prompt branch
    rebuilt the prompt without ``conv_context``/``persistent_context``,
    so a short reply like 'ano' arrived at the model with NO history.
    The model then correctly answered 'chýba mi kontext'."""

    @pytest.mark.asyncio
    async def test_simple_reply_includes_prior_assistant_message(self, brain, monkeypatch):
        from agent.core.tool_loop import ToolLoopResult

        # Pre-populate the in-memory chat buffer with one prior exchange.
        chat_id = "123"
        chat = brain._get_chat_conversation(chat_id)
        chat.append({"role": "user", "content": "vieš si nasadiť nový kód?", "sender": "owner"})
        chat.append({
            "role": "assistant",
            "content": (
                "Áno. Spravím:\n"
                "1. git pull origin main\n"
                "2. pip install -r requirements.txt\n"
                "3. reštart"
            ),
        })

        # Capture the prompt sent to the LLM.
        captured: dict[str, str] = {}

        async def fake_run(**kwargs):
            messages = kwargs.get("messages", [])
            if messages:
                captured["prompt"] = messages[0].get("content", "")
            return ToolLoopResult(
                text="Dobre, idem na to.", success=True, turns=1,
                total_tokens=10, total_input_tokens=8, total_output_tokens=2,
                total_cost=0.0, model="claude-haiku-4-5-20251001",
            )

        fake_provider = MagicMock()
        fake_provider.supports_tools.return_value = True
        fake_loop = MagicMock()
        fake_loop.run = AsyncMock(side_effect=fake_run)

        brain._tool_executor = MagicMock()
        monkeypatch.setenv("LLM_BACKEND", "api")
        monkeypatch.setattr("agent.core.llm_provider.get_provider", lambda: fake_provider)

        with patch("agent.core.tool_loop.ToolUseLoop", return_value=fake_loop):
            msg = IncomingMessage(
                text="ano",
                sender_id="1",
                sender_name="owner",
                channel_type="telegram",
                chat_id=chat_id,
                is_owner=True,
            )
            await brain.process(msg)

        prompt = captured.get("prompt", "")
        assert prompt, "ToolUseLoop must have been called"
        # The prompt MUST contain the prior assistant content so the
        # model knows what 'ano' is agreeing to.
        assert "git pull origin main" in prompt, (
            "Short follow-up 'ano' must carry conversation history into "
            "the simple/factual/greeting prompt branch"
        )


class TestBrainMultiChannel:
    """Brain works with any channel type."""

    @pytest.mark.asyncio
    async def test_agent_api_uses_agent_prompt(self, brain):
        """Agent API messages get agent prompt (not user prompt)."""
        # This would need LLM call — just verify the message passes through
        msg = IncomingMessage(
            text="kto si?", sender_id="bot1", sender_name="example_bot",
            channel_type="agent_api", chat_id="api_1",
        )
        result = await brain.process(msg)
        # Should respond (either from dispatcher or LLM)
        assert result is not None

    @pytest.mark.asyncio
    async def test_api_tool_loop_tracks_usage_without_response_object(self, brain, monkeypatch):
        """API tool-use path should use ToolLoopResult metrics and not crash."""
        from agent.core.tool_loop import ToolLoopResult

        fake_provider = MagicMock()
        fake_provider.supports_tools.return_value = True
        fake_loop = MagicMock()
        fake_loop.run = AsyncMock(return_value=ToolLoopResult(
            text="API reply",
            success=True,
            turns=2,
            total_tokens=19,
            total_input_tokens=12,
            total_output_tokens=7,
            total_cost=0.02,
            model="claude-sonnet-4-6",
        ))

        brain._tool_executor = MagicMock()
        monkeypatch.setenv("LLM_BACKEND", "api")
        monkeypatch.setattr("agent.core.llm_provider.get_provider", lambda: fake_provider)

        with patch("agent.core.tool_loop.ToolUseLoop", return_value=fake_loop):
            msg = IncomingMessage(
                text="navrhni zmenu architektury",
                sender_id="1",
                sender_name="owner",
                channel_type="telegram",
                chat_id="123",
                is_owner=True,
            )
            result = await brain.process(msg)

        assert "API reply" in result
        assert "$0.0200" in result
        assert "⬆12" in result
        assert "⬇7" in result
        tool_context = fake_loop.run.call_args.kwargs["tool_context"]
        assert tool_context.is_owner is True
        assert tool_context.safe_mode is False

    @pytest.mark.asyncio
    async def test_budget_blocks_post_routing_escalation(self, brain, monkeypatch):
        fake_provider = MagicMock()
        fake_provider.supports_tools.return_value = False
        fake_provider.generate = AsyncMock(
            return_value=MagicMock(
                success=True,
                text="Neviem. Nemám informácie.",
                cost_usd=0.01,
                input_tokens=10,
                output_tokens=6,
            )
        )

        brain._agent.finance.check_budget = MagicMock(
            return_value={
                "within_budget": True,
                "hard_cap_hit": False,
                "soft_cap_hit": True,
                "stop_loss_hit": False,
                "requires_approval": False,
                "warnings": ["soft cap"],
            }
        )

        monkeypatch.setattr(
            "agent.core.response_quality.assess_quality",
            lambda *args, **kwargs: QualityAssessment(
                score=0.2,
                should_escalate=True,
                reason="Needs stronger model",
                signals=["generic_response"],
            ),
        )
        monkeypatch.setattr("agent.core.llm_provider.get_provider", lambda: fake_provider)

        msg = IncomingMessage(
            text="analyzuj architekturu tohto projektu detailne",
            sender_id="1",
            sender_name="owner",
            channel_type="telegram",
            chat_id="123",
            is_owner=True,
        )

        result = await brain.process(msg)

        assert result is not None
        assert fake_provider.generate.await_count == 1
