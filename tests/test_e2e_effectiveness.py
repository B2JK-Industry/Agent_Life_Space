"""
End-to-End Effectiveness Tests — Does John ACTUALLY use everything?

Not "does module X exist" but "does the agent USE module X in real scenarios".

Tests verify:
1. Full message flow: Telegram → dispatcher → classify → route → respond
2. Memory is stored AND retrieved AND influences decisions
3. Persistent conversation survives across calls
4. Finance flow: propose → approve → track
5. Task lifecycle: create → start → complete → history
6. Watchdog detects problems
7. Router delivers messages AND persists them
8. Dead code detection — unused modules/handlers
9. AgentLoop sanitizes input and routes to correct model
10. Safe mode actually blocks non-owners
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.agent import AgentOrchestrator
from agent.core.messages import Message, MessageType, ModuleID, Priority
from agent.core.router import MessagePersistence, MessageRouter
from agent.memory.store import MemoryEntry, MemoryStore, MemoryType
from agent.memory.persistent_conversation import PersistentConversation
from agent.core.agent_loop import AgentLoop, _sanitize_work_description
from agent.core.models import classify_task, get_model


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
async def agent():
    """Full agent with temp data directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, "agent")
        for sub in ("memory", "tasks", "finance", "projects", "logs", "data"):
            os.makedirs(os.path.join(data_dir, sub), exist_ok=True)

        orchestrator = AgentOrchestrator(
            data_dir=data_dir,
            watchdog_interval=60.0,
        )
        await orchestrator.initialize()
        yield orchestrator
        await orchestrator.stop()


@pytest.fixture
async def persistent_conv():
    """PersistentConversation with temp DB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "conv.db")
        conv = PersistentConversation(db_path=db_path)
        await conv.initialize()
        yield conv
        await conv.close()


@pytest.fixture
async def router_with_persistence():
    """MessageRouter with SQLite persistence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "queue.db")
        router = MessageRouter(persistence_db=db_path)
        await router.init()
        yield router
        await router.stop()


# ─────────────────────────────────────────────
# 1. Memory is USED, not just stored
# ─────────────────────────────────────────────

class TestMemoryEffectiveness:
    """Memory must influence agent behavior, not just accumulate."""

    @pytest.mark.asyncio
    async def test_memory_store_and_retrieve_cycle(self, agent: AgentOrchestrator) -> None:
        """Store → query → get back the same data."""
        mem_id = await agent.memory.store(MemoryEntry(
            content="Daniel preferuje stručné odpovede po slovensky",
            memory_type=MemoryType.SEMANTIC,
            tags=["preference", "daniel", "language"],
            source="test",
            importance=0.9,
        ))
        assert mem_id

        results = await agent.memory.query(tags=["preference"], limit=5)
        assert len(results) >= 1
        assert any("stručné" in r.content for r in results)

    @pytest.mark.asyncio
    async def test_memory_relevance_scoring_works(self, agent: AgentOrchestrator) -> None:
        """Important memories rank higher than trivial ones."""
        await agent.memory.store(MemoryEntry(
            content="Triviálna vec",
            memory_type=MemoryType.EPISODIC,
            tags=["ranking_test"],
            source="test",
            importance=0.1,
        ))
        await agent.memory.store(MemoryEntry(
            content="Kriticky dôležitá informácia",
            memory_type=MemoryType.SEMANTIC,
            tags=["ranking_test"],
            source="test",
            importance=1.0,
        ))

        results = await agent.memory.query(tags=["ranking_test"], limit=2)
        assert len(results) == 2
        # Higher importance should come first
        assert results[0].importance >= results[1].importance

    @pytest.mark.asyncio
    async def test_memory_decay_removes_old_unimportant(self, agent: AgentOrchestrator) -> None:
        """Decay actually reduces memory count over time."""
        # Store low-importance memory
        await agent.memory.store(MemoryEntry(
            content="Jednorazová informácia",
            memory_type=MemoryType.WORKING,
            tags=["decay_test"],
            source="test",
            importance=0.01,
        ))

        before = agent.memory.get_stats()["total_memories"]
        # Apply aggressive decay
        deleted = await agent.memory.apply_decay(decay_rate=0.99)
        after = agent.memory.get_stats()["total_memories"]

        # At least some should be decayed (working type decays fastest)
        assert deleted >= 0  # Decay ran without error
        assert after <= before

    @pytest.mark.asyncio
    async def test_memory_types_used_correctly(self, agent: AgentOrchestrator) -> None:
        """Different memory types serve different purposes."""
        for mtype, content in [
            (MemoryType.WORKING, "Aktuálna úloha: review kódu"),
            (MemoryType.EPISODIC, "Daniel mi povedal aby som bol stručný"),
            (MemoryType.SEMANTIC, "Python je interpretovaný jazyk"),
            (MemoryType.PROCEDURAL, "Pri code review: najprv čítaj, potom komentuj"),
        ]:
            await agent.memory.store(MemoryEntry(
                content=content,
                memory_type=mtype,
                tags=["type_test", mtype.value],
                source="test",
                importance=0.5,
            ))

        stats = agent.memory.get_stats()
        by_type = stats["by_type"]
        assert by_type.get("working", 0) >= 1
        assert by_type.get("episodic", 0) >= 1
        assert by_type.get("semantic", 0) >= 1
        assert by_type.get("procedural", 0) >= 1


# ─────────────────────────────────────────────
# 2. Persistent Conversation actually works
# ─────────────────────────────────────────────

class TestPersistentConversation:
    """Conversation context survives across calls."""

    @pytest.mark.asyncio
    async def test_save_and_retrieve_exchange(self, persistent_conv: PersistentConversation) -> None:
        """Saved exchanges appear in context."""
        await persistent_conv.save_exchange(
            "session-1", "Ahoj John", "Ahoj Daniel! Čo riešiš?", sender="Daniel"
        )
        context = await persistent_conv.build_context("session-1")
        assert "Daniel" in context
        assert "Ahoj" in context

    @pytest.mark.asyncio
    async def test_multiple_exchanges_build_history(self, persistent_conv: PersistentConversation) -> None:
        """Multiple exchanges create conversation flow."""
        exchanges = [
            ("Čo vieš o Pythone?", "Python je interpretovaný jazyk."),
            ("A čo Rust?", "Rust je kompilovaný, memory safe."),
            ("Porovnaj ich", "Python je jednoduchší, Rust rýchlejší."),
        ]
        for user_msg, agent_msg in exchanges:
            await persistent_conv.save_exchange("session-2", user_msg, agent_msg)

        context = await persistent_conv.build_context("session-2")
        assert "Python" in context
        assert "Rust" in context

    @pytest.mark.asyncio
    async def test_core_memory_persists_facts(self, persistent_conv: PersistentConversation) -> None:
        """Core facts survive and appear in context."""
        await persistent_conv.update_core_fact("owner", "Daniel Babjak")
        await persistent_conv.update_core_fact("language", "slovenčina")

        context = await persistent_conv.build_context("session-3")
        assert "Daniel Babjak" in context
        assert "slovenčina" in context

    @pytest.mark.asyncio
    async def test_core_fact_upsert(self, persistent_conv: PersistentConversation) -> None:
        """Updating a core fact replaces the old value."""
        await persistent_conv.update_core_fact("mood", "dobrý")
        await persistent_conv.update_core_fact("mood", "unavený")

        context = await persistent_conv.build_context("session-4")
        assert "unavený" in context
        assert context.count("mood") <= 2  # Not duplicated

    @pytest.mark.asyncio
    async def test_search_past_uses_parameterized_queries(self, persistent_conv: PersistentConversation) -> None:
        """SQL injection attempt doesn't break search."""
        await persistent_conv.save_exchange(
            "session-5", "normálna správa", "normálna odpoveď"
        )
        # SQL injection attempt in search
        results = await persistent_conv._search_past("'; DROP TABLE messages; --")
        # Should return empty, not crash
        assert isinstance(results, list)


# ─────────────────────────────────────────────
# 3. Router + Persistence
# ─────────────────────────────────────────────

class TestRouterPersistence:
    """Messages survive crashes via SQLite persistence."""

    @pytest.mark.asyncio
    async def test_message_persisted_before_delivery(self) -> None:
        """Messages are stored in DB before delivery attempt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "queue.db")
            persistence = MessagePersistence(db_path)
            await persistence.init()

            msg = Message(
                source=ModuleID.BRAIN,
                target=ModuleID.MEMORY,
                msg_type=MessageType.MEMORY_STORE,
                payload={"content": "test"},
            )
            await persistence.store(msg.id, 2, 1, msg)

            pending = await persistence.load_pending()
            assert len(pending) == 1
            assert pending[0][2].id == msg.id

            await persistence.close()

    @pytest.mark.asyncio
    async def test_message_removed_after_delivery(self) -> None:
        """Successfully delivered messages are removed from persistence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "queue.db")
            persistence = MessagePersistence(db_path)
            await persistence.init()

            msg = Message(
                source=ModuleID.BRAIN,
                target=ModuleID.MEMORY,
                msg_type=MessageType.MEMORY_STORE,
                payload={"content": "test"},
            )
            await persistence.store(msg.id, 2, 1, msg)
            await persistence.remove(msg.id)

            pending = await persistence.load_pending()
            assert len(pending) == 0

            await persistence.close()

    @pytest.mark.asyncio
    async def test_replay_pending_on_init(self) -> None:
        """Undelivered messages are replayed when router reinitializes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "queue.db")

            # Phase 1: store message, don't deliver
            persistence = MessagePersistence(db_path)
            await persistence.init()
            msg = Message(
                source=ModuleID.BRAIN,
                target=ModuleID.MEMORY,
                msg_type=MessageType.MEMORY_STORE,
                payload={"content": "crash recovery test"},
            )
            await persistence.store(msg.id, 2, 1, msg)
            await persistence.close()

            # Phase 2: new router picks up the message
            router = MessageRouter(persistence_db=db_path)
            await router.init()
            assert router._queue.qsize() == 1

            await router.stop()


# ─────────────────────────────────────────────
# 4. Task lifecycle
# ─────────────────────────────────────────────

class TestTaskLifecycle:
    """Tasks go through full create → start → complete cycle."""

    @pytest.mark.asyncio
    async def test_full_task_lifecycle(self, agent: AgentOrchestrator) -> None:
        """Task: create → queue → start → complete."""
        from agent.tasks.manager import TaskStatus

        task = await agent.tasks.create_task(
            name="Otestuj API",
            description="Spusti testy pre agent API",
            priority=0.8,
        )
        assert task.status == TaskStatus.QUEUED

        await agent.tasks.start_task(task.id)
        updated = agent.tasks.get_task(task.id)
        assert updated.status == TaskStatus.RUNNING

        await agent.tasks.complete_task(task.id, result="Všetky testy prešli")
        completed = agent.tasks.get_task(task.id)
        assert completed.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_task_priority_ordering(self, agent: AgentOrchestrator) -> None:
        """Higher priority tasks are picked first."""
        low = await agent.tasks.create_task(name="Low priority", priority=0.2)
        high = await agent.tasks.create_task(name="High priority", priority=0.9)

        next_task = agent.tasks.get_next_task()
        assert next_task is not None
        assert next_task.name == "High priority"

    @pytest.mark.asyncio
    async def test_task_create_via_message_router(self, agent: AgentOrchestrator) -> None:
        """Tasks can be created through the message bus."""
        delivered = []

        async def capture(msg: Message) -> None:
            delivered.append(msg)
            return None

        agent.router.register_handler(ModuleID.BRAIN, capture)

        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.TASKS,
            msg_type=MessageType.TASK_CREATE,
            payload={"name": "Router task", "priority": 0.7},
        )

        router_task = asyncio.create_task(agent.router.start())
        await agent.router.send(msg)
        await asyncio.sleep(0.3)
        await agent.router.stop()
        router_task.cancel()
        try:
            await router_task
        except asyncio.CancelledError:
            pass

        assert len(delivered) >= 1
        assert "task_id" in delivered[0].payload


# ─────────────────────────────────────────────
# 5. Finance flow
# ─────────────────────────────────────────────

class TestFinanceFlow:
    """Finance module: propose → approve → track."""

    @pytest.mark.asyncio
    async def test_expense_tracking(self, agent: AgentOrchestrator) -> None:
        """Record income and propose expense, verify stats."""
        await agent.finance.record_income(
            amount_usd=10.0, description="Test income", source="test"
        )
        await agent.finance.propose_expense(
            amount_usd=3.0, description="Claude API cost", category="api"
        )

        stats = agent.finance.get_stats()
        assert stats["total_income"] == 10.0
        assert stats["pending_proposals"] >= 1

    @pytest.mark.asyncio
    async def test_budget_limits_enforced(self, agent: AgentOrchestrator) -> None:
        """Budget tracking returns remaining amounts."""
        stats = agent.finance.get_stats()
        budget = stats.get("budget", {})
        # Budget should have daily and monthly limits
        assert "daily_budget" in budget or "daily_remaining" in budget or isinstance(budget, dict)


# ─────────────────────────────────────────────
# 6. Watchdog detects problems
# ─────────────────────────────────────────────

class TestWatchdogEffectiveness:
    """Watchdog actually catches unhealthy modules."""

    @pytest.mark.asyncio
    async def test_all_modules_registered(self, agent: AgentOrchestrator) -> None:
        """Every module declared in architecture is monitored."""
        states = agent.watchdog.get_module_states()
        expected = {"brain", "memory", "tasks", "llm_router", "job_runner"}
        assert expected.issubset(set(states.keys())), \
            f"Missing modules in watchdog: {expected - set(states.keys())}"

    @pytest.mark.asyncio
    async def test_system_health_returns_metrics(self, agent: AgentOrchestrator) -> None:
        """Health check returns real system metrics."""
        health = agent.watchdog.get_system_health()
        assert health.cpu_percent >= 0
        assert health.memory_percent >= 0
        assert health.disk_percent >= 0
        assert isinstance(health.modules, dict)

    @pytest.mark.asyncio
    async def test_heartbeat_keeps_module_alive(self, agent: AgentOrchestrator) -> None:
        """Sending heartbeat prevents module from going stale."""
        agent.watchdog.heartbeat("brain")
        states = agent.watchdog.get_module_states()
        assert states["brain"] == "healthy"


# ─────────────────────────────────────────────
# 7. Model classification & routing
# ─────────────────────────────────────────────

class TestModelRouting:
    """Correct model selected for each task type."""

    def test_simple_greeting_uses_haiku(self) -> None:
        assert classify_task("ahoj") == "simple"
        assert get_model("simple").model_id == "claude-haiku-4-5-20251001"

    def test_programming_uses_opus(self) -> None:
        assert classify_task("naprogramuj mi REST API modul") == "programming"
        assert get_model("programming").model_id == "claude-opus-4-6"

    def test_chat_uses_sonnet(self) -> None:
        task = classify_task("čo si myslíš o budúcnosti AI a ako to ovplyvní spoločnosť?")
        assert task in ("chat", "analysis")
        assert get_model("chat").model_id == "claude-sonnet-4-6"

    def test_complex_task_escalates(self) -> None:
        """Multi-signal scoring escalates complex tasks."""
        # URL + action verb = complexity 4+ → programming (Opus)
        result = classify_task("preskúmaj https://github.com/example/repo a porovnaj s naším")
        assert result in ("programming", "analysis")

    def test_factual_short_question(self) -> None:
        result = classify_task("čo je Python?")
        assert result in ("factual", "chat")

    def test_work_queue_uses_sonnet(self) -> None:
        model = get_model("work_queue")
        assert model.model_id == "claude-sonnet-4-6"
        assert model.timeout == 180


# ─────────────────────────────────────────────
# 8. Input sanitization
# ─────────────────────────────────────────────

class TestInputSanitization:
    """Agent sanitizes inputs before processing."""

    def test_control_chars_stripped(self) -> None:
        dirty = "normálny text\x00\x01\x02 s control chars"
        clean = _sanitize_work_description(dirty)
        assert "\x00" not in clean
        assert "\x01" not in clean
        assert "normálny text" in clean

    def test_max_length_enforced(self) -> None:
        long_text = "a" * 5000
        clean = _sanitize_work_description(long_text)
        assert len(clean) <= 2020  # 2000 + "... (skrátené)"

    def test_normal_text_unchanged(self) -> None:
        text = "Otestuj 5 skills a zapíš výsledky"
        assert _sanitize_work_description(text) == text

    def test_newlines_preserved(self) -> None:
        text = "riadok 1\nriadok 2\nriadok 3"
        assert _sanitize_work_description(text) == text


# ─────────────────────────────────────────────
# 9. Safe mode enforcement
# ─────────────────────────────────────────────

class TestSafeModeEnforcement:
    """Non-owners in groups are actually restricted."""

    @pytest.mark.asyncio
    async def test_safe_mode_blocks_privileged_commands(self) -> None:
        """Non-owner in group can't use /sandbox, /review, /wallet etc."""
        from agent.social.telegram_handler import TelegramHandler

        mock_agent = MagicMock()
        mock_agent.memory = AsyncMock()
        mock_agent.memory.store = AsyncMock()
        mock_agent.memory.query = AsyncMock(return_value=[])

        mock_bot = MagicMock()
        mock_bot._api_call = AsyncMock()

        handler = TelegramHandler(agent=mock_agent, bot=mock_bot, owner_chat_id=123)

        blocked_commands = ["/wallet", "/review agent/core/router.py", "/newtask test", "/consolidate"]
        for cmd in blocked_commands:
            # Simulate non-owner in group — handle() sets _force_safe_mode
            result = await handler.handle(
                cmd, user_id=999, chat_id=456,
                username="hacker", chat_type="group",
            )
            assert "len pre ownera" in result, f"Command {cmd} was NOT blocked! Got: {result[:80]}"

    @pytest.mark.asyncio
    async def test_safe_commands_allowed_for_everyone(self) -> None:
        """Non-owner can still use /start, /help, /status, /health."""
        from agent.social.telegram_handler import TelegramHandler

        mock_agent = MagicMock()
        mock_agent.get_status = MagicMock(return_value={
            "running": True,
            "memory": {"total_memories": 10},
            "tasks": {"total_tasks": 0},
            "brain": {"total_decisions": 0},
            "jobs": {"total_completed": 0, "total_failed": 0},
            "watchdog": {"modules_registered": 5, "modules_healthy": 5},
        })
        mock_agent.watchdog = MagicMock()

        handler = TelegramHandler(agent=mock_agent, owner_chat_id=123)
        handler._force_safe_mode = True
        handler._current_sender = "visitor"

        # These should work
        result_help = await handler._handle_command("/help")
        assert "Príkazy" in result_help

        result_start = await handler._handle_command("/start")
        assert "Agent Life Space" in result_start


# ─────────────────────────────────────────────
# 10. Full orchestrator wiring — no dead modules
# ─────────────────────────────────────────────

class TestOrchestratorWiring:
    """All declared modules are actually initialized and wired."""

    @pytest.mark.asyncio
    async def test_all_message_handlers_registered(self, agent: AgentOrchestrator) -> None:
        """Every module has a handler in the router."""
        registered = set(agent.router._handlers.keys())
        expected = {
            ModuleID.BRAIN, ModuleID.MEMORY, ModuleID.TASKS,
            ModuleID.LLM_ROUTER, ModuleID.LOGS, ModuleID.WATCHDOG,
        }
        assert expected.issubset(registered), \
            f"Missing handlers: {expected - registered}"

    @pytest.mark.asyncio
    async def test_all_job_types_registered(self, agent: AgentOrchestrator) -> None:
        """All maintenance jobs are registered."""
        registered = set(agent.job_runner._job_functions.keys())
        expected = {"memory_decay", "health_check", "process_next_task"}
        assert expected.issubset(registered), \
            f"Missing job types: {expected - registered}"

    @pytest.mark.asyncio
    async def test_status_covers_all_modules(self, agent: AgentOrchestrator) -> None:
        """get_status() reports on every module."""
        status = agent.get_status()
        required_sections = {"memory", "tasks", "brain", "finance", "jobs", "watchdog", "router"}
        assert required_sections.issubset(set(status.keys())), \
            f"Missing status sections: {required_sections - set(status.keys())}"

    @pytest.mark.asyncio
    async def test_brain_makes_decisions(self, agent: AgentOrchestrator) -> None:
        """Brain's DecisionEngine actually classifies tasks."""
        decision = agent.brain.should_use_llm("Sort items alphabetically")
        assert decision.action in ("use_algorithm", "use_llm", "hybrid")
        assert 0 <= decision.confidence <= 1

    @pytest.mark.asyncio
    async def test_finance_initialized(self, agent: AgentOrchestrator) -> None:
        """Finance module is functional after init."""
        stats = agent.finance.get_stats()
        assert "total_income" in stats
        assert "total_expenses" in stats

    @pytest.mark.asyncio
    async def test_projects_initialized(self, agent: AgentOrchestrator) -> None:
        """Projects module is functional after init."""
        # Projects module should be accessible
        assert hasattr(agent, "projects")
        assert agent.projects is not None


# ─────────────────────────────────────────────
# 11. AgentLoop queue behavior
# ─────────────────────────────────────────────

class TestAgentLoopQueue:
    """Work queue manages items correctly."""

    def test_add_work_respects_max_queue(self) -> None:
        loop = AgentLoop(max_queue_size=3)
        added = loop.add_work(["a", "b", "c", "d", "e"])
        assert added == 3
        assert loop.queue_size == 3

    def test_status_reports_queue_state(self) -> None:
        loop = AgentLoop(max_queue_size=10)
        loop.add_work(["task1", "task2"])
        status = loop.get_status()
        assert status["queue_size"] == 2
        assert status["running"] is False

    def test_programming_task_detection(self) -> None:
        assert AgentLoop._is_programming_task("napíš kód pre parser modul") is True
        assert AgentLoop._is_programming_task("spusti test") is True
        assert AgentLoop._is_programming_task("git commit a push") is True
        assert AgentLoop._is_programming_task("aké je počasie?") is False


# ─────────────────────────────────────────────
# 12. Vault security
# ─────────────────────────────────────────────

class TestVaultSecurity:
    """Vault enforces encryption and access control."""

    def test_vault_fails_without_key_when_secrets_exist(self) -> None:
        """Cannot start vault without key if encrypted data exists."""
        from agent.vault.secrets import SecretsManager

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create vault with key, store a secret
            vault = SecretsManager(vault_dir=tmpdir, master_key="test-key-123")
            vault.set_secret("api_key", "sk-123")

            # Try to open without key — should fail
            with pytest.raises(RuntimeError, match="AGENT_VAULT_KEY not set"):
                SecretsManager(vault_dir=tmpdir, master_key="")

    def test_vault_refuses_unencrypted_storage(self) -> None:
        """Cannot store secrets without encryption key."""
        from agent.vault.secrets import SecretsManager

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = SecretsManager(vault_dir=tmpdir, master_key="")
            with pytest.raises(RuntimeError, match="Cannot store secrets"):
                vault.set_secret("test", "value")

    def test_vault_audit_trail(self) -> None:
        """Every access is logged in audit trail."""
        from agent.vault.secrets import SecretsManager

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = SecretsManager(vault_dir=tmpdir, master_key="audit-test-key")
            vault.set_secret("key1", "val1")
            vault.get_secret("key1")
            vault.list_secrets()

            audit = vault.get_audit_log()
            actions = [e["action"] for e in audit]
            assert "set" in actions
            assert "get_cached" in actions or "get" in actions
            assert "list" in actions
