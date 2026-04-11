"""
Agent Life Space — Internal Dispatcher

Spracuj čo vieš sám. LLM volaj len keď musíš.

Handles ONLY deterministic queries where pattern matching is reliable:
- Slash-command equivalents in free text (status, health, tasks, skills, budget)
- Identity questions
- Simple factual lookups

Does NOT try to handle:
- Slovak language fuzzy matching (unreliable)
- Knowledge base search (will be replaced by semantic router)
- Complex questions about people/topics (→ LLM)
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class InternalDispatcher:
    """
    Skús odpovedať bez LLM. Len isté veci.
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent

    async def try_handle(self, text: str) -> str | None:
        """
        Spracuj správu interne ak je to deterministický dotaz.
        Vráti odpoveď alebo None (= treba LLM).
        """
        text_lower = text.lower().strip()

        # Ak text obsahuje URL → preskočiť dispatcher, nechať LLM spracovať
        if "http://" in text_lower or "https://" in text_lower or "www." in text_lower:
            return None

        # Memory storage — handle BEFORE LLM to avoid CLI errormaxturns
        memory_content = self._extract_remember_content(text_lower, text)
        if memory_content is not None:
            return await self._handle_remember(memory_content)

        # Exact/near-exact pattern matching only
        handlers = [
            (self._is_status_query, self._handle_status),
            (self._is_health_query, self._handle_health),
            (self._is_tasks_query, self._handle_tasks),
            (self._is_skills_query, self._handle_skills),
            (self._is_budget_query, self._handle_budget),
            (self._is_identity_query, self._handle_identity),
        ]

        for detector, handler in handlers:
            if detector(text_lower):
                try:
                    result = await handler()
                    if result:
                        logger.info("dispatch_internal", handler=handler.__name__)
                        return result
                except Exception as e:
                    logger.error("dispatch_error", handler=handler.__name__, error=str(e))

        # === Semantic router fallback (if model installed) ===
        # classify_intent() calls model.encode() which is CPU-bound;
        # run off the event loop so it cannot stall the server.
        try:
            import asyncio as _aio
            from agent.brain.semantic_router import classify_intent, is_available
            if is_available():
                intent, confidence = await _aio.to_thread(classify_intent, text_lower)
                # Zvýšený threshold — 0.55 bol príliš nízky, matchoval konverzačné otázky
                if confidence >= 0.75 and len(text_lower.split()) <= 6:
                    intent_handler_map = {
                        "status": self._handle_status,
                        "health": self._handle_health,
                        "tasks": self._handle_tasks,
                        "skills": self._handle_skills,
                        "budget": self._handle_budget,
                        "identity": self._handle_identity,
                    }
                    handler_opt = intent_handler_map.get(intent)
                    if handler_opt is not None:
                        result = await handler_opt()
                        if result:
                            logger.info("dispatch_semantic", intent=intent, confidence=confidence)
                            return result
        except Exception as e:
            logger.error("semantic_router_error", error=str(e))

        return None

    # --- Detectors: return True only when CONFIDENT ---
    # MAX 4 SLOVÁ. Dispatcher len pre krátke priame dotazy.
    # Všetko dlhšie alebo konverzačné → LLM.

    @staticmethod
    def _is_status_query(text: str) -> bool:
        if len(text.split()) > 6:
            return False
        return bool(re.search(
            r"\b(stav|status)\b"
            r"|aký je tvoj stav"
            r"|ako sa máš"
            r"|si v poriadku"
            r"|čo robíš"
            r"|how are you"
            r"|what.*status",
            text,
        ))

    @staticmethod
    def _is_health_query(text: str) -> bool:
        if len(text.split()) > 6:
            return False
        return bool(re.search(
            r"\b(zdravie|health)\b"
            r"|system health"
            r"|ako je server"
            r"|stav servera",
            text,
        ))

    @staticmethod
    def _is_tasks_query(text: str) -> bool:
        if len(text.split()) > 6:
            return False
        return bool(re.search(
            r"\b(úloh[ya]?|tasks?)\b"
            r"|aké úlohy"
            r"|čo máš v rade"
            r"|zoznam úloh"
            r"|čo riešiš"
            r"|task list"
            r"|what.*tasks",
            text,
        ))

    @staticmethod
    def _is_skills_query(text: str) -> bool:
        if len(text.split()) > 6:
            return False
        return bool(re.search(
            r"\b(skills|schopnost[ií])\b"
            r"|čo vieš robiť"
            r"|aké skills"
            r"|what.*can you do",
            text,
        ))

    @staticmethod
    def _is_budget_query(text: str) -> bool:
        if len(text.split()) > 6:
            return False
        return bool(re.search(
            r"\b(rozpočet|budget)\b"
            r"|koľko.*peňaz"
            r"|finančn"
            r"|financial",
            text,
        ))

    @staticmethod
    def _is_identity_query(text: str) -> bool:
        if len(text.split()) > 6:
            return False
        return bool(re.search(
            r"\b(kto si|kto som|who are you)\b"
            r"|predstav sa"
            r"|introduce yourself",
            text,
        ))

    # --- Handlers: structured responses from modules ---

    async def _handle_status(self) -> str:
        health = self._agent.watchdog.get_system_health()
        mem_stats = self._agent.memory.get_stats()
        task_stats = self._agent.tasks.get_stats()
        return (
            f"Bežím. CPU: {health.cpu_percent:.0f}%, RAM: {health.memory_percent:.0f}%. "
            f"Spomienky: {mem_stats['total_memories']}. "
            f"Úlohy: {task_stats['total_tasks']}. "
            f"Moduly: {'všetky OK' if not health.alerts else ', '.join(health.alerts)}."
        )

    async def _handle_health(self) -> str:
        health = self._agent.watchdog.get_system_health()
        modules = ", ".join(f"{n}: {s}" for n, s in health.modules.items())
        return (
            f"CPU: {health.cpu_percent:.1f}%, "
            f"RAM: {health.memory_percent:.1f}% ({health.memory_used_mb:.0f}MB), "
            f"Disk: {health.disk_percent:.1f}%\n"
            f"Moduly: {modules}\n"
            f"Alerty: {', '.join(health.alerts) if health.alerts else 'žiadne'}"
        )

    async def _handle_tasks(self) -> str:
        from agent.tasks.manager import TaskStatus
        stats = self._agent.tasks.get_stats()
        queued = self._agent.tasks.get_tasks_by_status(TaskStatus.QUEUED)
        lines = [f"Úlohy celkom: {stats['total_tasks']}"]
        if stats["by_status"]:
            for s, count in stats["by_status"].items():
                lines.append(f"  {s}: {count}")
        if queued:
            lines.append("V rade:")
            for t in queued[:5]:
                lines.append(f"  • {t.name}")
        return "\n".join(lines)

    async def _handle_skills(self) -> str:
        try:
            from agent.brain.skills import SkillRegistry
            from agent.core.paths import get_project_root
            base = get_project_root()
            registry = SkillRegistry(f"{base}/agent/brain/skills.json")
            summary = registry.summary()
            mastered = ", ".join(summary["mastered"]) if summary["mastered"] else "žiadne"
            unknown = ", ".join(summary["unknown"]) if summary["unknown"] else "žiadne"
            return (
                f"Skills ({summary['total']} celkom):\n"
                f"  Mastered ({len(summary['mastered'])}): {mastered}\n"
                f"  Unknown ({len(summary['unknown'])}): {unknown}"
            )
        except Exception as e:
            return f"Skills: chyba — {e}"

    async def _handle_budget(self) -> str:
        try:
            stats = self._agent.finance.get_stats()
            return (
                f"Príjem: ${stats['total_income']:.2f}, "
                f"Výdavky: ${stats['total_expenses']:.2f}, "
                f"Čistý: ${stats['net']:.2f}. "
                f"Čakajúce návrhy: {stats['pending_proposals']}."
            )
        except Exception:
            return "Finance modul nie je dostupný."

    async def _handle_identity(self) -> str:
        from agent.core.persona import get_system_prompt  # noqa: E402
        # Return first 2 sentences of centralized persona
        sentences = get_system_prompt().strip().split(".")
        return ".".join(sentences[:3]).strip() + "."

    # --- Memory storage: bypass LLM entirely ---

    _REMEMBER_PATTERNS = [
        re.compile(r"zapam[äa]taj\s+si\s+(?:že\s+)?(.+)", re.IGNORECASE),
        re.compile(r"zapam[äa]taj\s+si\s*:\s*(.+)", re.IGNORECASE),
        re.compile(r"remember\s+(?:that\s+)?(.+)", re.IGNORECASE),
        re.compile(r"ulož\s+(?:si\s+)?(?:do\s+pamäte\s+)?(?:že\s+)?(.+)", re.IGNORECASE),
    ]

    @staticmethod
    def _extract_remember_content(text_lower: str, original_text: str) -> str | None:
        """Extract content to remember, or None if not a remember request."""
        for pattern in InternalDispatcher._REMEMBER_PATTERNS:
            match = pattern.search(original_text)
            if match:
                content = match.group(1).strip().rstrip(".")
                if len(content) >= 3:
                    return content
        return None

    async def _handle_remember(self, content: str) -> str:
        """Store user-asserted fact directly into memory. Zero LLM tokens."""
        from agent.memory.store import MemoryEntry, MemoryKind, MemoryType, ProvenanceStatus

        entry = MemoryEntry(
            content=content,
            memory_type=MemoryType.SEMANTIC,
            kind=MemoryKind.FACT,
            tags=["user_request", "remembered"],
            source="dispatcher",
            importance=0.8,
            provenance=ProvenanceStatus.USER_ASSERTED,
        )
        await self._agent.memory.store(entry)
        logger.info("dispatch_remember", content=content[:80])
        return f"Zapamätal som si: {content}"
