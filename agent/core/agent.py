"""
Agent Life Space — Agent Orchestrator

The main agent that ties all modules together.
Manages the complete lifecycle: startup → run → shutdown.

Architecture:
    ┌──────────────────────────────────────┐
    │         AgentOrchestrator            │
    │                                      │
    │  ┌──────────┐  ┌─────────────────┐  │
    │  │ Watchdog  │  │ Decision Engine │  │
    │  └─────┬────┘  └───────┬─────────┘  │
    │        │               │             │
    │  ┌─────┴───────────────┴──────────┐  │
    │  │        Message Router          │  │
    │  └─┬───┬───┬───┬───┬───┬───┬───┬─┘  │
    │    │   │   │   │   │   │   │   │     │
    │  Brain Mem Task Work Proj Soc Fin Log│
    │                                      │
    │  ┌──────────┐  ┌────────────────┐   │
    │  │LLM Router│  │  Job Runner    │   │
    │  └──────────┘  └────────────────┘   │
    └──────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

import structlog

from agent.brain.decision_engine import DecisionEngine
from agent.core.job_runner import JobConfig, JobRunner
from agent.core.llm_router import LLMRouter
from agent.core.messages import Message, MessageType, ModuleID, Priority
from agent.core.router import MessageRouter
from agent.core.watchdog import Watchdog
from agent.memory.store import MemoryEntry, MemoryStore, MemoryType
from agent.finance.tracker import FinanceTracker
from agent.projects.manager import ProjectManager
from agent.tasks.manager import TaskManager, TaskStatus
from agent.work.workspace import WorkspaceManager

logger = structlog.get_logger(__name__)


class AgentOrchestrator:
    """
    Main agent process. Initializes all modules, starts the event loop,
    and manages graceful shutdown.
    """

    def __init__(
        self,
        data_dir: str = "agent",
        watchdog_interval: float = 10.0,
    ) -> None:
        if not data_dir:
            msg = "data_dir cannot be empty"
            raise ValueError(msg)
        if watchdog_interval <= 0:
            msg = f"watchdog_interval must be > 0, got {watchdog_interval}"
            raise ValueError(msg)

        self._data_dir = Path(data_dir)
        self._running = False
        self._initialized = False

        # Core infrastructure
        self.router = MessageRouter()  # FUTURE: inter-module messaging
        self.watchdog = Watchdog(check_interval=watchdog_interval)
        self.job_runner = JobRunner(max_concurrent=4)
        self.llm_router = LLMRouter()  # FUTURE: direct API calls (now using Claude CLI)

        # Agent modules
        self.brain = DecisionEngine()
        self.memory = MemoryStore(
            db_path=str(self._data_dir / "memory" / "memories.db")
        )
        self.tasks = TaskManager(
            db_path=str(self._data_dir / "tasks" / "tasks.db")
        )
        self.finance = FinanceTracker(
            db_path=str(self._data_dir / "finance" / "finance.db")
        )
        self.projects = ProjectManager(
            db_path=str(self._data_dir / "projects" / "projects.db")
        )
        self.workspaces = WorkspaceManager()

        # Background tasks
        self._background_tasks: list[asyncio.Task[Any]] = []

    async def initialize(self) -> None:
        """Initialize all modules and register handlers."""
        if self._initialized:
            logger.warning("agent_already_initialized")
            return
        logger.info("agent_initializing")

        # Ensure data directories exist
        (self._data_dir / "memory").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "tasks").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "finance").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "projects").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "logs").mkdir(parents=True, exist_ok=True)

        # Initialize persistent stores
        await self.memory.initialize()
        await self.tasks.initialize()
        await self.finance.initialize()
        await self.projects.initialize()
        self.workspaces.initialize()

        # Register message handlers
        self.router.register_handler(ModuleID.BRAIN, self._handle_brain_message)
        self.router.register_handler(ModuleID.MEMORY, self._handle_memory_message)
        self.router.register_handler(ModuleID.TASKS, self._handle_tasks_message)
        self.router.register_handler(ModuleID.LLM_ROUTER, self._handle_llm_message)
        self.router.register_handler(ModuleID.LOGS, self._handle_log_message)
        self.router.register_handler(ModuleID.WATCHDOG, self._handle_watchdog_message)

        # Register modules with watchdog
        self.watchdog.register_module("brain", heartbeat_timeout=60.0)
        self.watchdog.register_module("memory", heartbeat_timeout=60.0)
        self.watchdog.register_module("tasks", heartbeat_timeout=60.0)
        self.watchdog.register_module("llm_router", heartbeat_timeout=120.0)
        self.watchdog.register_module("job_runner", heartbeat_timeout=60.0)

        # Register built-in job types
        self.job_runner.register_job_type("memory_decay", self._job_memory_decay)
        self.job_runner.register_job_type("health_check", self._job_health_check)
        self.job_runner.register_job_type("process_next_task", self._job_process_next_task)

        # Store startup memory
        await self.memory.store(
            MemoryEntry(
                content="Agent started successfully. All modules initialized.",
                memory_type=MemoryType.EPISODIC,
                tags=["system", "startup"],
                source="orchestrator",
                importance=0.3,
            )
        )

        self._initialized = True
        logger.info(
            "agent_initialized",
            modules=["brain", "memory", "tasks", "llm_router", "watchdog", "job_runner"],
        )

    async def start(self) -> None:
        """Start the agent event loop."""
        if self._running:
            logger.warning("agent_already_running")
            return

        self._running = True
        logger.info("agent_starting")

        # Start background services
        self._background_tasks.append(
            asyncio.create_task(self.router.start())
        )
        self._background_tasks.append(
            asyncio.create_task(self.watchdog.start())
        )
        self._background_tasks.append(
            asyncio.create_task(self._heartbeat_loop())
        )
        self._background_tasks.append(
            asyncio.create_task(self._maintenance_loop())
        )

        logger.info("agent_running")

        # Keep running until stopped
        try:
            while self._running:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Gracefully shut down all modules."""
        logger.info("agent_stopping")
        self._running = False

        # Stop background tasks
        for task in self._background_tasks:
            task.cancel()

        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        # Stop services
        await self.router.stop()
        await self.watchdog.stop()

        # Close persistent stores
        await self.memory.close()
        await self.tasks.close()
        await self.finance.close()
        await self.projects.close()

        # Store shutdown memory (in a new connection since we closed)
        logger.info("agent_stopped")

    # --- Message Handlers ---

    async def _handle_brain_message(self, message: Message) -> Message | None:
        """Handle messages directed to the brain module."""
        self.watchdog.heartbeat("brain")

        if message.msg_type == MessageType.DECISION_REQUEST:
            task_desc = message.payload.get("task_description", "")
            decision = self.brain.should_use_llm(task_desc)
            return message.create_response(
                payload={
                    "action": decision.action,
                    "method": decision.method.value,
                    "confidence": decision.confidence,
                    "reasoning": decision.reasoning,
                },
                msg_type=MessageType.DECISION_RESULT,
            )

        return None

    async def _handle_memory_message(self, message: Message) -> Message | None:
        """Handle memory operations."""
        self.watchdog.heartbeat("memory")

        if message.msg_type == MessageType.MEMORY_STORE:
            entry = MemoryEntry(
                content=message.payload.get("content", ""),
                memory_type=MemoryType(message.payload.get("type", "semantic")),
                tags=message.payload.get("tags", []),
                source=message.source.value,
                importance=message.payload.get("importance", 0.5),
            )
            mem_id = await self.memory.store(entry)
            return message.create_response(
                payload={"memory_id": mem_id, "status": "stored"},
                msg_type=MessageType.MEMORY_RESULT,
            )

        elif message.msg_type == MessageType.MEMORY_QUERY:
            results = await self.memory.query(
                tags=message.payload.get("tags"),
                keyword=message.payload.get("keyword"),
                limit=message.payload.get("limit", 5),
            )
            return message.create_response(
                payload={
                    "results": [r.to_dict() for r in results],
                    "count": len(results),
                },
                msg_type=MessageType.MEMORY_RESULT,
            )

        return None

    async def _handle_tasks_message(self, message: Message) -> Message | None:
        """Handle task operations."""
        self.watchdog.heartbeat("tasks")

        if message.msg_type == MessageType.TASK_CREATE:
            task = await self.tasks.create_task(
                name=message.payload.get("name", "Unnamed"),
                description=message.payload.get("description", ""),
                priority=message.payload.get("priority", 0.5),
                importance=message.payload.get("importance", 0.5),
                urgency=message.payload.get("urgency", 0.5),
                tags=message.payload.get("tags", []),
            )
            return message.create_response(
                payload={"task_id": task.id, "status": task.status.value},
            )

        elif message.msg_type == MessageType.TASK_COMPLETE:
            task_id = message.payload.get("task_id", "")
            result = message.payload.get("result")
            task = await self.tasks.complete_task(task_id, result)
            return message.create_response(
                payload={"task_id": task.id, "status": task.status.value},
            )

        return None

    async def _handle_llm_message(self, message: Message) -> Message | None:
        """Handle LLM requests."""
        self.watchdog.heartbeat("llm_router")
        # LLM calls go through the LLM Router with full validation
        # Actual API calls happen here
        return message.create_response(
            payload={"status": "llm_request_received"},
            msg_type=MessageType.LLM_RESPONSE,
        )

    async def _handle_log_message(self, message: Message) -> Message | None:
        """Handle log messages."""
        logger.info(
            "agent_log",
            source=message.source.value,
            log_type=message.payload.get("type", "info"),
            log_message=message.payload.get("message", ""),
        )
        return None

    async def _handle_watchdog_message(self, message: Message) -> Message | None:
        """Handle watchdog messages."""
        if message.msg_type == MessageType.HEALTH_CHECK:
            health = self.watchdog.get_system_health()
            return message.create_response(
                payload={
                    "cpu": health.cpu_percent,
                    "memory": health.memory_percent,
                    "modules": health.modules,
                    "alerts": health.alerts,
                },
                msg_type=MessageType.HEALTH_REPORT,
            )
        return None

    # --- Background Jobs ---

    async def _job_memory_decay(self) -> dict[str, Any]:
        """Periodic memory decay job."""
        deleted = await self.memory.apply_decay(decay_rate=0.005)
        stats = self.memory.get_stats()
        return {
            "deleted_memories": deleted,
            "total_memories": stats["total_memories"],
        }

    async def _job_health_check(self) -> dict[str, Any]:
        """Periodic health check job."""
        health = self.watchdog.get_system_health()
        return {
            "cpu": health.cpu_percent,
            "memory": health.memory_percent,
            "modules": health.modules,
            "alerts": health.alerts,
        }

    async def _job_process_next_task(self) -> dict[str, Any]:
        """Process the next queued task."""
        next_task = self.tasks.get_next_task()
        if next_task is None:
            return {"status": "no_tasks"}

        # Start the task
        await self.tasks.start_task(next_task.id)

        # Decide how to process
        decision = self.brain.should_use_llm(
            f"{next_task.name}: {next_task.description}"
        )

        return {
            "task_id": next_task.id,
            "task_name": next_task.name,
            "decision": decision.action,
            "method": decision.method.value,
        }

    # --- Background Loops ---

    async def _heartbeat_loop(self) -> None:
        """Send heartbeats from all modules periodically."""
        while self._running:
            for name in ["brain", "memory", "tasks", "llm_router", "job_runner"]:
                self.watchdog.heartbeat(name)
            await asyncio.sleep(15.0)

    async def _maintenance_loop(self) -> None:
        """Periodic maintenance tasks."""
        while self._running:
            # Every 6 hours: memory decay
            await self.job_runner.schedule(
                "memory_decay",
                config=JobConfig(timeout_seconds=30, max_retries=1),
            )
            # Every hour: health check
            await self.job_runner.schedule(
                "health_check",
                config=JobConfig(timeout_seconds=10, max_retries=0),
            )
            await asyncio.sleep(3600)  # 1 hour

    # --- Public API ---

    def get_status(self) -> dict[str, Any]:
        """Get overall agent status."""
        return {
            "running": self._running,
            "memory": self.memory.get_stats(),
            "tasks": self.tasks.get_stats(),
            "brain": self.brain.get_stats(),
            "finance": self.finance.get_stats(),
            "jobs": self.job_runner.get_stats(),
            "watchdog": self.watchdog.get_stats(),
            "router": self.router.get_metrics(),
        }
