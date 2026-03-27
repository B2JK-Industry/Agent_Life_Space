"""
Agent Life Space — Task Manager

Deterministic task lifecycle management.
Tasks flow through defined states — no ambiguity.

Task lifecycle:
    CREATED → QUEUED → SCHEDULED → RUNNING → COMPLETED | FAILED | CANCELLED

Features:
    - Priority-based scheduling (algorithmic, no LLM)
    - Dependency tracking (task B waits for task A)
    - Deadline enforcement
    - Persistent task storage (SQLite)
    - Cron-like recurring tasks
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import aiosqlite
import orjson
import structlog

logger = structlog.get_logger(__name__)


class TaskStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"  # Waiting for dependencies


class TaskType(str, Enum):
    ONE_TIME = "one_time"
    RECURRING = "recurring"
    CRON = "cron"


@dataclass
class Task:
    """A unit of work for the agent."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.CREATED
    task_type: TaskType = TaskType.ONE_TIME
    priority: float = 0.5  # 0.0 - 1.0
    importance: float = 0.5
    urgency: float = 0.5
    effort: float = 0.5  # Estimated effort 0-1
    tags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # Task IDs
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    scheduled_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    deadline: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    requires_llm: bool = False
    requires_approval: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    # Recurring task config
    cron_expression: str | None = None  # e.g., "0 */6 * * *"
    recurrence_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "task_type": self.task_type.value,
            "priority": self.priority,
            "importance": self.importance,
            "urgency": self.urgency,
            "effort": self.effort,
            "tags": self.tags,
            "dependencies": self.dependencies,
            "created_at": self.created_at,
            "scheduled_at": self.scheduled_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "deadline": self.deadline,
            "result": self.result,
            "error": self.error,
            "requires_llm": self.requires_llm,
            "requires_approval": self.requires_approval,
            "metadata": self.metadata,
            "cron_expression": self.cron_expression,
            "recurrence_count": self.recurrence_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            description=data.get("description", ""),
            status=TaskStatus(data["status"]),
            task_type=TaskType(data.get("task_type", "one_time")),
            priority=data.get("priority", 0.5),
            importance=data.get("importance", 0.5),
            urgency=data.get("urgency", 0.5),
            effort=data.get("effort", 0.5),
            tags=data.get("tags", []),
            dependencies=data.get("dependencies", []),
            created_at=data.get("created_at", ""),
            scheduled_at=data.get("scheduled_at"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            deadline=data.get("deadline"),
            result=data.get("result"),
            error=data.get("error"),
            requires_llm=data.get("requires_llm", False),
            requires_approval=data.get("requires_approval", False),
            metadata=data.get("metadata", {}),
            cron_expression=data.get("cron_expression"),
            recurrence_count=data.get("recurrence_count", 0),
        )


class TaskManager:
    """
    Manages task lifecycle with persistent storage.
    Scheduling and prioritization are DETERMINISTIC (no LLM).
    """

    def __init__(self, db_path: str = "agent/tasks/tasks.db") -> None:
        if not db_path:
            msg = "db_path cannot be empty"
            raise ValueError(msg)
        self._db_path = db_path
        self._tasks: dict[str, Task] = {}
        self._db: aiosqlite.Connection | None = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize database and load tasks."""
        if self._initialized:
            logger.warning("task_manager_already_initialized")
            return
        self._db = await aiosqlite.connect(self._db_path)
        self._initialized = True
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        await self._db.commit()

        # Load all tasks
        async with self._db.execute("SELECT id, data FROM tasks") as cursor:
            async for row in cursor:
                data = orjson.loads(row[1])
                task = Task.from_dict(data)
                self._tasks[task.id] = task

        logger.info("task_manager_initialized", count=len(self._tasks))

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def create_task(
        self,
        name: str,
        description: str = "",
        priority: float = 0.5,
        importance: float = 0.5,
        urgency: float = 0.5,
        effort: float = 0.5,
        tags: list[str] | None = None,
        dependencies: list[str] | None = None,
        deadline: str | None = None,
        requires_llm: bool = False,
        requires_approval: bool = False,
        task_type: TaskType = TaskType.ONE_TIME,
        cron_expression: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Create a new task."""
        if not name or not name.strip():
            msg = "Task name cannot be empty"
            raise ValueError(msg)

        # Validate dependencies exist
        deps = dependencies or []
        for dep_id in deps:
            if dep_id not in self._tasks:
                msg = f"Dependency task '{dep_id}' does not exist"
                raise ValueError(msg)

        task = Task(
            name=name,
            description=description,
            priority=max(0.0, min(1.0, priority)),
            importance=max(0.0, min(1.0, importance)),
            urgency=max(0.0, min(1.0, urgency)),
            effort=max(0.0, min(1.0, effort)),
            tags=tags or [],
            dependencies=deps,
            deadline=deadline,
            requires_llm=requires_llm,
            requires_approval=requires_approval,
            task_type=task_type,
            cron_expression=cron_expression,
            metadata=metadata or {},
        )

        # Check if blocked by dependencies
        if deps and not self._all_deps_completed(deps):
            task.status = TaskStatus.BLOCKED
        else:
            task.status = TaskStatus.QUEUED

        self._tasks[task.id] = task
        await self._persist(task)

        logger.info(
            "task_created",
            id=task.id,
            name=name,
            status=task.status.value,
            deps=len(deps),
        )
        return task

    def _all_deps_completed(self, dep_ids: list[str]) -> bool:
        """Check if all dependency tasks are completed. DETERMINISTIC."""
        return all(
            self._tasks.get(d, Task()).status == TaskStatus.COMPLETED
            for d in dep_ids
        )

    async def start_task(self, task_id: str) -> Task:
        """Mark a task as running."""
        task = self._get_task(task_id)
        if task.status == TaskStatus.BLOCKED:
            msg = f"Task '{task_id}' is blocked by dependencies"
            raise ValueError(msg)
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC).isoformat()
        await self._persist(task)
        return task

    async def complete_task(
        self, task_id: str, result: dict[str, Any] | None = None
    ) -> Task:
        """Mark a task as completed."""
        task = self._get_task(task_id)
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now(UTC).isoformat()
        task.result = result
        await self._persist(task)

        # Unblock dependent tasks
        await self._check_unblock(task_id)

        logger.info("task_completed", id=task_id, name=task.name)
        return task

    async def fail_task(self, task_id: str, error: str) -> Task:
        """Mark a task as failed."""
        task = self._get_task(task_id)
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now(UTC).isoformat()
        task.error = error
        await self._persist(task)
        logger.error("task_failed", id=task_id, name=task.name, error=error)
        return task

    async def cancel_task(self, task_id: str) -> Task:
        """Cancel a task."""
        task = self._get_task(task_id)
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(UTC).isoformat()
        await self._persist(task)
        return task

    async def _check_unblock(self, completed_id: str) -> None:
        """After a task completes, check if blocked tasks can be unblocked."""
        for task in self._tasks.values():
            if (
                task.status == TaskStatus.BLOCKED
                and completed_id in task.dependencies
                and self._all_deps_completed(task.dependencies)
            ):
                task.status = TaskStatus.QUEUED
                await self._persist(task)
                logger.info("task_unblocked", id=task.id, name=task.name)

    def get_next_task(self) -> Task | None:
        """
        Get the highest-priority queued task. DETERMINISTIC.
        Uses the scoring formula from DecisionEngine logic.
        """
        queued = [
            t for t in self._tasks.values() if t.status == TaskStatus.QUEUED
        ]
        if not queued:
            return None

        # Score and sort (deterministic)
        def score(t: Task) -> float:
            return t.importance * 0.4 + t.urgency * 0.3 + (1 - t.effort) * 0.2 + t.priority * 0.1

        queued.sort(key=score, reverse=True)
        return queued[0]

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        limit: int = 50,
    ) -> list[Task]:
        tasks = list(self._tasks.values())
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        tasks.sort(key=lambda task: task.created_at, reverse=True)
        return tasks[:limit]

    def _get_task(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if not task:
            msg = f"Task '{task_id}' not found"
            raise KeyError(msg)
        return task

    def get_tasks_by_status(self, status: TaskStatus) -> list[Task]:
        return [t for t in self._tasks.values() if t.status == status]

    def get_tasks_by_tag(self, tag: str) -> list[Task]:
        return [t for t in self._tasks.values() if tag in t.tags]

    async def _persist(self, task: Task) -> None:
        """Persist task to database."""
        if self._db:
            data = orjson.dumps(task.to_dict()).decode()
            await self._db.execute(
                "INSERT OR REPLACE INTO tasks (id, data) VALUES (?, ?)",
                (task.id, data),
            )
            await self._db.commit()

    def get_stats(self) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        for task in self._tasks.values():
            s = task.status.value
            status_counts[s] = status_counts.get(s, 0) + 1

        return {
            "total_tasks": len(self._tasks),
            "by_status": status_counts,
        }
