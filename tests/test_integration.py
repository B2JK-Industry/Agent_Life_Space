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
        original_handler = agent.router._handlers.get(ModuleID.TASKS)

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
        assert "jobs" in status
        assert "watchdog" in status
        assert "router" in status

    @pytest.mark.asyncio
    async def test_status_memory_section(self, agent: AgentOrchestrator) -> None:
        status = agent.get_status()
        assert "total_memories" in status["memory"]
        assert "by_type" in status["memory"]

    @pytest.mark.asyncio
    async def test_status_tasks_section(self, agent: AgentOrchestrator) -> None:
        status = agent.get_status()
        assert "total_tasks" in status["tasks"]
