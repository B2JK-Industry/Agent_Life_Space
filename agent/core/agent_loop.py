"""
Agent Life Space — Agent Loop

John si sám vytvára frontu úloh a postupne ich spracúva.
Nie všetko naraz v jednom Claude volaní — ale po jednom, s výsledkami.

Flow:
    1. Používateľ pošle správu (napr. "otestuj 5 skills")
    2. John rozloží na tasky a zaradí do internej queue
    3. Odpovie "mám N úloh, začínam"
    4. Spracúva po jednom (každý = jeden Claude CLI call)
    5. Zapisuje výsledky (skills, memory, knowledge)
    6. Po dokončení pošle súhrn

Toto beží na pozadí — neblokuje Telegram polling.
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Characters that could break prompt structure or inject instructions
_UNSAFE_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Max description length to prevent prompt flooding
_MAX_DESCRIPTION_LEN = 2000


def _sanitize_work_description(description: str) -> str:
    """Sanitize work item description before injecting into prompt.

    Removes control characters, trims length, and escapes prompt delimiters.
    """
    # Strip control characters (keep \n, \r, \t)
    cleaned = _UNSAFE_PATTERN.sub("", description)
    # Trim to max length
    if len(cleaned) > _MAX_DESCRIPTION_LEN:
        cleaned = cleaned[:_MAX_DESCRIPTION_LEN] + "... (skrátené)"
    return cleaned.strip()


class WorkItem:
    """Jedna položka v pracovnej fronte."""

    def __init__(
        self,
        description: str,
        callback_chat_id: int = 0,
        priority: int = 0,
    ) -> None:
        self.description = description
        self.callback_chat_id = callback_chat_id
        self.priority = priority
        self.created_at = datetime.now(UTC).isoformat()
        self.result: str | None = None
        self.success: bool = False


class AgentLoop:
    """
    Johnova pracovná fronta. Spracúva úlohy postupne na pozadí.
    """

    def __init__(
        self,
        telegram_bot: Any = None,
        max_queue_size: int = 50,
    ) -> None:
        self._queue: deque[WorkItem] = deque()
        self._bot = telegram_bot
        self._max_queue = max_queue_size
        self._running = False
        self._processing = False
        self._processed_count = 0
        self._error_count = 0
        self._consecutive_errors = 0
        self._task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        """Spusti background worker."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._worker())
        logger.info("agent_loop_started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("agent_loop_stopped")

    def add_work(
        self,
        items: list[str],
        chat_id: int = 0,
    ) -> int:
        """Pridaj úlohy do fronty. Vracia počet pridaných."""
        added = 0
        for desc in items:
            if len(self._queue) >= self._max_queue:
                break
            self._queue.append(WorkItem(description=desc, callback_chat_id=chat_id))
            added += 1
        logger.info("work_added", count=added, queue_size=len(self._queue))
        return added

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    @property
    def is_busy(self) -> bool:
        return self._processing

    async def _worker(self) -> None:
        """Background worker — spracúva frontu po jednom."""
        while self._running:
            if not self._queue:
                await asyncio.sleep(2)
                continue

            item = self._queue.popleft()
            self._processing = True

            logger.info(
                "work_item_start",
                description=item.description[:80],
                remaining=len(self._queue),
            )

            try:
                result = await self._execute_item(item)
                item.result = result
                item.success = True
                self._processed_count += 1

                # Pošli výsledok na Telegram
                if self._bot and item.callback_chat_id:
                    remaining = len(self._queue)
                    status = f" ({remaining} zostáva)" if remaining > 0 else " (hotovo)"
                    await self._bot.send_message(
                        item.callback_chat_id,
                        f"✅ {item.description}\n{result[:500]}{status}",
                    )

            except Exception as e:
                item.result = str(e)
                item.success = False
                self._error_count += 1
                logger.error(
                    "work_item_error",
                    error=str(e),
                    consecutive_errors=self._consecutive_errors + 1,
                )
                self._consecutive_errors += 1

                if self._bot and item.callback_chat_id:
                    await self._bot.send_message(
                        item.callback_chat_id,
                        f"❌ {item.description}\nChyba: {e!s}",
                    )

                # Circuit breaker: 3 chyby za sebou → pauza
                if self._consecutive_errors >= 3:
                    logger.warning(
                        "circuit_breaker_triggered",
                        consecutive=self._consecutive_errors,
                        pause_seconds=30,
                    )
                    if self._bot and item.callback_chat_id:
                        await self._bot.send_message(
                            item.callback_chat_id,
                            "⚠️ 3 chyby za sebou. Pauzujem na 30s.",
                        )
                    await asyncio.sleep(30)
                    self._consecutive_errors = 0
            else:
                self._consecutive_errors = 0

            self._processing = False
            # Krátka pauza medzi úlohami
            await asyncio.sleep(1)

    @staticmethod
    def _is_programming_task(description: str) -> bool:
        """Detect if work item needs file system access (programming task)."""
        import re
        _PROGRAMMING_SIGNALS = [
            r"(napíš|napís|uprav|vytvor|oprav|refaktor|implementuj)\w*\s+.*(kód|súbor|subor|funkci|modul|test)",
            r"\b(commit|push|pull|git|deploy)\b",
            r"\b(python|javascript|typescript|bash|docker)\b",
            r"(spusti|spust|run)\s+test",
            r"(otestuj|skontroluj)\s+\w+\.(py|js|ts|sh)",
            r"\b(pip|npm|apt)\s+install\b",
        ]
        text = description.lower()
        return any(re.search(p, text) for p in _PROGRAMMING_SIGNALS)

    async def _execute_item(self, item: WorkItem) -> str:
        """Vykonaj jednu úlohu cez LLM provider (CLI alebo API)."""
        from agent.core.identity import get_agent_identity, get_response_language_instruction
        from agent.core.llm_provider import GenerateRequest, get_provider
        from agent.core.models import get_model

        safe_description = _sanitize_work_description(item.description)

        model = get_model("work_queue")
        from agent.core.paths import get_project_root
        project_root = get_project_root()

        prompt = (
            f"You are {get_agent_identity().agent_name}, an agent running on "
            f"{get_agent_identity().server_name}. "
            f"Pracuješ v {project_root}.\n\n"
            f"ÚLOHA: {safe_description}\n\n"
            "Urob to a na konci VŽDY napíš stručné zhrnutie čo si urobil. "
            f"{get_response_language_instruction()}"
        )

        provider = get_provider()
        response = await provider.generate(GenerateRequest(
            messages=[{"role": "user", "content": prompt}],
            model=model.model_id,
            max_turns=model.max_turns,
            timeout=model.timeout,
            allow_file_access=self._is_programming_task(item.description),
            cwd=project_root,
        ))

        if not response.success:
            return f"Error: {response.error[:200]}"

        return response.text or "Hotovo (bez detailov)."

    def get_status(self) -> dict[str, Any]:
        total = self._processed_count + self._error_count
        return {
            "queue_size": len(self._queue),
            "processing": self._processing,
            "total_success": self._processed_count,
            "total_errors": self._error_count,
            "total_attempted": total,
            "consecutive_errors": self._consecutive_errors,
            "running": self._running,
            "error_rate": round(
                self._error_count / max(total, 1), 2
            ),
        }

    def get_queue_snapshot(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return a lightweight snapshot of queued work items."""
        return [
            {
                "description": item.description,
                "created_at": item.created_at,
                "priority": item.priority,
            }
            for item in list(self._queue)[:limit]
        ]
