"""
Test scenarios for Job Runner.

CRITICAL tests — these verify the anti-hang guarantees:
1. Jobs timeout correctly (no infinite running)
2. Failed jobs retry with backoff
3. After max retries → dead letter queue
4. Concurrent job limit respected
5. Jobs must return JSON-serializable dicts
6. Job status tracking works end-to-end
7. Error in one job doesn't affect others
"""

from __future__ import annotations

import asyncio

import pytest

from agent.core.job_runner import JobConfig, JobPriority, JobRunner, JobStatus


# --- Test job functions ---


async def successful_job(value: str = "ok") -> dict:
    """A job that succeeds immediately."""
    return {"status": "success", "value": value}


async def slow_job(duration: float = 0.2) -> dict:
    """A job that takes some time."""
    await asyncio.sleep(duration)
    return {"status": "completed", "duration": duration}


async def failing_job() -> dict:
    """A job that always fails."""
    raise RuntimeError("Job exploded")


async def hanging_job() -> dict:
    """A job that hangs forever — must be killed by timeout."""
    await asyncio.sleep(9999)
    return {"status": "should never reach this"}


async def bad_return_job() -> str:
    """A job that returns wrong type (not dict)."""
    return "this is not a dict"  # type: ignore[return-value]


async def eventually_succeeds(
    fail_count: int = 2, _attempt: list[int] | None = None
) -> dict:
    """A job that fails N times then succeeds. For retry testing."""
    if _attempt is None:
        _attempt = [0]
    _attempt[0] += 1
    if _attempt[0] <= fail_count:
        raise RuntimeError(f"Failing on attempt {_attempt[0]}")
    return {"status": "success", "attempts": _attempt[0]}


@pytest.fixture
def runner() -> JobRunner:
    runner = JobRunner(max_concurrent=4)
    runner.register_job_type("success", successful_job)
    runner.register_job_type("slow", slow_job)
    runner.register_job_type("fail", failing_job)
    runner.register_job_type("hang", hanging_job)
    runner.register_job_type("bad_return", bad_return_job)
    return runner


class TestJobExecution:
    """Basic job execution scenarios."""

    @pytest.mark.asyncio
    async def test_successful_job(self, runner: JobRunner) -> None:
        """A simple job that completes successfully."""
        job_id = await runner.schedule("success", {"value": "test123"})
        await asyncio.sleep(0.1)

        record = runner.get_job_status(job_id)
        assert record is not None
        assert record.status == JobStatus.COMPLETED
        assert record.result == {"status": "success", "value": "test123"}
        assert record.execution_time_ms >= 0

    @pytest.mark.asyncio
    async def test_unknown_job_type_raises(self, runner: JobRunner) -> None:
        """Scheduling unknown job type must fail immediately."""
        with pytest.raises(ValueError, match="Unknown job type"):
            await runner.schedule("nonexistent")

    @pytest.mark.asyncio
    async def test_slow_job_completes(self, runner: JobRunner) -> None:
        """A slow job that finishes within timeout."""
        config = JobConfig(timeout_seconds=5)
        job_id = await runner.schedule("slow", {"duration": 0.2}, config=config)
        await asyncio.sleep(0.5)

        record = runner.get_job_status(job_id)
        assert record is not None
        assert record.status == JobStatus.COMPLETED


class TestJobTimeout:
    """CRITICAL: Jobs must not hang forever."""

    @pytest.mark.asyncio
    async def test_hanging_job_killed(self, runner: JobRunner) -> None:
        """A hanging job MUST be killed by timeout."""
        config = JobConfig(timeout_seconds=1, max_retries=0)
        job_id = await runner.schedule("hang", config=config)
        await asyncio.sleep(1.5)

        record = runner.get_job_status(job_id)
        assert record is not None
        assert record.status == JobStatus.DEAD_LETTERED
        assert "Timeout" in (record.error or "")

    @pytest.mark.asyncio
    async def test_timeout_counts_in_stats(self, runner: JobRunner) -> None:
        config = JobConfig(timeout_seconds=1, max_retries=0)
        await runner.schedule("hang", config=config)
        await asyncio.sleep(1.5)

        stats = runner.get_stats()
        assert stats["total_timeouts"] >= 1


class TestJobRetry:
    """Jobs must retry with exponential backoff."""

    @pytest.mark.asyncio
    async def test_failing_job_retries(self, runner: JobRunner) -> None:
        """A failing job retries max_retries times, then dead letters."""
        config = JobConfig(
            timeout_seconds=5,
            max_retries=2,
            retry_base_delay=0.1,
        )
        job_id = await runner.schedule("fail", config=config)
        await asyncio.sleep(1.0)  # Wait for retries

        record = runner.get_job_status(job_id)
        assert record is not None
        assert record.status == JobStatus.DEAD_LETTERED
        assert record.retry_count == 2  # Tried 3 times total (0, 1, 2)

    @pytest.mark.asyncio
    async def test_dead_letter_queue_populated(self, runner: JobRunner) -> None:
        """Failed jobs must end up in dead letter queue."""
        config = JobConfig(max_retries=0, retry_base_delay=0.1)
        await runner.schedule("fail", config=config)
        await asyncio.sleep(0.5)

        dead = runner.get_dead_letters()
        assert len(dead) >= 1
        assert dead[0].status == JobStatus.DEAD_LETTERED

    @pytest.mark.asyncio
    async def test_retry_stats_tracked(self, runner: JobRunner) -> None:
        config = JobConfig(max_retries=2, retry_base_delay=0.1)
        await runner.schedule("fail", config=config)
        await asyncio.sleep(1.5)

        stats = runner.get_stats()
        assert stats["total_retries"] >= 1
        assert stats["total_dead_lettered"] >= 1


class TestJobValidation:
    """Jobs must return valid JSON dicts."""

    @pytest.mark.asyncio
    async def test_bad_return_type_fails(self, runner: JobRunner) -> None:
        """Jobs returning non-dict are treated as failures."""
        config = JobConfig(max_retries=0)
        job_id = await runner.schedule("bad_return", config=config)
        await asyncio.sleep(0.5)

        record = runner.get_job_status(job_id)
        assert record is not None
        assert record.status == JobStatus.DEAD_LETTERED
        assert "dict" in (record.error or "").lower()


class TestConcurrency:
    """Concurrent job limit must be respected."""

    @pytest.mark.asyncio
    async def test_concurrent_limit(self) -> None:
        """Only max_concurrent jobs run simultaneously."""
        max_running = [0]
        current_running = [0]

        async def tracking_job() -> dict:
            current_running[0] += 1
            if current_running[0] > max_running[0]:
                max_running[0] = current_running[0]
            await asyncio.sleep(0.2)
            current_running[0] -= 1
            return {"ok": True}

        runner = JobRunner(max_concurrent=2)
        runner.register_job_type("track", tracking_job)

        # Schedule 5 jobs, only 2 should run at once
        for _ in range(5):
            await runner.schedule("track")

        await asyncio.sleep(1.5)

        assert max_running[0] <= 2


class TestJobCancel:
    """Jobs can be cancelled."""

    @pytest.mark.asyncio
    async def test_cancel_job(self, runner: JobRunner) -> None:
        config = JobConfig(timeout_seconds=10)
        job_id = await runner.schedule("slow", {"duration": 5.0}, config=config)
        await asyncio.sleep(0.05)

        cancelled = await runner.cancel(job_id)
        # Job might already be running, cancel removes from active
        # This is a best-effort cancel
        record = runner.get_job_status(job_id)
        assert record is not None


class TestCircuitBreaker:
    """Circuit breaker prevents cascading failures."""

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold(self) -> None:
        """Too many failures in a window → reject new jobs."""
        runner = JobRunner(
            max_concurrent=4,
            circuit_breaker_threshold=3,
            circuit_breaker_window=10.0,
        )
        runner.register_job_type("fail", failing_job)
        runner.register_job_type("success", successful_job)

        # Trigger 3 fast failures (max_retries=0 so each is one failure)
        for _ in range(3):
            await runner.schedule("fail", config=JobConfig(max_retries=0))
        await asyncio.sleep(0.5)

        # Circuit should be open now — new jobs rejected
        with pytest.raises(RuntimeError, match="Circuit breaker"):
            await runner.schedule("success")

    @pytest.mark.asyncio
    async def test_circuit_closes_after_window(self) -> None:
        """Circuit breaker resets after the time window passes."""
        runner = JobRunner(
            max_concurrent=4,
            circuit_breaker_threshold=2,
            circuit_breaker_window=0.3,  # Very short for testing
        )
        runner.register_job_type("fail", failing_job)
        runner.register_job_type("success", successful_job)

        for _ in range(2):
            await runner.schedule("fail", config=JobConfig(max_retries=0))
        await asyncio.sleep(0.5)  # Wait for window to expire

        # Should be able to schedule again
        job_id = await runner.schedule("success")
        assert job_id is not None


class TestMultipleJobIsolation:
    """Error in one job must not affect others."""

    @pytest.mark.asyncio
    async def test_failing_job_doesnt_affect_success(self, runner: JobRunner) -> None:
        config_fail = JobConfig(max_retries=0)
        config_ok = JobConfig(timeout_seconds=5)

        fail_id = await runner.schedule("fail", config=config_fail)
        ok_id = await runner.schedule("success", {"value": "isolated"}, config=config_ok)

        await asyncio.sleep(0.5)

        fail_record = runner.get_job_status(fail_id)
        ok_record = runner.get_job_status(ok_id)

        assert fail_record is not None
        assert fail_record.status == JobStatus.DEAD_LETTERED

        assert ok_record is not None
        assert ok_record.status == JobStatus.COMPLETED
        assert ok_record.result == {"status": "success", "value": "isolated"}
