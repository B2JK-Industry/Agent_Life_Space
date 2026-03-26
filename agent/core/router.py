"""
Agent Life Space — Message Router

Target-based message delivery bus with priority ordering.

What it does:
    - Routes messages to registered handlers by target ModuleID
    - Priority queue ordering (CRITICAL before NORMAL)
    - Dead letter queue for undeliverable messages
    - Retry with configurable backoff delay
    - Message TTL expiry (prevents stale delivery)
    - Delivery timeout (separate from TTL)
    - Metrics tracking

What it does NOT do:
    - No content-aware routing (does not inspect MessageType)
    - No load balancing (single handler per module)
    - No persistence (in-memory only — messages lost on crash)
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import aiosqlite
import orjson
import structlog

from agent.core.messages import (
    Message,
    MessageStatus,
    ModuleID,
)

logger = structlog.get_logger(__name__)


class MessagePersistence:
    """SQLite-backed message persistence for crash recovery.

    Messages are stored before delivery and removed after successful delivery.
    On restart, undelivered messages are replayed into the queue.
    """

    def __init__(self, db_path: str = "agent/data/message_queue.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS pending_messages (
                id TEXT PRIMARY KEY,
                priority INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                message_json BLOB NOT NULL,
                enqueued_at REAL NOT NULL
            )
        """)
        await self._db.commit()

    async def store(self, msg_id: str, priority: int, seq: int, message: Message) -> None:
        """Persist message before delivery attempt."""
        if self._db is None:
            return
        raw = orjson.dumps(message.model_dump(mode="json"))
        await self._db.execute(
            "INSERT OR REPLACE INTO pending_messages (id, priority, seq, message_json, enqueued_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (msg_id, priority, seq, raw, time.time()),
        )
        await self._db.commit()

    async def remove(self, msg_id: str) -> None:
        """Remove message after successful delivery."""
        if self._db is None:
            return
        await self._db.execute("DELETE FROM pending_messages WHERE id = ?", (msg_id,))
        await self._db.commit()

    async def load_pending(self) -> list[tuple[int, int, Message]]:
        """Load undelivered messages for replay (ordered by priority, seq)."""
        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT priority, seq, message_json FROM pending_messages ORDER BY priority, seq"
        )
        rows = await cursor.fetchall()
        results: list[tuple[int, int, Message]] = []
        for priority, seq, raw in rows:
            try:
                data = orjson.loads(raw)
                msg = Message(**data)
                if not msg.is_expired():
                    results.append((priority, seq, msg))
            except Exception:
                logger.warning("message_persistence_skip_corrupt", priority=priority)
        return results

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

MessageHandler = Callable[[Message], Coroutine[Any, Any, Message | None]]

# Default delivery timeout for handlers (seconds)
DEFAULT_DELIVERY_TIMEOUT = 30.0


class DeadLetterQueue:
    """
    Messages that couldn't be delivered.
    For manual inspection and retry. Never silently dropped.
    Uses dict for O(1) lookup by message ID.
    """

    def __init__(self, max_size: int = 10000) -> None:
        self._entries: dict[str, tuple[Message, str]] = {}  # id -> (message, reason)
        self._max_size = max_size

    def add(self, message: Message, reason: str) -> None:
        if len(self._entries) >= self._max_size:
            oldest_id = next(iter(self._entries))
            del self._entries[oldest_id]
            logger.warning(
                "dead_letter_queue_overflow",
                removed_id=oldest_id,
                queue_size=self._max_size,
            )
        self._entries[message.id] = (message, reason)
        logger.error(
            "message_dead_lettered",
            message_id=message.id,
            source=message.source.value,
            target=message.target.value,
            msg_type=message.msg_type.value,
            reason=reason,
        )

    def get_all(self) -> list[tuple[Message, str]]:
        return list(self._entries.values())

    def retry(self, message_id: str) -> Message | None:
        entry = self._entries.pop(message_id, None)
        return entry[0] if entry else None

    @property
    def size(self) -> int:
        return len(self._entries)


class MessageRouter:
    """
    Routes messages between agent modules.

    Uses asyncio priority queue with monotonic sequence number
    for tie-breaking (avoids comparing Message objects).
    """

    def __init__(
        self,
        delivery_timeout: float = DEFAULT_DELIVERY_TIMEOUT,
        retry_base_delay: float = 0.5,
        retry_max_delay: float = 10.0,
        persistence_db: str = "agent/data/message_queue.db",
    ) -> None:
        self._handlers: dict[ModuleID, MessageHandler] = {}
        self._queue: asyncio.PriorityQueue[tuple[int, int, Message]] = (
            asyncio.PriorityQueue()
        )
        self._seq = 0  # Monotonic sequence number for tie-breaking
        self._dead_letters = DeadLetterQueue()
        self._running = False
        self._delivery_timeout = delivery_timeout
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._metrics: dict[str, int] = defaultdict(int)
        self._last_activity: dict[ModuleID, float] = {}
        self._persistence = MessagePersistence(persistence_db)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def register_handler(self, module_id: ModuleID, handler: MessageHandler) -> None:
        if module_id in self._handlers:
            logger.warning("handler_replaced", module=module_id.value)
        self._handlers[module_id] = handler
        self._last_activity[module_id] = time.monotonic()
        logger.info("handler_registered", module=module_id.value)

    def unregister_handler(self, module_id: ModuleID) -> None:
        self._handlers.pop(module_id, None)
        self._last_activity.pop(module_id, None)
        logger.info("handler_unregistered", module=module_id.value)

    async def init(self) -> None:
        """Initialize persistence and replay undelivered messages from previous run."""
        await self._persistence.init()
        pending = await self._persistence.load_pending()
        if pending:
            for priority, seq, msg in pending:
                await self._queue.put((priority, seq, msg))
            # Update seq counter to avoid collisions
            if pending:
                self._seq = max(s for _, s, _ in pending) + 1
            logger.info("router_replayed_messages", count=len(pending))

    async def send(self, message: Message) -> None:
        """
        Enqueue a message for delivery.
        Validates before queuing — fail fast.
        """
        if message.is_expired():
            self._dead_letters.add(message, "expired_before_send")
            self._metrics["expired"] += 1
            return

        if message.target not in self._handlers:
            self._dead_letters.add(
                message, f"no_handler_for_{message.target.value}"
            )
            self._metrics["no_handler"] += 1
            return

        # (priority, sequence_number, message) — seq prevents Message comparison
        seq = self._next_seq()
        await self._queue.put(
            (message.priority.value, seq, message)
        )
        # Persist before delivery — survives crash
        await self._persistence.store(message.id, message.priority.value, seq, message)
        self._metrics["enqueued"] += 1

    async def start(self) -> None:
        """Start the message routing loop."""
        if self._running:
            logger.warning("router_already_running")
            return

        self._running = True
        logger.info("router_started")

        while self._running:
            try:
                try:
                    _priority, _seq, message = await asyncio.wait_for(
                        self._queue.get(), timeout=1.0
                    )
                except TimeoutError:
                    continue

                if message.is_expired():
                    self._dead_letters.add(message, "expired_in_queue")
                    self._metrics["expired"] += 1
                    self._queue.task_done()
                    continue

                await self._deliver(message)
                self._queue.task_done()

            except Exception:
                logger.exception("router_error")
                self._metrics["router_errors"] += 1

    async def stop(self) -> None:
        """Gracefully stop the router. Process remaining messages."""
        logger.info("router_stopping", remaining=self._queue.qsize())
        self._running = False

        drain_count = 0
        while not self._queue.empty():
            try:
                _priority, _seq, message = self._queue.get_nowait()
                if not message.is_expired():
                    await self._deliver(message)
                    drain_count += 1
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        await self._persistence.close()
        logger.info("router_stopped", drained=drain_count)

    async def _deliver(self, message: Message) -> None:
        """Deliver a message to its target handler."""
        handler = self._handlers.get(message.target)
        if handler is None:
            self._dead_letters.add(message, "handler_disappeared")
            self._metrics["delivery_failed"] += 1
            return

        try:
            # Delivery timeout is separate from message TTL
            response = await asyncio.wait_for(
                handler(message),
                timeout=self._delivery_timeout,
            )
            self._metrics["delivered"] += 1
            self._last_activity[message.target] = time.monotonic()
            # Remove from persistence after successful delivery
            await self._persistence.remove(message.id)

            if response is not None:
                await self.send(response)

        except TimeoutError:
            self._metrics["delivery_timeout"] += 1
            await self._maybe_retry(
                message, f"delivery_timeout_after_{self._delivery_timeout}s"
            )
        except Exception as e:
            self._metrics["delivery_error"] += 1
            await self._maybe_retry(message, f"error: {e!s}")

    async def _maybe_retry(self, message: Message, reason: str) -> None:
        """Retry with backoff delay, or dead-letter if exhausted."""
        if message.retry_count < message.max_retries:
            # Exponential backoff: base * 2^attempt, capped
            delay = min(
                self._retry_base_delay * (2 ** message.retry_count),
                self._retry_max_delay,
            )
            retry_msg = message.model_copy(
                update={
                    "retry_count": message.retry_count + 1,
                    "status": MessageStatus.PENDING,
                }
            )
            logger.warning(
                "message_retry",
                message_id=message.id,
                retry=message.retry_count + 1,
                max_retries=message.max_retries,
                backoff_delay=delay,
                reason=reason,
            )
            await asyncio.sleep(delay)
            await self.send(retry_msg)
        else:
            self._dead_letters.add(
                message,
                f"{reason} (after {message.max_retries} retries)",
            )

    def get_metrics(self) -> dict[str, int]:
        return dict(self._metrics)

    @property
    def dead_letters(self) -> DeadLetterQueue:
        return self._dead_letters

    def get_last_activity(self) -> dict[str, float]:
        """Seconds since last successful delivery per module."""
        now = time.monotonic()
        return {
            mod.value: round(now - last, 2)
            for mod, last in self._last_activity.items()
        }
