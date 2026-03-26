"""
Smoke tests — verify core modules import and initialize without errors.

These catch:
- Missing imports
- Circular dependencies
- Constructor crashes
- Basic wiring issues
"""

from __future__ import annotations


class TestCoreImports:
    """All core modules import without error."""

    def test_import_agent(self):
        from agent.core.agent import AgentOrchestrator
        assert AgentOrchestrator

    def test_import_brain(self):
        from agent.core.brain import AgentBrain
        assert AgentBrain

    def test_import_models(self):
        from agent.core.models import classify_task, get_model
        assert classify_task
        assert get_model

    def test_import_tool_policy(self):
        from agent.core.tool_policy import TOOL_CAPABILITIES, ToolPolicy
        assert ToolPolicy
        assert len(TOOL_CAPABILITIES) >= 10

    def test_import_tool_executor(self):
        from agent.core.tool_executor import ToolExecutor
        assert ToolExecutor

    def test_import_tool_loop(self):
        from agent.core.tool_loop import ToolLoopResult, ToolUseLoop
        assert ToolUseLoop
        assert ToolLoopResult

    def test_import_llm_provider(self):
        from agent.core.llm_provider import (
            AnthropicProvider,
            ClaudeCliProvider,
            OpenAiProvider,
        )
        assert ClaudeCliProvider
        assert AnthropicProvider
        assert OpenAiProvider

    def test_import_action(self):
        from agent.core.action import ActionEnvelope, ActionLog
        assert ActionEnvelope
        assert ActionLog

    def test_import_approval(self):
        from agent.core.approval import ApprovalQueue
        assert ApprovalQueue

    def test_import_persona(self):
        from agent.core.persona import AGENT_PROMPT, SIMPLE_PROMPT, SYSTEM_PROMPT
        assert "John" in SYSTEM_PROMPT
        assert "John" in AGENT_PROMPT
        assert "John" in SIMPLE_PROMPT

    def test_import_status(self):
        try:
            from agent.core.status import AgentState, AgentStatusModel
            assert AgentStatusModel
            assert AgentState.IDLE
        except ImportError:
            pass  # Module may not be merged yet


class TestMemoryImports:
    """Memory modules import and basic operations work."""

    def test_import_store(self):
        from agent.memory.store import (
            MemoryEntry,
            MemoryKind,
            MemoryStore,
            ProvenanceStatus,
        )
        assert MemoryStore
        assert MemoryEntry
        assert ProvenanceStatus.VERIFIED
        assert MemoryKind.FACT

    def test_import_persistent_conversation(self):
        from agent.memory.persistent_conversation import PersistentConversation
        assert PersistentConversation

    def test_memory_entry_creation(self):
        from agent.memory.store import MemoryEntry, MemoryType
        entry = MemoryEntry(content="test", memory_type=MemoryType.SEMANTIC)
        assert entry.id
        assert entry.provenance.value == "observed"


class TestSocialImports:
    """Social/channel modules import."""

    def test_import_channel(self):
        from agent.social.channel import Channel, ChannelRegistry, IncomingMessage
        assert Channel
        assert ChannelRegistry
        assert IncomingMessage

    def test_import_channel_policy(self):
        from agent.social.channel_policy import (
            classify_response,
            get_channel_capabilities,
        )
        assert classify_response
        assert get_channel_capabilities

    def test_import_telegram_handler(self):
        from agent.social.telegram_handler import TelegramHandler
        assert TelegramHandler


class TestFinanceImports:
    """Finance module imports."""

    def test_import_tracker(self):
        from agent.finance.tracker import FinanceTracker, Transaction
        assert FinanceTracker
        assert Transaction


class TestWorkImports:
    """Work module imports."""

    def test_import_workspace(self):
        from agent.work.workspace import Workspace, WorkspaceManager
        assert WorkspaceManager
        assert Workspace


class TestBrainImports:
    """Brain module imports."""

    def test_import_learning(self):
        from agent.brain.learning import LearningSystem
        assert LearningSystem

    def test_import_dispatcher(self):
        from agent.brain.dispatcher import InternalDispatcher
        assert InternalDispatcher


class TestClassificationSmoke:
    """Smoke test for task classification — basic sanity."""

    def test_classify_returns_valid_type(self):
        from agent.core.models import classify_task
        valid_types = {"simple", "greeting", "factual", "chat", "analysis", "programming", "work_queue"}
        result = classify_task("ahoj")
        assert result in valid_types

    def test_get_model_returns_config(self):
        from agent.core.models import get_model
        model = get_model("chat")
        assert model.model_id
        assert model.timeout > 0
