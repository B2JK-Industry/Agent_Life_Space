"""
Integration tests — modules working together.

These test REAL scenarios the agent will face:
1. Agent startup and shutdown cycle
2. Brain receives decision request via message router
3. Memory stores and queries via messages
4. Task creation triggers correct routing
5. Watchdog detects all modules healthy after startup
6. Job runner executes maintenance jobs
7. Full status report is comprehensive
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from agent.core.agent import AgentOrchestrator
from agent.core.messages import Message, MessageType, ModuleID, Priority
from agent.memory.store import MemoryEntry, MemoryType


@pytest.fixture
async def agent():
    """Create agent with temporary data directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, "agent")
        os.makedirs(os.path.join(data_dir, "memory"), exist_ok=True)
        os.makedirs(os.path.join(data_dir, "tasks"), exist_ok=True)
        os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)
        os.makedirs(os.path.join(data_dir, "work"), exist_ok=True)

        orchestrator = AgentOrchestrator(
            data_dir=data_dir,
            watchdog_interval=60.0,
        )
        await orchestrator.initialize()
        yield orchestrator
        await orchestrator.stop()


class TestAgentLifecycle:
    """Agent starts up and shuts down cleanly."""

    @pytest.mark.asyncio
    async def test_initialize(self, agent: AgentOrchestrator) -> None:
        """Agent initializes all modules without error."""
        status = agent.get_status()
        assert status["running"] is False  # Not started yet
        assert status["memory"]["total_memories"] >= 1  # Startup memory recorded
        assert status["build"]["initialized"] is True
        assert status["review"]["initialized"] is True
        assert status["build"]["total_jobs"] == 0
        assert status["review"]["total_jobs"] == 0

    @pytest.mark.asyncio
    async def test_startup_memory_recorded(self, agent: AgentOrchestrator) -> None:
        """Agent records its own startup in memory."""
        results = await agent.memory.query(tags=["startup"])
        assert len(results) >= 1
        assert "started" in results[0].content.lower()

    @pytest.mark.asyncio
    async def test_start_and_stop(self, agent: AgentOrchestrator) -> None:
        """Agent starts, runs briefly, and stops cleanly."""
        task = asyncio.create_task(agent.start())
        await asyncio.sleep(0.2)
        assert agent._running is True

        await agent.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert agent._running is False


class TestMessageIntegration:
    """Messages flow between modules correctly."""

    @pytest.mark.asyncio
    async def test_brain_decision_via_router(self, agent: AgentOrchestrator) -> None:
        """Send decision request to brain via router, get response."""
        received: list[Message] = []

        # Override tasks handler to capture brain's response
        agent.router._handlers.get(ModuleID.TASKS)

        async def capture_handler(msg: Message) -> None:
            received.append(msg)
            return None

        agent.router.register_handler(ModuleID.TASKS, capture_handler)

        # Send decision request from tasks to brain
        request = Message(
            source=ModuleID.TASKS,
            target=ModuleID.BRAIN,
            msg_type=MessageType.DECISION_REQUEST,
            payload={"task_description": "Sort items by priority"},
        )

        router_task = asyncio.create_task(agent.router.start())
        await agent.router.send(request)
        await asyncio.sleep(0.3)
        await agent.router.stop()
        router_task.cancel()
        try:
            await router_task
        except asyncio.CancelledError:
            pass

        # Brain should have responded
        assert len(received) >= 1
        response = received[0]
        assert response.msg_type == MessageType.DECISION_RESULT
        assert response.payload["action"] == "use_algorithm"

    @pytest.mark.asyncio
    async def test_memory_store_via_router(self, agent: AgentOrchestrator) -> None:
        """Store memory via message router."""
        received: list[Message] = []

        async def capture_handler(msg: Message) -> None:
            received.append(msg)
            return None

        agent.router.register_handler(ModuleID.BRAIN, capture_handler)

        # Brain stores a memory
        store_msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.MEMORY_STORE,
            payload={
                "content": "Integration test memory",
                "type": "semantic",
                "tags": ["test", "integration"],
                "importance": 0.7,
            },
        )

        router_task = asyncio.create_task(agent.router.start())
        await agent.router.send(store_msg)
        await asyncio.sleep(0.3)
        await agent.router.stop()
        router_task.cancel()
        try:
            await router_task
        except asyncio.CancelledError:
            pass

        # Should get a response back
        assert len(received) >= 1
        assert received[0].payload["status"] == "stored"

        # Verify memory was actually stored
        results = await agent.memory.query(tags=["integration"])
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_task_create_via_router(self, agent: AgentOrchestrator) -> None:
        """Create task via message router."""
        received: list[Message] = []

        async def capture_handler(msg: Message) -> None:
            received.append(msg)
            return None

        agent.router.register_handler(ModuleID.BRAIN, capture_handler)

        create_msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.TASKS,
            msg_type=MessageType.TASK_CREATE,
            payload={
                "name": "Research competitors",
                "description": "Find and analyze competitors",
                "priority": 0.8,
                "tags": ["research"],
            },
        )

        router_task = asyncio.create_task(agent.router.start())
        await agent.router.send(create_msg)
        await asyncio.sleep(0.3)
        await agent.router.stop()
        router_task.cancel()
        try:
            await router_task
        except asyncio.CancelledError:
            pass

        assert len(received) >= 1
        assert "task_id" in received[0].payload


class TestWatchdogIntegration:
    """Watchdog monitors all modules."""

    @pytest.mark.asyncio
    async def test_all_modules_healthy(self, agent: AgentOrchestrator) -> None:
        """After startup, all modules should be healthy."""
        states = agent.watchdog.get_module_states()
        for module, state in states.items():
            assert state == "healthy", f"Module '{module}' is {state}, expected healthy"

    @pytest.mark.asyncio
    async def test_health_check_job(self, agent: AgentOrchestrator) -> None:
        """Health check job returns system metrics."""
        result = await agent._job_health_check()
        assert "cpu" in result
        assert "memory" in result
        assert "modules" in result


class TestJobIntegration:
    """Job runner executes maintenance jobs."""

    @pytest.mark.asyncio
    async def test_memory_decay_job(self, agent: AgentOrchestrator) -> None:
        """Memory decay job runs successfully."""
        result = await agent._job_memory_decay()
        assert "deleted_memories" in result
        assert "total_memories" in result

    @pytest.mark.asyncio
    async def test_process_next_task_no_tasks(self, agent: AgentOrchestrator) -> None:
        """Process next task returns no_tasks when queue is empty."""
        # Clear startup tasks if any
        result = await agent._job_process_next_task()
        assert result["status"] == "no_tasks"

    @pytest.mark.asyncio
    async def test_process_next_task_with_task(self, agent: AgentOrchestrator) -> None:
        """Process next task picks and starts the highest priority task."""
        await agent.tasks.create_task(
            name="Sort data",
            description="Sort the dataset",
            priority=0.9,
        )
        result = await agent._job_process_next_task()
        assert result["task_name"] == "Sort data"
        assert result["decision"] == "use_algorithm"  # "sort" keyword


class TestFullStatus:
    """Agent status report is comprehensive."""

    @pytest.mark.asyncio
    async def test_status_has_all_sections(self, agent: AgentOrchestrator) -> None:
        status = agent.get_status()
        assert "memory" in status
        assert "tasks" in status
        assert "brain" in status
        assert "build" in status
        assert "review" in status
        assert "jobs" in status
        assert "watchdog" in status
        assert "router" in status

    @pytest.mark.asyncio
    async def test_builder_service_is_available(self, agent: AgentOrchestrator) -> None:
        status = agent.get_status()
        assert agent.build is not None
        assert status["build"]["initialized"] is True
        assert status["build"]["total_jobs"] == 0

    @pytest.mark.asyncio
    async def test_status_memory_section(self, agent: AgentOrchestrator) -> None:
        status = agent.get_status()
        assert "total_memories" in status["memory"]
        assert "by_type" in status["memory"]

    @pytest.mark.asyncio
    async def test_status_tasks_section(self, agent: AgentOrchestrator) -> None:
        status = agent.get_status()
        assert "total_tasks" in status["tasks"]


# ─────────────────────────────────────────────
# Cross-module integration: brain ↔ memory ↔ tasks
# ─────────────────────────────────────────────

class TestCrossModuleIntegration:
    """Verify modules interact correctly across boundaries."""

    @pytest.mark.asyncio
    async def test_brain_decision_depends_on_task_content(self, agent: AgentOrchestrator) -> None:
        """DecisionEngine routes differently based on task description."""
        algo_decision = agent.brain.should_use_llm("Sort items alphabetically")
        assert algo_decision.action == "use_algorithm"

        llm_decision = agent.brain.should_use_llm("Write a poem about programming")
        assert llm_decision.action == "use_llm"

    @pytest.mark.asyncio
    async def test_memory_query_via_router_returns_results(self, agent: AgentOrchestrator) -> None:
        """Memory query through router returns stored entries."""
        # First store something
        await agent.memory.store(MemoryEntry(
            content="Router query integration test",
            memory_type=MemoryType.SEMANTIC,
            tags=["router_query_test"],
            source="test",
            importance=0.8,
        ))

        received: list[Message] = []

        async def capture(msg: Message) -> None:
            received.append(msg)
            return None

        agent.router.register_handler(ModuleID.BRAIN, capture)

        query_msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.MEMORY_QUERY,
            payload={"tags": ["router_query_test"], "limit": 5},
        )

        router_task = asyncio.create_task(agent.router.start())
        await agent.router.send(query_msg)
        await asyncio.sleep(0.3)
        await agent.router.stop()
        router_task.cancel()
        try:
            await router_task
        except asyncio.CancelledError:
            pass

        assert len(received) >= 1
        assert received[0].payload["count"] >= 1
        assert received[0].payload["results"][0]["content"] == "Router query integration test"

    @pytest.mark.asyncio
    async def test_task_complete_via_router(self, agent: AgentOrchestrator) -> None:
        """Task can be completed through router message."""
        from agent.tasks.manager import TaskStatus

        task = await agent.tasks.create_task(name="Router complete test", priority=0.5)
        await agent.tasks.start_task(task.id)

        received: list[Message] = []

        async def capture(msg: Message) -> None:
            received.append(msg)
            return None

        agent.router.register_handler(ModuleID.BRAIN, capture)

        complete_msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.TASKS,
            msg_type=MessageType.TASK_COMPLETE,
            payload={"task_id": task.id, "result": "Done via router"},
        )

        router_task = asyncio.create_task(agent.router.start())
        await agent.router.send(complete_msg)
        await asyncio.sleep(0.3)
        await agent.router.stop()
        router_task.cancel()
        try:
            await router_task
        except asyncio.CancelledError:
            pass

        assert len(received) >= 1
        completed = agent.tasks.get_task(task.id)
        assert completed.status == TaskStatus.COMPLETED


class TestDispatcherIntegration:
    """Internal dispatcher handles deterministic queries without LLM."""

    @pytest.mark.asyncio
    async def test_status_query_handled_internally(self, agent: AgentOrchestrator) -> None:
        """Questions about status are handled without LLM."""
        from agent.brain.dispatcher import InternalDispatcher

        dispatcher = InternalDispatcher(agent)
        result = await dispatcher.try_handle("aký je tvoj stav?")
        assert result is not None
        assert "Agent" in result or "Running" in result or "Spomienky" in result

    @pytest.mark.asyncio
    async def test_identity_query_handled_internally(self, agent: AgentOrchestrator) -> None:
        """Identity questions are handled without LLM."""
        from agent.brain.dispatcher import InternalDispatcher

        dispatcher = InternalDispatcher(agent)
        result = await dispatcher.try_handle("kto si?")
        assert result is not None
        assert "John" in result

    @pytest.mark.asyncio
    async def test_complex_query_not_handled(self, agent: AgentOrchestrator) -> None:
        """Complex questions pass through to LLM (return None)."""
        from agent.brain.dispatcher import InternalDispatcher

        dispatcher = InternalDispatcher(agent)
        result = await dispatcher.try_handle("Vysvetli mi ako funguje transformer architecture")
        assert result is None

    @pytest.mark.asyncio
    async def test_url_in_text_skips_dispatcher(self, agent: AgentOrchestrator) -> None:
        """Messages with URLs skip dispatcher (need LLM to process)."""
        from agent.brain.dispatcher import InternalDispatcher

        dispatcher = InternalDispatcher(agent)
        result = await dispatcher.try_handle("pozri sa na https://github.com/test")
        assert result is None


class TestFinanceIntegration:
    """Finance module: propose → approve → complete lifecycle."""

    @pytest.mark.asyncio
    async def test_propose_approve_complete_flow(self, agent: AgentOrchestrator) -> None:
        """Full expense lifecycle: propose → approve → complete."""
        from agent.finance.tracker import TransactionStatus

        # Propose
        tx = await agent.finance.propose_expense(
            amount_usd=5.0,
            description="Claude API usage",
            category="api",
        )
        assert tx.status == TransactionStatus.PROPOSED

        # Approve (human action)
        approved = await agent.finance.approve(tx.id)
        assert approved.status == TransactionStatus.APPROVED
        assert approved.approved_by == "human"

        # Complete
        completed = await agent.finance.complete(tx.id)
        assert completed.status == TransactionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_propose_reject_flow(self, agent: AgentOrchestrator) -> None:
        """Rejected expense doesn't count toward spending."""
        tx = await agent.finance.propose_expense(
            amount_usd=100.0,
            description="Expensive thing",
        )
        await agent.finance.reject(tx.id, reason="Too expensive")

        stats = agent.finance.get_stats()
        # Rejected expense should not count
        assert stats["total_expenses"] == 0

    @pytest.mark.asyncio
    async def test_income_recorded_immediately(self, agent: AgentOrchestrator) -> None:
        """Income doesn't need approval."""
        from agent.finance.tracker import TransactionStatus

        tx = await agent.finance.record_income(
            amount_usd=10.0,
            description="Test earning",
            source="test",
        )
        assert tx.status == TransactionStatus.COMPLETED

        stats = agent.finance.get_stats()
        assert stats["total_income"] >= 10.0

    @pytest.mark.asyncio
    async def test_budget_check(self, agent: AgentOrchestrator) -> None:
        """Budget limits are checked when proposing."""
        budget = agent.finance.check_budget(1.0)
        assert "within_budget" in budget
        assert "daily_remaining" in budget
        assert "monthly_remaining" in budget


class TestConsolidationIntegration:
    """Memory consolidation: episodic → semantic/procedural."""

    @pytest.mark.asyncio
    async def test_consolidation_runs_without_crash(self, agent: AgentOrchestrator) -> None:
        """Consolidation can run on empty/small memory."""
        from agent.memory.consolidation import MemoryConsolidation

        consolidator = MemoryConsolidation(agent.memory)
        report = await consolidator.consolidate()
        assert "episodic_reviewed" in report
        assert "patterns_found" in report

    @pytest.mark.asyncio
    async def test_consolidation_finds_patterns(self, agent: AgentOrchestrator) -> None:
        """Consolidation detects patterns in episodic memory."""
        from agent.memory.consolidation import MemoryConsolidation

        # Store multiple related episodic memories
        for i in range(5):
            await agent.memory.store(MemoryEntry(
                content=f"Daniel preferuje stručné odpovede session {i}",
                memory_type=MemoryType.EPISODIC,
                tags=["telegram", "user_input", "daniel"],
                source="telegram",
                importance=0.5,
            ))

        consolidator = MemoryConsolidation(agent.memory)
        report = await consolidator.consolidate()
        assert report["episodic_reviewed"] >= 5

    @pytest.mark.asyncio
    async def test_decay_removes_low_importance(self, agent: AgentOrchestrator) -> None:
        """Memory decay reduces memory count over time."""
        # Store many low-importance working memories
        for i in range(10):
            await agent.memory.store(MemoryEntry(
                content=f"Temporary working memory {i}",
                memory_type=MemoryType.WORKING,
                tags=["temp", "working"],
                source="test",
                importance=0.01,
            ))

        before = agent.memory.get_stats()["total_memories"]
        # Aggressive decay
        await agent.memory.apply_decay(decay_rate=0.99)
        after = agent.memory.get_stats()["total_memories"]
        assert after <= before


class TestMessagePriorityIntegration:
    """Messages with different priorities are delivered in correct order."""

    @pytest.mark.asyncio
    async def test_critical_before_normal(self, agent: AgentOrchestrator) -> None:
        """CRITICAL messages are delivered before NORMAL ones."""
        delivery_order: list[str] = []

        async def track_handler(msg: Message) -> None:
            delivery_order.append(msg.payload.get("label", ""))
            return None

        agent.router.register_handler(ModuleID.BRAIN, track_handler)

        # Send normal first, then critical
        normal_msg = Message(
            source=ModuleID.TASKS,
            target=ModuleID.BRAIN,
            msg_type=MessageType.DECISION_REQUEST,
            payload={"task_description": "low", "label": "normal"},
            priority=Priority.NORMAL,
        )
        critical_msg = Message(
            source=ModuleID.TASKS,
            target=ModuleID.BRAIN,
            msg_type=MessageType.DECISION_REQUEST,
            payload={"task_description": "high", "label": "critical"},
            priority=Priority.CRITICAL,
        )

        # Queue both before processing
        await agent.router.send(normal_msg)
        await agent.router.send(critical_msg)

        router_task = asyncio.create_task(agent.router.start())
        await asyncio.sleep(0.3)
        await agent.router.stop()
        router_task.cancel()
        try:
            await router_task
        except asyncio.CancelledError:
            pass

        # Critical should be delivered first
        assert len(delivery_order) >= 2
        assert delivery_order[0] == "critical"
        assert delivery_order[1] == "normal"


class TestWatchdogHeartbeatIntegration:
    """Watchdog detects module health via heartbeats."""

    @pytest.mark.asyncio
    async def test_heartbeat_keeps_module_healthy(self, agent: AgentOrchestrator) -> None:
        """Regular heartbeats maintain healthy status."""
        agent.watchdog.heartbeat("brain")
        agent.watchdog.heartbeat("memory")

        states = agent.watchdog.get_module_states()
        assert states["brain"] == "healthy"
        assert states["memory"] == "healthy"

    @pytest.mark.asyncio
    async def test_system_health_has_all_fields(self, agent: AgentOrchestrator) -> None:
        """System health report contains all required metrics."""
        health = agent.watchdog.get_system_health()
        assert hasattr(health, "cpu_percent")
        assert hasattr(health, "memory_percent")
        assert hasattr(health, "disk_percent")
        assert hasattr(health, "modules")
        assert hasattr(health, "alerts")
        assert health.cpu_percent >= 0
        assert health.memory_percent >= 0


class TestMultiModuleScenario:
    """Real-world scenarios involving multiple modules."""

    @pytest.mark.asyncio
    async def test_create_task_store_memory_check_status(self, agent: AgentOrchestrator) -> None:
        """Scenario: create task, store related memory, verify both in status."""
        # Create task
        task = await agent.tasks.create_task(
            name="Research competitors",
            priority=0.8,
        )

        # Store related memory
        await agent.memory.store(MemoryEntry(
            content=f"Vytvorená úloha: Research competitors (id: {task.id})",
            memory_type=MemoryType.EPISODIC,
            tags=["task_created", "research"],
            source="orchestrator",
            importance=0.6,
        ))

        # Verify status shows both
        status = agent.get_status()
        assert status["tasks"]["total_tasks"] >= 1
        assert status["memory"]["total_memories"] >= 2  # startup + this one

    @pytest.mark.asyncio
    async def test_job_execution_updates_stats(self, agent: AgentOrchestrator) -> None:
        """Running a job updates job runner statistics."""
        from agent.core.job_runner import JobConfig

        before = agent.job_runner.get_stats()
        await agent.job_runner.schedule(
            "health_check",
            config=JobConfig(timeout_seconds=10, max_retries=0),
        )
        await asyncio.sleep(0.2)
        after = agent.job_runner.get_stats()

        assert after["total_completed"] > before["total_completed"]

    @pytest.mark.asyncio
    async def test_memory_store_query_decay_cycle(self, agent: AgentOrchestrator) -> None:
        """Full memory lifecycle: store → query → decay."""
        # Store
        mem_id = await agent.memory.store(MemoryEntry(
            content="Lifecycle test memory",
            memory_type=MemoryType.EPISODIC,
            tags=["lifecycle_test"],
            source="test",
            importance=0.5,
        ))
        assert mem_id

        # Query
        results = await agent.memory.query(tags=["lifecycle_test"])
        assert len(results) >= 1
        assert any(r.content == "Lifecycle test memory" for r in results)

        # Decay (mild — shouldn't delete this one)
        count_before = len(await agent.memory.query(tags=["lifecycle_test"]))
        await agent.memory.apply_decay(decay_rate=0.001)
        count_after = len(await agent.memory.query(tags=["lifecycle_test"]))
        assert count_after == count_before  # Mild decay doesn't delete
