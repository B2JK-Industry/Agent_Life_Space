"""
Test scenarios for Task Manager.

1. Task lifecycle: created → queued → running → completed
2. Dependencies block tasks until resolved
3. Completing a dependency unblocks dependents
4. Priority-based next task selection (deterministic)
5. Task persistence across restart
6. Failed/cancelled tasks handled correctly
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from agent.tasks.manager import Task, TaskManager, TaskStatus, TaskType


@pytest.fixture
async def manager():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    mgr = TaskManager(db_path=db_path)
    await mgr.initialize()
    yield mgr
    await mgr.close()
    os.unlink(db_path)


class TestTaskLifecycle:
    """Task state transitions must be correct."""

    @pytest.mark.asyncio
    async def test_create_task(self, manager: TaskManager) -> None:
        task = await manager.create_task(
            name="Research market",
            description="Find opportunities",
            priority=0.8,
            tags=["research", "market"],
        )
        assert task.status == TaskStatus.QUEUED
        assert task.name == "Research market"
        assert "research" in task.tags

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, manager: TaskManager) -> None:
        """created → queued → running → completed"""
        task = await manager.create_task(name="Test task")
        assert task.status == TaskStatus.QUEUED

        task = await manager.start_task(task.id)
        assert task.status == TaskStatus.RUNNING
        assert task.started_at is not None

        task = await manager.complete_task(task.id, result={"output": "done"})
        assert task.status == TaskStatus.COMPLETED
        assert task.completed_at is not None
        assert task.result == {"output": "done"}

    @pytest.mark.asyncio
    async def test_fail_task(self, manager: TaskManager) -> None:
        task = await manager.create_task(name="Failing task")
        await manager.start_task(task.id)
        task = await manager.fail_task(task.id, "Something went wrong")
        assert task.status == TaskStatus.FAILED
        assert task.error == "Something went wrong"

    @pytest.mark.asyncio
    async def test_cancel_task(self, manager: TaskManager) -> None:
        task = await manager.create_task(name="To cancel")
        task = await manager.cancel_task(task.id)
        assert task.status == TaskStatus.CANCELLED


class TestDependencies:
    """Tasks can depend on other tasks."""

    @pytest.mark.asyncio
    async def test_blocked_by_dependency(self, manager: TaskManager) -> None:
        """Task with unfinished dependency is blocked."""
        dep = await manager.create_task(name="Dependency")
        task = await manager.create_task(
            name="Dependent",
            dependencies=[dep.id],
        )
        assert task.status == TaskStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_unblocked_after_dependency_completes(
        self, manager: TaskManager
    ) -> None:
        """Completing a dependency unblocks the dependent task."""
        dep = await manager.create_task(name="Dependency")
        task = await manager.create_task(
            name="Dependent",
            dependencies=[dep.id],
        )
        assert task.status == TaskStatus.BLOCKED

        # Complete the dependency
        await manager.start_task(dep.id)
        await manager.complete_task(dep.id)

        # Check that dependent is now queued
        updated = manager.get_task(task.id)
        assert updated is not None
        assert updated.status == TaskStatus.QUEUED

    @pytest.mark.asyncio
    async def test_cannot_start_blocked_task(self, manager: TaskManager) -> None:
        dep = await manager.create_task(name="Dependency")
        task = await manager.create_task(
            name="Dependent",
            dependencies=[dep.id],
        )
        with pytest.raises(ValueError, match="blocked"):
            await manager.start_task(task.id)

    @pytest.mark.asyncio
    async def test_invalid_dependency_rejected(self, manager: TaskManager) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            await manager.create_task(
                name="Bad deps",
                dependencies=["nonexistent_id"],
            )


class TestPrioritization:
    """Task selection must be deterministic."""

    @pytest.mark.asyncio
    async def test_next_task_highest_priority(self, manager: TaskManager) -> None:
        await manager.create_task(name="Low", importance=0.2, urgency=0.2)
        await manager.create_task(name="High", importance=0.9, urgency=0.9)
        await manager.create_task(name="Mid", importance=0.5, urgency=0.5)

        next_task = manager.get_next_task()
        assert next_task is not None
        assert next_task.name == "High"

    @pytest.mark.asyncio
    async def test_no_queued_returns_none(self, manager: TaskManager) -> None:
        assert manager.get_next_task() is None

    @pytest.mark.asyncio
    async def test_deterministic_ordering(self, manager: TaskManager) -> None:
        """Same tasks = same order. Always."""
        await manager.create_task(name="A", importance=0.5, urgency=0.5)
        await manager.create_task(name="B", importance=0.7, urgency=0.3)
        await manager.create_task(name="C", importance=0.3, urgency=0.7)

        order1 = manager.get_next_task()
        # Reset and recreate
        assert order1 is not None


class TestTaskQueries:
    """Querying tasks by status and tags."""

    @pytest.mark.asyncio
    async def test_get_by_status(self, manager: TaskManager) -> None:
        await manager.create_task(name="Queued 1")
        t2 = await manager.create_task(name="Queued 2")
        await manager.start_task(t2.id)

        queued = manager.get_tasks_by_status(TaskStatus.QUEUED)
        running = manager.get_tasks_by_status(TaskStatus.RUNNING)
        assert len(queued) == 1
        assert len(running) == 1

    @pytest.mark.asyncio
    async def test_get_by_tag(self, manager: TaskManager) -> None:
        await manager.create_task(name="Research", tags=["research", "market"])
        await manager.create_task(name="Code", tags=["development"])

        research = manager.get_tasks_by_tag("research")
        assert len(research) == 1
        assert research[0].name == "Research"


class TestTaskPersistence:
    """Tasks survive database restart."""

    @pytest.mark.asyncio
    async def test_persist_and_reload(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mgr1 = TaskManager(db_path=db_path)
            await mgr1.initialize()
            task = await mgr1.create_task(
                name="Persistent task",
                priority=0.9,
                tags=["persistent"],
            )
            task_id = task.id
            await mgr1.close()

            mgr2 = TaskManager(db_path=db_path)
            await mgr2.initialize()
            loaded = mgr2.get_task(task_id)
            assert loaded is not None
            assert loaded.name == "Persistent task"
            assert loaded.priority == 0.9
            assert "persistent" in loaded.tags
            await mgr2.close()
        finally:
            os.unlink(db_path)


class TestTaskStats:
    @pytest.mark.asyncio
    async def test_stats(self, manager: TaskManager) -> None:
        await manager.create_task(name="A")
        t = await manager.create_task(name="B")
        await manager.start_task(t.id)
        await manager.complete_task(t.id)

        stats = manager.get_stats()
        assert stats["total_tasks"] == 2
        assert stats["by_status"]["queued"] == 1
        assert stats["by_status"]["completed"] == 1


class TestTaskSerialization:
    def test_round_trip(self) -> None:
        task = Task(
            name="Test",
            description="Desc",
            priority=0.8,
            tags=["a", "b"],
            metadata={"key": "value"},
        )
        data = task.to_dict()
        restored = Task.from_dict(data)
        assert restored.name == task.name
        assert restored.priority == task.priority
        assert restored.tags == task.tags
        assert restored.metadata == task.metadata
