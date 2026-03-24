"""
Agent Life Space — Agent Loop

John si sám vytvára frontu úloh a postupne ich spracúva.
Nie všetko naraz v jednom Claude volaní — ale po jednom, s výsledkami.

Flow:
    1. Daniel pošle správu (napr. "otestuj 5 skills")
    2. John rozloží na tasky a zaradí do internej queue
    3. Odpovie "mám N úloh, začínam"
    4. Spracúva po jednom (každý = jeden Claude CLI call)
    5. Zapisuje výsledky (skills, memory, knowledge)
    6. Po dokončení pošle súhrn

Toto beží na pozadí — neblokuje Telegram polling.
"""

from __future__ import annotations

import asyncio
import subprocess
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any

import orjson
import structlog

logger = structlog.get_logger(__name__)


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
        self.created_at = datetime.now(timezone.utc).isoformat()
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
                logger.error("work_item_error", error=str(e))

                if self._bot and item.callback_chat_id:
                    await self._bot.send_message(
                        item.callback_chat_id,
                        f"❌ {item.description}\nChyba: {e!s}",
                    )

            self._processing = False
            # Krátka pauza medzi úlohami
            await asyncio.sleep(1)

    async def _execute_item(self, item: WorkItem) -> str:
        """Vykonaj jednu úlohu cez Claude CLI."""
        env = os.environ.copy()
        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

        claude_bin = os.path.expanduser("~/.local/bin/claude")

        prompt = (
            f"Si John, agent na serveri b2jk-agentlifespace. "
            f"Pracuješ v ~/agent-life-space.\n\n"
            f"ÚLOHA: {item.description}\n\n"
            f"Urob to a na konci VŽDY napíš stručné zhrnutie čo si urobil. "
            f"Odpovedaj po slovensky."
        )

        from agent.core.models import get_model
        model = get_model("work_queue")

        result = await asyncio.to_thread(
            subprocess.run,
            [
                claude_bin,
                "--print",
                "--output-format", "json",
                "--model", model.model_id,
                "--max-turns", str(model.max_turns),
                "--dangerously-skip-permissions",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=model.timeout,
            env=env,
            cwd=os.path.expanduser("~/agent-life-space"),
        )

        if result.returncode != 0:
            return f"Error: {result.stderr[:200] or result.stdout[:200]}"

        try:
            data = orjson.loads(result.stdout)
            if data.get("is_error"):
                return f"Error: {data.get('result', '?')}"
            return data.get("result", "").strip() or "Hotovo (bez detailov)."
        except Exception:
            return "Nepodarilo sa spracovať odpoveď."

    def get_status(self) -> dict[str, Any]:
        return {
            "queue_size": len(self._queue),
            "processing": self._processing,
            "total_processed": self._processed_count,
            "running": self._running,
        }
