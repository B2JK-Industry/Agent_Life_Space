"""
Test scenarios for Message Router.

Practical scenarios:
1. Messages are delivered to correct module
2. Unknown targets go to dead letter queue (not silently dropped)
3. Expired messages are rejected
4. Priority ordering works (critical before normal)
5. Timeout triggers retry, then dead letter
6. Router graceful shutdown drains remaining messages
7. Handler errors don't crash the router
"""

from __future__ import annotations

import asyncio

import pytest

from agent.core.messages import Message, MessageType, ModuleID, Priority
from agent.core.router import DeadLetterQueue, MessageRouter


@pytest.fixture
def router() -> MessageRouter:
    return MessageRouter(retry_base_delay=0.05, retry_max_delay=0.2)


class TestDeadLetterQueue:
    """Dead letters must never be silently lost."""

    def test_add_and_retrieve(self) -> None:
        dlq = DeadLetterQueue()
        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.REQUEST,
        )
        dlq.add(msg, "test_reason")
        assert dlq.size == 1
        items = dlq.get_all()
        assert items[0][0].id == msg.id
        assert items[0][1] == "test_reason"

    def test_retry_removes_from_queue(self) -> None:
        dlq = DeadLetterQueue()
        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.REQUEST,
        )
        dlq.add(msg, "test")
        retried = dlq.retry(msg.id)
        assert retried is not None
        assert retried.id == msg.id
        assert dlq.size == 0

    def test_retry_nonexistent_returns_none(self) -> None:
        dlq = DeadLetterQueue()
        assert dlq.retry("nonexistent") is None

    def test_overflow_removes_oldest(self) -> None:
        dlq = DeadLetterQueue(max_size=2)
        msgs = []
        for i in range(3):
            msg = Message(
                source=ModuleID.BRAIN,
                target=ModuleID.MEMORY,
                msg_type=MessageType.REQUEST,
                payload={"index": i},
            )
            msgs.append(msg)
            dlq.add(msg, f"reason_{i}")

        assert dlq.size == 2
        # Oldest (index 0) should have been removed
        items = dlq.get_all()
        assert items[0][0].payload["index"] == 1
        assert items[1][0].payload["index"] == 2


class TestMessageRouting:
    """Messages must reach their target module."""

    @pytest.mark.asyncio
    async def test_message_delivered_to_handler(self, router: MessageRouter) -> None:
        """Basic delivery: brain sends to memory, memory receives."""
        received: list[Message] = []

        async def memory_handler(msg: Message) -> None:
            received.append(msg)
            return None

        router.register_handler(ModuleID.MEMORY, memory_handler)

        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.MEMORY_QUERY,
            payload={"query": "test"},
        )
        await router.send(msg)

        # Run router briefly to process the message
        task = asyncio.create_task(router.start())
        await asyncio.sleep(0.1)
        await router.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(received) == 1
        assert received[0].id == msg.id

    @pytest.mark.asyncio
    async def test_no_handler_goes_to_dead_letter(self, router: MessageRouter) -> None:
        """Messages to unregistered modules go to dead letter queue."""
        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,  # No handler registered
            msg_type=MessageType.REQUEST,
        )
        await router.send(msg)
        assert router.dead_letters.size == 1

    @pytest.mark.asyncio
    async def test_expired_message_rejected(self, router: MessageRouter) -> None:
        """Expired messages go to dead letter, not delivered."""
        import time

        async def handler(msg: Message) -> None:
            return None

        router.register_handler(ModuleID.MEMORY, handler)

        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.REQUEST,
            ttl_seconds=1,
            created_at_mono=time.monotonic() - 10,
        )
        await router.send(msg)
        assert router.dead_letters.size == 1

    @pytest.mark.asyncio
    async def test_response_routed_back(self, router: MessageRouter) -> None:
        """Handler returns a response → router sends it back to original sender."""
        brain_received: list[Message] = []

        async def memory_handler(msg: Message) -> Message:
            return msg.create_response({"result": "found it"})

        async def brain_handler(msg: Message) -> None:
            brain_received.append(msg)
            return None

        router.register_handler(ModuleID.MEMORY, memory_handler)
        router.register_handler(ModuleID.BRAIN, brain_handler)

        request = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.MEMORY_QUERY,
        )
        await router.send(request)

        task = asyncio.create_task(router.start())
        await asyncio.sleep(0.2)
        await router.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(brain_received) == 1
        assert brain_received[0].correlation_id == request.id

    @pytest.mark.asyncio
    async def test_handler_error_doesnt_crash_router(
        self, router: MessageRouter
    ) -> None:
        """A failing handler must not take down the entire router."""
        call_count = 0

        async def failing_handler(msg: Message) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("handler exploded")

        router.register_handler(ModuleID.MEMORY, failing_handler)

        # Send message with max_retries=1
        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.REQUEST,
            max_retries=1,
        )
        await router.send(msg)

        task = asyncio.create_task(router.start())
        await asyncio.sleep(0.3)
        await router.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should have tried twice (original + 1 retry), then dead lettered
        assert call_count == 2
        assert router.dead_letters.size == 1

    @pytest.mark.asyncio
    async def test_metrics_tracked(self, router: MessageRouter) -> None:
        """Router tracks delivery metrics."""
        async def handler(msg: Message) -> None:
            return None

        router.register_handler(ModuleID.MEMORY, handler)

        msg = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.REQUEST,
        )
        await router.send(msg)

        task = asyncio.create_task(router.start())
        await asyncio.sleep(0.1)
        await router.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        metrics = router.get_metrics()
        assert metrics["enqueued"] == 1
        assert metrics["delivered"] == 1


class TestPriorityRouting:
    """Critical messages must be processed before normal ones."""

    @pytest.mark.asyncio
    async def test_priority_order(self, router: MessageRouter) -> None:
        """Send low then critical — critical should be delivered first."""
        delivery_order: list[int] = []

        async def handler(msg: Message) -> None:
            delivery_order.append(msg.payload["order"])
            return None

        router.register_handler(ModuleID.MEMORY, handler)

        # Send low priority first
        low = Message(
            source=ModuleID.BRAIN,
            target=ModuleID.MEMORY,
            msg_type=MessageType.REQUEST,
            priority=Priority.LOW,
            payload={"order": 2},
        )
        critical = Message(
            source=ModuleID.WATCHDOG,
            target=ModuleID.MEMORY,
            msg_type=MessageType.HEALTH_CHECK,
            priority=Priority.CRITICAL,
            payload={"order": 1},
        )

        await router.send(low)
        await router.send(critical)

        task = asyncio.create_task(router.start())
        await asyncio.sleep(0.2)
        await router.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert delivery_order == [1, 2]


class TestLastActivity:
    """Router tracks last activity per module (not health — just last delivery)."""

    @pytest.mark.asyncio
    async def test_activity_tracking(self, router: MessageRouter) -> None:
        async def handler(msg: Message) -> None:
            return None

        router.register_handler(ModuleID.BRAIN, handler)
        activity = router.get_last_activity()
        assert "brain" in activity
        assert activity["brain"] >= 0
