"""
Agent Life Space — Job Runner

Reliable job execution with anti-hang guarantees.
Every job has a timeout, heartbeat, and retry policy.

Design principles:
    - NO job runs forever — hard timeout on everything
    - NO silent failures — every failure is logged and tracked
    - NO infinite retries — bounded exponential backoff
    - NO zombie processes — watchdog integration
    - Graceful shutdown — jobs can save state before kill

Job lifecycle:
    SCHEDULED -> RUNNING -> COMPLETED | FAILED | TIMEOUT

Dead Letter Queue:
    Jobs that fail after max retries go here for manual inspection.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Type for async job functions
JobFunc = Callable[..., Coroutine[Any, Any, dict[str, Any]]]


class JobStatus(str, Enum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    DEAD_LETTERED = "dead_lettered"


class JobPriority(int, Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass
class JobConfig:
    """Configuration for a job. Sensible defaults that prevent hanging."""

    timeout_seconds: int = 60  # Hard kill after this
    max_retries: int = 3  # Max retry attempts
    retry_base_delay: float = 1.0  # Base delay for exponential backoff
    retry_max_delay: float = 60.0  # Cap on backoff delay
    heartbeat_interval: float = 10.0  # Seconds between heartbeats
    priority: JobPriority = JobPriority.NORMAL
    require_json_result: bool = True  # Job must return JSON-serializable dict


@dataclass
class JobRecord:
    """Complete record of a job's lifecycle."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    status: JobStatus = JobStatus.SCHEDULED
    config: JobConfig = field(default_factory=JobConfig)
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    retry_count: int = 0
    last_heartbeat: float = 0.0
    execution_time_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retry_count": self.retry_count,
            "execution_time_ms": self.execution_time_ms,
            "error": self.error,
            "result": self.result,
            "priority": self.config.priority.name.lower(),
            "timeout_seconds": self.config.timeout_seconds,
            "max_retries": self.config.max_retries,
        }


class JobRunner:
    """
    Executes jobs with timeout, retry, and monitoring.

    Key guarantees:
    1. Every job has a hard timeout — no infinite running
    2. Failed jobs retry with exponential backoff (bounded)
    3. After max retries, jobs go to dead letter queue
    4. All jobs are tracked from creation to completion/failure
    5. Heartbeat mechanism for long-running jobs
    """

    def __init__(
        self,
        max_concurrent: int = 4,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_window: float = 60.0,
        max_completed_history: int = 1000,
        max_dead_letters: int = 500,
    ) -> None:
        if max_concurrent < 1:
            msg = f"max_concurrent must be >= 1, got {max_concurrent}"
            raise ValueError(msg)

        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_jobs: dict[str, JobRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}  # job_id → asyncio Task
        self._completed_jobs: dict[str, JobRecord] = {}  # O(1) lookup by id
        self._dead_letters: dict[str, JobRecord] = {}  # O(1) lookup by id
        self._max_completed = max_completed_history
        self._max_dead = max_dead_letters
        self._job_functions: dict[str, JobFunc] = {}
        self._running = False
        # Circuit breaker: if N failures in window seconds, stop scheduling
        self._cb_threshold = circuit_breaker_threshold
        self._cb_window = circuit_breaker_window
        self._cb_failures: list[float] = []  # monotonic timestamps of recent failures
        self._cb_open = False
        self._stats = {
            "total_scheduled": 0,
            "total_completed": 0,
            "total_failed": 0,
            "total_timeouts": 0,
            "total_retries": 0,
            "total_dead_lettered": 0,
        }

    def register_job_type(self, name: str, func: JobFunc) -> None:
        """Register a named job type with its execution function."""
        self._job_functions[name] = func
        logger.info("job_type_registered", name=name)

    def _check_circuit_breaker(self) -> bool:
        """
        Circuit breaker: if too many failures in a window, stop scheduling.
        Prevents cascading failures and resource waste.
        Returns True if circuit is OPEN (reject new jobs).
        """
        now = time.monotonic()
        # Prune old failures outside window
        self._cb_failures = [
            t for t in self._cb_failures if now - t < self._cb_window
        ]
        if len(self._cb_failures) >= self._cb_threshold:
            if not self._cb_open:
                self._cb_open = True
                logger.error(
                    "circuit_breaker_open",
                    failures=len(self._cb_failures),
                    window=self._cb_window,
                )
            return True
        if self._cb_open:
            self._cb_open = False
            logger.info("circuit_breaker_closed")
        return False

    def _record_failure(self) -> None:
        """Record a job failure for circuit breaker tracking."""
        self._cb_failures.append(time.monotonic())

    @property
    def circuit_breaker_open(self) -> bool:
        return self._check_circuit_breaker()

    async def schedule(
        self,
        name: str,
        kwargs: dict[str, Any] | None = None,
        config: JobConfig | None = None,
    ) -> str:
        """
        Schedule a job for execution. Returns job ID.
        Rejects if circuit breaker is open.
        """
        if name not in self._job_functions:
            msg = f"Unknown job type: '{name}'. Registered: {list(self._job_functions.keys())}"
            raise ValueError(msg)

        if self._check_circuit_breaker():
            msg = f"Circuit breaker OPEN: too many failures ({len(self._cb_failures)} in {self._cb_window}s). Job '{name}' rejected."
            raise RuntimeError(msg)

        config = config or JobConfig()
        record = JobRecord(name=name, config=config)
        self._active_jobs[record.id] = record
        self._stats["total_scheduled"] += 1

        logger.info(
            "job_scheduled",
            job_id=record.id,
            name=name,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        )

        # Launch execution (respects concurrency limit)
        task = asyncio.create_task(self._execute(record, kwargs or {}))
        self._tasks[record.id] = task

        return record.id

    async def _execute(self, record: JobRecord, kwargs: dict[str, Any]) -> None:
        """Execute a job with full lifecycle management."""
        async with self._semaphore:
            await self._run_with_retries(record, kwargs)

    async def _run_with_retries(
        self, record: JobRecord, kwargs: dict[str, Any]
    ) -> None:
        """Run a job, retrying on failure with exponential backoff."""
        func = self._job_functions[record.name]

        for attempt in range(1 + record.config.max_retries):
            record.status = JobStatus.RUNNING
            record.started_at = datetime.now(UTC).isoformat()
            record.last_heartbeat = time.monotonic()
            record.retry_count = attempt

            start_time = time.monotonic()

            try:
                # Run with hard timeout
                result = await asyncio.wait_for(
                    func(**kwargs),
                    timeout=record.config.timeout_seconds,
                )

                # Validate result is dict (JSON-serializable)
                if record.config.require_json_result and not isinstance(result, dict):
                    raise TypeError(
                        f"Job must return dict, got {type(result).__name__}"
                    )

                # Validate JSON serializable
                if record.config.require_json_result:
                    import orjson
                    orjson.dumps(result)

                # Success
                record.status = JobStatus.COMPLETED
                record.result = result
                record.completed_at = datetime.now(UTC).isoformat()
                record.execution_time_ms = int(
                    (time.monotonic() - start_time) * 1000
                )

                self._active_jobs.pop(record.id, None)
                self._store_completed(record)
                self._stats["total_completed"] += 1

                logger.info(
                    "job_completed",
                    job_id=record.id,
                    name=record.name,
                    execution_time_ms=record.execution_time_ms,
                    retries=attempt,
                )
                return

            except TimeoutError:
                record.error = (
                    f"Timeout after {record.config.timeout_seconds}s "
                    f"(attempt {attempt + 1}/{1 + record.config.max_retries})"
                )
                self._stats["total_timeouts"] += 1
                self._record_failure()
                logger.warning(
                    "job_timeout",
                    job_id=record.id,
                    name=record.name,
                    attempt=attempt + 1,
                    timeout=record.config.timeout_seconds,
                )

            except Exception as e:
                record.error = f"{type(e).__name__}: {e!s}"
                self._stats["total_failed"] += 1
                self._record_failure()
                logger.error(
                    "job_error",
                    job_id=record.id,
                    name=record.name,
                    attempt=attempt + 1,
                    error=str(e),
                )

            # Should we retry?
            if attempt < record.config.max_retries:
                # Exponential backoff: base * 2^attempt, capped
                delay = min(
                    record.config.retry_base_delay * (2**attempt),
                    record.config.retry_max_delay,
                )
                self._stats["total_retries"] += 1
                logger.info(
                    "job_retry",
                    job_id=record.id,
                    name=record.name,
                    delay=delay,
                    next_attempt=attempt + 2,
                )
                await asyncio.sleep(delay)

        # All retries exhausted — dead letter
        record.status = JobStatus.DEAD_LETTERED
        record.completed_at = datetime.now(UTC).isoformat()
        self._active_jobs.pop(record.id, None)
        self._store_dead_letter(record)
        self._stats["total_dead_lettered"] += 1

        logger.error(
            "job_dead_lettered",
            job_id=record.id,
            name=record.name,
            total_attempts=1 + record.config.max_retries,
            last_error=record.error,
        )

    def _store_completed(self, record: JobRecord) -> None:
        """Store completed job with bounded history."""
        self._tasks.pop(record.id, None)
        if len(self._completed_jobs) >= self._max_completed:
            oldest_id = next(iter(self._completed_jobs))
            del self._completed_jobs[oldest_id]
        self._completed_jobs[record.id] = record

    def _store_dead_letter(self, record: JobRecord) -> None:
        """Store dead-lettered job with bounded history."""
        self._tasks.pop(record.id, None)
        if len(self._dead_letters) >= self._max_dead:
            oldest_id = next(iter(self._dead_letters))
            del self._dead_letters[oldest_id]
        self._dead_letters[record.id] = record

    def get_job_status(self, job_id: str) -> JobRecord | None:
        """Get the status of a job by ID. O(1) lookup."""
        return (
            self._active_jobs.get(job_id)
            or self._completed_jobs.get(job_id)
            or self._dead_letters.get(job_id)
        )

    def get_active_jobs(self) -> list[JobRecord]:
        return list(self._active_jobs.values())

    def get_recent_jobs(self, limit: int = 20) -> list[JobRecord]:
        """Return recent active/completed/dead-letter jobs."""
        records = [
            *self._active_jobs.values(),
            *self._completed_jobs.values(),
            *self._dead_letters.values(),
        ]
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records[:limit]

    def get_dead_letters(self) -> list[JobRecord]:
        return list(self._dead_letters.values())

    def get_stats(self) -> dict[str, int]:
        return dict(self._stats)

    async def cancel(self, job_id: str) -> bool:
        """Cancel a scheduled/running job."""
        if job_id in self._active_jobs:
            task = self._tasks.pop(job_id, None)
            if task and not task.done():
                task.cancel()
            record = self._active_jobs.pop(job_id)
            record.status = JobStatus.CANCELLED
            record.completed_at = datetime.now(UTC).isoformat()
            self._store_completed(record)
            logger.info("job_cancelled", job_id=job_id, name=record.name)
            return True
        return False
