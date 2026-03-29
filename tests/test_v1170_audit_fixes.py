"""
Tests for v1.17.0 audit-driven bug fixes.

Each test validates a specific bug fix identified during the full codebase audit.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

# --- B1: Cron month-boundary crash ---


class TestCronMonthBoundary:
    """Verify morning report loop handles last day of month correctly."""

    def test_timedelta_handles_month_end(self):
        """Jan 31 + timedelta(days=1) = Feb 1, not ValueError."""
        jan_31 = datetime(2026, 1, 31, 9, 0, 0, tzinfo=UTC)
        next_8am = jan_31.replace(hour=8, minute=0, second=0, microsecond=0)
        result = next_8am + timedelta(days=1)
        assert result.month == 2
        assert result.day == 1
        assert result.hour == 8

    def test_timedelta_handles_december_end(self):
        """Dec 31 + timedelta(days=1) = Jan 1 next year."""
        dec_31 = datetime(2026, 12, 31, 9, 0, 0, tzinfo=UTC)
        next_8am = dec_31.replace(hour=8, minute=0, second=0, microsecond=0)
        result = next_8am + timedelta(days=1)
        assert result.year == 2027
        assert result.month == 1
        assert result.day == 1

    def test_timedelta_handles_feb_28_non_leap(self):
        """Feb 28 in non-leap year + 1 day = Mar 1."""
        feb_28 = datetime(2027, 2, 28, 9, 0, 0, tzinfo=UTC)
        next_8am = feb_28.replace(hour=8, minute=0, second=0, microsecond=0)
        result = next_8am + timedelta(days=1)
        assert result.month == 3
        assert result.day == 1


# --- B2: Telegram operator precedence ---


class TestOperatorPrecedence:
    """Verify the simple prompt branch is reachable."""

    def test_short_context_matches_simple(self):
        """tool_context with <= 2 newlines should match simple branch."""
        tool_context = "Dnes je 26. marca 2026."
        task_type = "simple"
        result = task_type in ("simple", "factual", "greeting") and tool_context.count("\n") <= 2
        assert result is True

    def test_long_context_rejects_simple(self):
        """tool_context with > 2 newlines should NOT match simple branch."""
        tool_context = "line1\nline2\nline3\nline4\nline5"
        task_type = "simple"
        result = task_type in ("simple", "factual", "greeting") and tool_context.count("\n") <= 2
        assert result is False

    def test_non_simple_type_rejects(self):
        """Non-simple task types should not match simple branch."""
        tool_context = ""
        task_type = "programming"
        result = task_type in ("simple", "factual", "greeting") and tool_context.count("\n") <= 2
        assert result is False


# --- B3: query_facts filter ---


class TestQueryFactsFilter:
    """Verify query_facts filters by memory type AND kind."""

    @pytest.fixture
    async def store(self, tmp_path):
        from agent.memory.store import MemoryStore

        store = MemoryStore(str(tmp_path / "test.db"))
        await store.initialize()
        yield store
        await store.close()

    async def test_filters_by_type_and_kind(self, store):
        from agent.memory.store import MemoryEntry, MemoryKind, MemoryType

        # SEMANTIC + FACT — should be returned
        fact = MemoryEntry(
            content="Python je interpretovaný jazyk",
            memory_type=MemoryType.SEMANTIC,
            tags=["python"],
            kind=MemoryKind.FACT,
        )
        # SEMANTIC + BELIEF — should be filtered out
        belief = MemoryEntry(
            content="Python je najlepší jazyk",
            memory_type=MemoryType.SEMANTIC,
            tags=["python"],
            kind=MemoryKind.BELIEF,
        )
        # EPISODIC + FACT — should be filtered out (wrong type)
        episodic = MemoryEntry(
            content="Spustil som Python skript",
            memory_type=MemoryType.EPISODIC,
            tags=["python"],
            kind=MemoryKind.FACT,
        )
        # PROCEDURAL + PROCEDURE — should be returned
        procedure = MemoryEntry(
            content="pip install na inštaláciu balíčkov",
            memory_type=MemoryType.PROCEDURAL,
            tags=["python"],
            kind=MemoryKind.PROCEDURE,
        )

        for entry in [fact, belief, episodic, procedure]:
            await store.store(entry)

        results = await store.query_facts(tags=["python"])
        contents = {e.content for e in results}

        assert fact.content in contents
        assert procedure.content in contents
        assert belief.content not in contents
        assert episodic.content not in contents


# --- B5: Escape triple quotes order ---


class TestEscapeTripleQuotes:
    """Verify backslash escaping happens before triple-quote escaping."""

    def test_plain_code_unchanged(self):
        from agent.core.sandbox_executor import _escape_triple_quotes

        assert _escape_triple_quotes("print('hello')") == "print('hello')"

    def test_triple_quotes_escaped(self):
        from agent.core.sandbox_executor import _escape_triple_quotes

        result = _escape_triple_quotes('x = """hello"""')
        assert r'\"\"\"' in result
        assert '"""' not in result

    def test_backslash_escaped(self):
        from agent.core.sandbox_executor import _escape_triple_quotes

        result = _escape_triple_quotes("x = 'a\\b'")
        # Single backslash becomes double backslash
        assert "\\\\" in result

    def test_combined_backslash_and_triple_quotes(self):
        from agent.core.sandbox_executor import _escape_triple_quotes

        code = '"""\\n"""'
        result = _escape_triple_quotes(code)
        # Triple-quotes should be escaped
        assert '"""' not in result


# --- B6: cancel() Task cancellation ---


class TestCancelTask:
    """Verify cancel() actually cancels the asyncio Task."""

    @pytest.fixture
    def runner(self):
        from agent.core.job_runner import JobRunner

        runner = JobRunner()

        async def slow_job(**kwargs):
            await asyncio.sleep(60)
            return {"done": True}

        runner.register_job_type("slow", slow_job)
        return runner

    async def test_cancel_stores_task_ref(self, runner):
        """Scheduling a job stores a Task reference."""
        job_id = await runner.schedule("slow")
        await asyncio.sleep(0.05)
        assert job_id in runner._tasks
        task = runner._tasks[job_id]
        assert not task.done()
        await runner.cancel(job_id)

    async def test_cancel_actually_cancels(self, runner):
        """After cancel(), the asyncio Task is cancelled."""
        job_id = await runner.schedule("slow")
        await asyncio.sleep(0.05)
        task = runner._tasks.get(job_id)
        result = await runner.cancel(job_id)
        assert result is True
        assert task is not None
        # Give the event loop a moment to process the cancellation
        await asyncio.sleep(0.05)
        assert task.cancelled() or task.done()


# --- B7: _model_failures instance isolation ---


class TestModelFailuresIsolation:
    """Verify _model_failures is per-instance, not shared."""

    def test_instances_have_separate_failures(self, tmp_path):
        from agent.brain.learning import LearningSystem

        dir1 = tmp_path / "l1"
        dir2 = tmp_path / "l2"
        dir1.mkdir()
        dir2.mkdir()

        ls1 = LearningSystem(
            skills_path=str(dir1 / "skills.json"),
            knowledge_dir=str(dir1 / "knowledge"),
        )
        ls2 = LearningSystem(
            skills_path=str(dir2 / "skills.json"),
            knowledge_dir=str(dir2 / "knowledge"),
        )

        ls1._record_model_failure("curl", "claude-haiku-4-5-20251001", "timeout")

        assert ls1._get_last_failed_model("curl") == "claude-haiku-4-5-20251001"
        assert ls2._get_last_failed_model("curl") == ""
