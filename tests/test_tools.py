"""
Tests for tool definitions, ToolExecutor, and ToolUseLoop.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.tool_policy import ToolExecutionContext
from agent.core.tools import AGENT_TOOLS, get_tool_names


class TestToolDefinitions:
    """Tool schemas are valid and complete."""

    def test_all_tools_have_required_fields(self):
        for tool in AGENT_TOOLS:
            assert "name" in tool, f"Tool missing name: {tool}"
            assert "description" in tool, f"Tool {tool['name']} missing description"
            assert "input_schema" in tool, f"Tool {tool['name']} missing input_schema"
            assert tool["input_schema"]["type"] == "object"

    def test_tool_names_unique(self):
        names = get_tool_names()
        assert len(names) == len(set(names)), "Duplicate tool names found"

    def test_expected_tools_present(self):
        names = get_tool_names()
        expected = [
            "store_memory", "query_memory", "create_task", "list_tasks",
            "run_code", "run_tests", "web_fetch", "check_health",
            "get_status", "search_knowledge",
        ]
        for name in expected:
            assert name in names, f"Expected tool '{name}' not found"

    def test_run_code_supports_multiple_languages(self):
        run_code = next(t for t in AGENT_TOOLS if t["name"] == "run_code")
        languages = run_code["input_schema"]["properties"]["language"]["enum"]
        assert "python" in languages
        assert "node" in languages
        assert "bash" in languages


class TestToolExecutor:
    """ToolExecutor maps tool calls to agent methods."""

    @pytest.fixture
    def executor(self):
        from agent.core.tool_executor import ToolExecutor

        # Mock agent
        agent = MagicMock()
        agent.memory = AsyncMock()
        agent.memory.store = AsyncMock(return_value="mem_123")
        agent.memory.query = AsyncMock(return_value=[])
        agent.tasks = AsyncMock()
        agent.tasks.create_task = AsyncMock(return_value=MagicMock(id="task_1", name="test"))
        agent.tasks.get_stats = MagicMock(return_value={"total_tasks": 0})
        agent.watchdog = MagicMock()
        agent.watchdog.get_system_health = MagicMock(return_value=MagicMock(
            cpu_percent=25.0, memory_percent=40.0, disk_percent=50.0,
            modules={"brain": "healthy"}, alerts=[],
        ))
        agent.get_status = MagicMock(return_value={"running": True})

        # Mock sandbox
        sandbox = AsyncMock()

        return ToolExecutor(agent=agent, sandbox=sandbox)

    @pytest.mark.asyncio
    async def test_store_memory(self, executor):
        result = await executor.execute("store_memory", {
            "content": "test fact",
            "memory_type": "semantic",
            "tags": ["test"],
        })
        assert result["status"] == "stored"
        assert "memory_id" in result

    @pytest.mark.asyncio
    async def test_query_memory(self, executor):
        result = await executor.execute("query_memory", {"keyword": "test"})
        assert "count" in result
        assert "results" in result

    @pytest.mark.asyncio
    async def test_create_task(self, executor):
        result = await executor.execute("create_task", {
            "name": "Do something",
            "priority": 0.8,
        })
        assert result["status"] == "created"
        assert "task_id" in result

    @pytest.mark.asyncio
    async def test_check_health(self, executor):
        result = await executor.execute("check_health", {})
        assert "cpu_percent" in result
        assert "modules" in result

    @pytest.mark.asyncio
    async def test_get_status(self, executor):
        result = await executor.execute("get_status", {})
        assert "running" in result

    @pytest.mark.asyncio
    async def test_risky_tool_blocked_in_safe_mode(self, executor):
        result = await executor.execute(
            "run_code",
            {"code": "print('hi')"},
            context=ToolExecutionContext(is_owner=False, safe_mode=True, channel_type="telegram"),
        )
        assert result["blocked"] is True
        assert result["risk_level"] == "high"
        assert "safe mode" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, executor):
        result = await executor.execute("nonexistent_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_stats_tracking(self, executor):
        await executor.execute("check_health", {})
        await executor.execute("get_status", {})
        stats = executor.get_stats()
        assert stats["total_calls"] == 2
        assert stats["errors"] == 0
        assert stats["blocked"] == 0


class TestToolUseLoop:
    """ToolUseLoop handles multi-turn LLM conversations with tool calls."""

    @pytest.fixture
    def mock_provider(self):
        provider = AsyncMock()
        provider.supports_tools.return_value = True
        return provider

    @pytest.fixture
    def mock_executor(self):
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value={"status": "ok", "data": "test"})
        return executor

    @pytest.mark.asyncio
    async def test_simple_text_response(self, mock_provider, mock_executor):
        """LLM responds with text only — no tool calls needed."""
        from agent.core.llm_provider import GenerateResponse
        from agent.core.tool_loop import ToolUseLoop

        mock_provider.generate = AsyncMock(return_value=GenerateResponse(
            text="Hello!",
            success=True,
            model="test",
            input_tokens=10,
            output_tokens=5,
        ))

        loop = ToolUseLoop(mock_provider, mock_executor)
        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
        )

        assert result.success
        assert result.text == "Hello!"
        assert result.turns == 1
        assert len(result.tool_calls) == 0

    @pytest.mark.asyncio
    async def test_single_tool_call(self, mock_provider, mock_executor):
        """LLM calls one tool, then responds with text."""
        from agent.core.llm_provider import GenerateResponse
        from agent.core.tool_loop import ToolUseLoop

        # First call: tool_use
        # Second call: text response
        mock_provider.generate = AsyncMock(side_effect=[
            GenerateResponse(
                text="",
                success=True,
                model="test",
                tool_calls=[{
                    "id": "tc_1",
                    "name": "check_health",
                    "input": {},
                }],
            ),
            GenerateResponse(
                text="System is healthy!",
                success=True,
                model="test",
            ),
        ])

        loop = ToolUseLoop(mock_provider, mock_executor)
        result = await loop.run(
            messages=[{"role": "user", "content": "how is the system?"}],
        )

        assert result.success
        assert result.text == "System is healthy!"
        assert result.turns == 2
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "check_health"
        mock_executor.execute.assert_called_once_with("check_health", {}, context=None)

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self, mock_provider, mock_executor):
        """LLM calls multiple tools in one turn."""
        from agent.core.llm_provider import GenerateResponse
        from agent.core.tool_loop import ToolUseLoop

        mock_provider.generate = AsyncMock(side_effect=[
            GenerateResponse(
                text="",
                success=True,
                model="test",
                tool_calls=[
                    {"id": "tc_1", "name": "check_health", "input": {}},
                    {"id": "tc_2", "name": "get_status", "input": {}},
                ],
            ),
            GenerateResponse(
                text="All good!",
                success=True,
                model="test",
            ),
        ])

        loop = ToolUseLoop(mock_provider, mock_executor)
        result = await loop.run(
            messages=[{"role": "user", "content": "full report"}],
        )

        assert result.success
        assert len(result.tool_calls) == 2
        assert mock_executor.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_context_forwarded(self, mock_provider, mock_executor):
        """Execution context is forwarded to ToolExecutor for policy checks."""
        from agent.core.llm_provider import GenerateResponse
        from agent.core.tool_loop import ToolUseLoop

        mock_provider.generate = AsyncMock(side_effect=[
            GenerateResponse(
                text="",
                success=True,
                model="test",
                tool_calls=[{"id": "tc_1", "name": "check_health", "input": {}}],
            ),
            GenerateResponse(
                text="done",
                success=True,
                model="test",
            ),
        ])

        loop = ToolUseLoop(mock_provider, mock_executor)
        context = ToolExecutionContext(is_owner=False, safe_mode=True, channel_type="telegram")
        await loop.run(
            messages=[{"role": "user", "content": "status"}],
            tool_context=context,
        )

        mock_executor.execute.assert_called_once_with("check_health", {}, context=context)

    @pytest.mark.asyncio
    async def test_max_turns_limit(self, mock_provider, mock_executor):
        """Loop stops after max_turns even if LLM keeps calling tools."""
        from agent.core.llm_provider import GenerateResponse
        from agent.core.tool_loop import ToolUseLoop

        # Always return tool call (infinite loop)
        mock_provider.generate = AsyncMock(return_value=GenerateResponse(
            text="",
            success=True,
            model="test",
            tool_calls=[{"id": "tc_1", "name": "check_health", "input": {}}],
        ))

        loop = ToolUseLoop(mock_provider, mock_executor, max_turns=3)
        result = await loop.run(
            messages=[{"role": "user", "content": "loop forever"}],
        )

        assert result.turns == 3
        assert len(result.tool_calls) == 3

    @pytest.mark.asyncio
    async def test_provider_error_stops_loop(self, mock_provider, mock_executor):
        """Provider error terminates the loop."""
        from agent.core.llm_provider import GenerateResponse
        from agent.core.tool_loop import ToolUseLoop

        mock_provider.generate = AsyncMock(return_value=GenerateResponse(
            text="",
            success=False,
            error="API timeout",
        ))

        loop = ToolUseLoop(mock_provider, mock_executor)
        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
        )

        assert not result.success
        assert "timeout" in result.text.lower()


class TestSandboxExecutor:
    """SandboxExecutor provides high-level sandbox API."""

    def test_execution_result_to_dict(self):
        from agent.core.sandbox_executor import ExecutionResult

        result = ExecutionResult(
            success=True,
            output="Hello",
            language="python",
            test_passed=3,
            test_failed=1,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["test_passed"] == 3
        assert d["language"] == "python"


class TestChannelAbstraction:
    """Channel interface and registry work correctly."""

    def test_incoming_message_defaults(self):
        from agent.social.channel import IncomingMessage

        msg = IncomingMessage(
            text="hello",
            sender_id="123",
            sender_name="Daniel",
            channel_type="telegram",
            chat_id="456",
        )
        assert msg.is_owner is False
        assert msg.is_group is False

    def test_outgoing_message(self):
        from agent.social.channel import OutgoingMessage

        msg = OutgoingMessage(text="response", chat_id="456")
        assert msg.text == "response"

    def test_channel_registry(self):
        from agent.social.channel import Channel, ChannelRegistry

        class MockChannel(Channel):
            def __init__(self):
                self._started = False

            async def start(self):
                self._started = True

            async def stop(self):
                self._started = False

            async def send(self, message):
                return True

            def on_message(self, callback):
                pass

            @property
            def channel_type(self):
                return "test"

        registry = ChannelRegistry()
        channel = MockChannel()
        registry.register(channel)

        assert "test" in registry.active_channels
        assert registry.get_channel("test") is channel
        assert registry.get_channel("nonexistent") is None

    @pytest.mark.asyncio
    async def test_registry_start_all(self):
        from agent.social.channel import Channel, ChannelRegistry

        started = []

        class MockChannel(Channel):
            def __init__(self, name):
                self._name = name

            async def start(self):
                started.append(self._name)

            async def stop(self):
                pass

            async def send(self, message):
                return True

            def on_message(self, callback):
                pass

            @property
            def channel_type(self):
                return self._name

        registry = ChannelRegistry()
        registry.register(MockChannel("ch1"))
        registry.register(MockChannel("ch2"))
        await registry.start_all()

        assert "ch1" in started
        assert "ch2" in started
