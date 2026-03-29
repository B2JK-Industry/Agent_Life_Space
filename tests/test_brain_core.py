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


class TestBrainMultiChannel:
    """Brain works with any channel type."""

    @pytest.mark.asyncio
    async def test_agent_api_uses_agent_prompt(self, brain):
        """Agent API messages get agent prompt (not user prompt)."""
        # This would need LLM call — just verify the message passes through
        msg = IncomingMessage(
            text="kto si?", sender_id="bot1", sender_name="b2jk_bot",
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
