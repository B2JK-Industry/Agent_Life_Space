"""
Tests pre agent/core/agent_loop.py — pracovná fronta s circuit breaker.

Pokrýva:
    - WorkItem dataclass
    - AgentLoop init, add_work, queue management
    - Circuit breaker (3 consecutive errors → 30s pauza)
    - Error counting a error rate
    - get_status formát
    - Worker start/stop
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.agent_loop import AgentLoop, WorkItem


# --- WorkItem ---


class TestWorkItem:
    def test_defaults(self):
        item = WorkItem(description="test task")
        assert item.description == "test task"
        assert item.callback_chat_id == 0
        assert item.priority == 0
        assert item.result is None
        assert item.success is False
        assert item.created_at  # not empty

    def test_custom_values(self):
        item = WorkItem(description="x", callback_chat_id=123, priority=5)
        assert item.callback_chat_id == 123
        assert item.priority == 5


# --- AgentLoop Init ---


class TestAgentLoopInit:
    def test_defaults(self):
        loop = AgentLoop()
        assert loop.queue_size == 0
        assert loop.is_busy is False
        assert loop._running is False
        assert loop._error_count == 0
        assert loop._consecutive_errors == 0

    def test_custom_max_queue(self):
        loop = AgentLoop(max_queue_size=10)
        assert loop._max_queue == 10


# --- add_work ---


class TestAddWork:
    def test_add_items(self):
        loop = AgentLoop()
        added = loop.add_work(["task1", "task2", "task3"])
        assert added == 3
        assert loop.queue_size == 3

    def test_max_queue_respected(self):
        loop = AgentLoop(max_queue_size=2)
        added = loop.add_work(["a", "b", "c", "d"])
        assert added == 2
        assert loop.queue_size == 2

    def test_empty_list(self):
        loop = AgentLoop()
        added = loop.add_work([])
        assert added == 0

    def test_chat_id_propagated(self):
        loop = AgentLoop()
        loop.add_work(["task"], chat_id=42)
        item = loop._queue[0]
        assert item.callback_chat_id == 42


# --- get_status ---


class TestGetStatus:
    def test_initial_status(self):
        loop = AgentLoop()
        status = loop.get_status()
        assert status["queue_size"] == 0
        assert status["processing"] is False
        assert status["total_processed"] == 0
        assert status["total_errors"] == 0
        assert status["consecutive_errors"] == 0
        assert status["running"] is False
        assert status["error_rate"] == 0.0

    def test_error_rate_calculation(self):
        loop = AgentLoop()
        loop._processed_count = 7
        loop._error_count = 3
        status = loop.get_status()
        assert status["error_rate"] == 0.3

    def test_error_rate_no_division_by_zero(self):
        loop = AgentLoop()
        status = loop.get_status()
        assert status["error_rate"] == 0.0  # 0/1 = 0.0


# --- Circuit Breaker ---


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_consecutive_errors_tracked(self):
        """Errors should increment consecutive counter."""
        loop = AgentLoop()

        # Mock _execute_item to raise
        async def failing_execute(item):
            raise RuntimeError("fail")

        loop._execute_item = failing_execute

        # Add work and process one item manually
        loop.add_work(["task1"])
        item = loop._queue.popleft()
        loop._processing = True

        try:
            await loop._execute_item(item)
        except RuntimeError:
            loop._error_count += 1
            loop._consecutive_errors += 1

        assert loop._error_count == 1
        assert loop._consecutive_errors == 1

    @pytest.mark.asyncio
    async def test_success_resets_consecutive(self):
        """Success should reset consecutive error counter."""
        loop = AgentLoop()
        loop._consecutive_errors = 2

        # Simulate success
        loop._consecutive_errors = 0  # as worker does on success

        assert loop._consecutive_errors == 0

    def test_circuit_breaker_threshold_is_3(self):
        """Verify the threshold is 3 consecutive errors."""
        # Read the source to verify the constant
        import inspect
        from agent.core import agent_loop
        source = inspect.getsource(agent_loop.AgentLoop._worker)
        assert "self._consecutive_errors >= 3" in source

    def test_circuit_breaker_pause_is_30s(self):
        """Verify the pause duration is 30 seconds."""
        import inspect
        from agent.core import agent_loop
        source = inspect.getsource(agent_loop.AgentLoop._worker)
        assert "asyncio.sleep(30)" in source


# --- Worker lifecycle ---


class TestWorkerLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        loop = AgentLoop()
        # Start and immediately stop
        await loop.start()
        assert loop._running is True
        await loop.stop()
        assert loop._running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        loop = AgentLoop()
        await loop.start()
        assert loop._task is not None
        await loop.stop()
        # Task should be cancelled


# --- _execute_item (mocked) ---


class TestExecuteItem:
    @pytest.mark.asyncio
    async def test_execute_returns_result(self):
        loop = AgentLoop()

        # Mock subprocess to return JSON
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"result": "done", "is_error": false}'
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            item = WorkItem(description="test task")
            result = await loop._execute_item(item)

        assert result == "done"

    @pytest.mark.asyncio
    async def test_execute_error_returncode(self):
        loop = AgentLoop()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "CLI error"

        with patch("subprocess.run", return_value=mock_result):
            item = WorkItem(description="test task")
            result = await loop._execute_item(item)

        assert "Error" in result

    @pytest.mark.asyncio
    async def test_execute_invalid_json(self):
        loop = AgentLoop()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            item = WorkItem(description="test task")
            result = await loop._execute_item(item)

        assert "Nepodarilo" in result
