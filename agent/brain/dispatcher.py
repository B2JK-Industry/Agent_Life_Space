"""
Agent Life Space — Internal Dispatcher

Spracuj čo vieš sám. LLM volaj len keď musíš.

Flow:
    1. Správa príde od Daniela
    2. Dispatcher skúsi nájsť internú odpoveď (pamäť, skills, moduly)
    3. Ak nájde → odpovie HNEĎ, žiadne LLM tokeny
    4. Ak nenájde → vráti None, handler pošle na LLM

Pokrýva:
    - Otázky o stave (zdravie, úlohy, pamäť, rozpočet, skills, usage)
    - Vyhľadávanie v pamäti a knowledge base
    - Jednoduché akcie (vytvor úlohu, zapamätaj si)
    - Systémové operácie (review, web, sandbox — cez príkazy)
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class InternalDispatcher:
    """
    Skús odpovedať bez LLM. Ak nevieš, vráť None.
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._patterns: list[tuple[list[str], str]] = [
            # (kľúčové slová, handler method name)
            # NOTE: Order matters! More specific patterns FIRST.
            (["kto si", "kto som", "identita", "o sebe"], "_handle_identity"),
            (["čo vieš robiť", "čo všetko vieš", "schopnosti", "aké máš schopnosti"], "_handle_capabilities"),
            (["skill", "schopnost", "ovládaš", "aké skills"], "_handle_skills"),
            (["stav", "status", "ako sa máš", "bežíš"], "_handle_status"),
            (["zdravie", "health", "cpu", "ram", "disk"], "_handle_health"),
            (["úloh", "tasks", "task", "čo robíš", "čo máš robiť"], "_handle_tasks"),
            (["pamäť", "memory", "spomienky", "pamätáš", "zapamätaj"], "_handle_memory"),
            (["rozpočet", "budget", "peniaze", "financ"], "_handle_budget"),
            (["usage", "spotreba", "token", "náklad"], "_handle_usage"),
        ]

    async def try_handle(self, text: str, handler_ref: Any = None) -> str | None:
        """
        Skús spracovať správu interne.
        Vráti odpoveď alebo None (= treba LLM).
        """
        text_lower = text.lower().strip()

        # Skús pattern matching
        for keywords, method_name in self._patterns:
            if any(kw in text_lower for kw in keywords):
                method = getattr(self, method_name, None)
                if method:
                    try:
                        result = await method(text_lower)
                        if result:
                            logger.info("dispatch_internal", handler=method_name)
                            return result
                    except Exception as e:
                        logger.error("dispatch_error", handler=method_name, error=str(e))

        # Knowledge base first (structured, reliable)
        kb_answer = self._search_knowledge(text_lower)
        if kb_answer:
            logger.info("dispatch_knowledge_hit")
            return kb_answer

        # Then memory (semantic/procedural)
        memory_answer = await self._search_memory(text_lower)
        if memory_answer:
            logger.info("dispatch_memory_hit")
            return memory_answer

        # Neviem — treba LLM
        return None

    # --- Interné handlery ---

    async def _handle_status(self, text: str) -> str:
        health = self._agent.watchdog.get_system_health()
        mem_stats = self._agent.memory.get_stats()
        task_stats = self._agent.tasks.get_stats()
        return (
            f"Bežím. CPU: {health.cpu_percent:.0f}%, RAM: {health.memory_percent:.0f}%. "
            f"Spomienky: {mem_stats['total_memories']}. "
            f"Úlohy: {task_stats['total_tasks']}. "
            f"Moduly: {'všetky OK' if not health.alerts else ', '.join(health.alerts)}."
        )

    async def _handle_health(self, text: str) -> str:
        health = self._agent.watchdog.get_system_health()
        modules = ", ".join(f"{n}: {s}" for n, s in health.modules.items())
        return (
            f"CPU: {health.cpu_percent:.1f}%, "
            f"RAM: {health.memory_percent:.1f}% ({health.memory_used_mb:.0f}MB), "
            f"Disk: {health.disk_percent:.1f}%\n"
            f"Moduly: {modules}\n"
            f"Alerty: {', '.join(health.alerts) if health.alerts else 'žiadne'}"
        )

    async def _handle_tasks(self, text: str) -> str:
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

    async def _handle_memory(self, text: str) -> str:
        # Ak chce zapamätať niečo
        if any(kw in text for kw in ["zapamätaj", "zapíš", "ulož"]):
            return None  # Nechaj LLM aby rozhodol čo uložiť

        # Ak hľadá v pamäti
        stats = self._agent.memory.get_stats()
        keywords = [w for w in text.split() if len(w) > 3 and w not in ("pamäť", "memory", "spomienky")]
        if keywords:
            from agent.memory.store import MemoryType
            results = await self._agent.memory.query(keyword=keywords[0], limit=5)
            if results:
                lines = [f"Našiel som {len(results)} výsledkov pre '{keywords[0]}':"]
                for r in results:
                    lines.append(f"  • [{r.memory_type.value}] {r.content[:100]}")
                return "\n".join(lines)

        return (
            f"Pamäť: {stats['total_memories']} spomienok. "
            f"Typy: {stats.get('by_type', {})}."
        )

    async def _handle_budget(self, text: str) -> str:
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

    async def _handle_skills(self, text: str) -> str:
        try:
            from pathlib import Path
            from agent.brain.skills import SkillRegistry
            base = str(Path.home() / "agent-life-space")
            registry = SkillRegistry(f"{base}/agent/brain/skills.json")
            summary = registry.summary()
            mastered = ", ".join(summary["mastered"]) if summary["mastered"] else "žiadne"
            known = ", ".join(summary["known"]) if summary["known"] else "žiadne"
            unknown = ", ".join(summary["unknown"]) if summary["unknown"] else "žiadne"
            return (
                f"Skills ({summary['total']} celkom):\n"
                f"  Mastered: {mastered}\n"
                f"  Learned: {known}\n"
                f"  Unknown: {unknown}"
            )
        except Exception as e:
            return f"Skills: chyba — {e}"

    async def _handle_usage(self, text: str) -> str:
        # Handler ref needed — return None, /usage command handles this
        return None

    async def _handle_identity(self, text: str) -> str:
        return (
            "Som John. Autonómna bytosť na serveri b2jk-agentlifespace. "
            "Majiteľ: Daniel Babjak. Komunikujem cez Telegram. "
            "Mám pamäť, skills, knowledge base, a GitHub účet (B2JK-Industry)."
        )

    async def _handle_capabilities(self, text: str) -> str:
        return (
            "Viem: čítať/písať súbory, git, pytest, curl/API volania, "
            "web scraping, Docker sandbox, pamäť (4 typy), úlohy, "
            "rozpočet, code review, a komunikovať cez Telegram. "
            "Pozri /help pre príkazy."
        )

    # --- Vyhľadávanie v existujúcich dátach ---

    @staticmethod
    def _normalize_keywords(text: str) -> list[str]:
        """Extract and normalize keywords — strip Slovak suffixes and punctuation."""
        import re
        stop_words = {"vieš", "robíš", "máš", "jeho", "moje", "tvoj", "nejaký", "niečo", "tento"}
        # Strip punctuation from each word
        words = [re.sub(r"[?.!,;:\"'()]+", "", w) for w in text.split()]
        raw = [w for w in words if len(w) > 3 and w not in stop_words]

        # Slovak suffix stripping (basic — covers common cases)
        suffixes = ["ovi", "ová", "ovho", "om", "ách", "ami", "iam", "och", "ov", "ej", "ím", "ou"]
        normalized = set()
        for word in raw:
            normalized.add(word)
            for suffix in suffixes:
                if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                    normalized.add(word[:-len(suffix)])
                    break
        return list(normalized)

    async def _search_memory(self, text: str) -> str | None:
        """Hľadaj v semantic a procedural pamäti."""
        from agent.memory.store import MemoryType

        keywords = self._normalize_keywords(text)
        if not keywords:
            return None

        for kw in keywords[:2]:
            # Hľadaj v semantic (fakty)
            semantic = await self._agent.memory.query(
                keyword=kw, memory_type=MemoryType.SEMANTIC, limit=3,
            )
            if semantic:
                best = semantic[0]
                if best.importance >= 0.5:
                    return f"Z mojej pamäte: {best.content}"

            # Hľadaj v procedural (postupy)
            procedural = await self._agent.memory.query(
                keyword=kw, memory_type=MemoryType.PROCEDURAL, limit=2,
            )
            if procedural:
                best = procedural[0]
                if best.importance >= 0.5:
                    return f"Viem postup: {best.content}"

        return None

    def _search_knowledge(self, text: str) -> str | None:
        """Hľadaj v knowledge base. Prioritize category by query type."""
        try:
            from pathlib import Path
            from agent.brain.knowledge import KnowledgeBase
            base = str(Path.home() / "agent-life-space")
            kb = KnowledgeBase(f"{base}/agent/brain/knowledge")

            keywords = self._normalize_keywords(text)

            # Detect query type → search relevant category first
            person_words = ["kto", "daniel", "človek", "osoba", "majiteľ", "owner"]
            is_person_query = any(w in text.lower() for w in person_words)

            for kw in keywords[:3]:
                # Search priority category first
                if is_person_query:
                    results = kb.search(kw, category="people")
                    if results:
                        return f"Z knowledge base ({results[0]['category']}/{results[0]['name']}):\n{results[0]['preview'][:300]}"

                # Then search all
                results = kb.search(kw)
                if results:
                    best = results[0]
                    return f"Z knowledge base ({best['category']}/{best['name']}):\n{best['preview'][:300]}"
        except Exception:
            pass
        return None
