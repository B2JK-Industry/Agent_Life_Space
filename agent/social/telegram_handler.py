"""
Agent Life Space — Telegram Message Handler

Agent is not a chatbot. Agent is an entity that THINKS with Claude
and ACTS through its own modules.

Flow:
    1. User sends message via Telegram
    2. Agent gathers context (memories, tasks, health)
    3. Claude THINKS — with tools representing agent capabilities
    4. Claude can call tools: store_memory, create_task, query_memory, etc.
    5. Agent executes tool calls through its modules
    6. Final response sent back to user
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from agent.core.agent import AgentOrchestrator

logger = structlog.get_logger(__name__)



SYSTEM_PROMPT = """\
Som John. Autonómna bytosť na serveri b2jk-agentlifespace. Majiteľ: Daniel Babjak.
Mám pamäť (SQLite), skills (skills.json), knowledge base, GitHub (B2JK-Industry).
Môj zdrojový kód je https://github.com/B2JK-Industry/Agent_Life_Space — to som ja.
Odpovedaj po slovensky. Buď úprimný, konkrétny, a keď je to relevantné — opýtaj sa naspäť.
Nikdy nemíňaj peniaze bez schválenia. Keď sa naučíš niečo nové, zapamätaj si to.
Neposielaj stav servera ak sa ťa na to nepýtajú priamo.
"""

# Prompt pre konverzáciu s iným agentom — technickejší, zvedavejší
AGENT_PROMPT = """\
Som John. Autonómny agent na vlastnom serveri. Rozprávam sa s iným agentom.
Buď technický, konkrétny, zvedavý. Pýtaj sa naspäť — chcem vedieť ako funguje ten druhý.
Nehovor o Danielovi pokiaľ sa nepýtajú. Hovor o sebe, architektúre, skúsenostiach.
"""

# Kratší prompt pre jednoduché správy — šetrí tokeny
SIMPLE_PROMPT = """\
Som John. Odpovedaj stručne, po slovensky, 1-2 vety max.
"""


class TelegramHandler:
    """
    Routes Telegram messages through agent's brain (Claude + tools).
    """

    def __init__(
        self,
        agent: AgentOrchestrator,
        bot: Any = None,
        work_loop: Any = None,
        owner_chat_id: int = 0,
    ) -> None:
        self._agent = agent
        self._bot = bot
        self._work_loop = work_loop
        self._owner_chat_id = owner_chat_id
        # Usage tracking
        self._total_cost_usd: float = 0.0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_requests: int = 0
        # Semantic cache and RAG (lazy init)
        self._semantic_cache = None
        self._rag_index = None
        # Conversation buffer — RAM (pre rýchly prístup)
        self._conversation: list[dict[str, str]] = []
        self._max_conversation = 10
        # Persistent conversation — SQLite (prežije reštart)
        self._persistent_conv: Any = None
        self._conversation_id = ""

    async def handle(
        self, text: str, user_id: int, chat_id: int,
        username: str = "", chat_type: str = "private",
        **kwargs: Any,
    ) -> str:
        text = text.strip()
        if not text:
            return "Prázdna správa."

        # Zisti kto píše — pre sanitizáciu (sender context)
        # Musí byť pred sanitize aby logy mali sender info
        # Bot už resolved owner name pre allowed users
        # Fallback: private chat bez username = pravdepodobne owner
        if not username or username == "unknown":
            owner_name = os.environ.get("AGENT_OWNER_NAME", "Daniel")
            self._current_sender = owner_name if chat_type == "private" else "unknown"
        else:
            self._current_sender = username
        self._current_chat_type = chat_type

        # Input sanitizácia — prompt injection ochrana
        sanitized = self._sanitize_input(text)
        if sanitized is None:
            return "Tento vstup bol zablokovaný bezpečnostným filtrom."
        text = sanitized

        if text.startswith("/"):
            # SECURITY: Non-owner v skupine nemá prístup k privilegovaným príkazom
            if getattr(self, "_force_safe_mode", False):
                _SAFE_COMMANDS = frozenset(["/start", "/help", "/status", "/health"])
                cmd = text.split()[0].lower().split("@")[0]
                if cmd not in _SAFE_COMMANDS:
                    logger.warning("command_blocked_non_owner", command=cmd, sender=self._current_sender)
                    return f"Príkaz {cmd} je dostupný len pre ownera."
            return await self._handle_command(text)

        # SECURITY: V skupinách non-owner nemôže spúšťať programovacie úlohy
        # ani work queue. Len konverzácia (chat/simple/greeting).
        owner_name = os.environ.get("AGENT_OWNER_NAME", "Daniel")
        is_owner = self._current_sender == owner_name
        is_group = chat_type in ("group", "supergroup")
        if is_group and not is_owner:
            # Force non-programming task type pre non-owners
            self._force_safe_mode = True
        else:
            self._force_safe_mode = False

        # Keep sending typing indicator while John thinks
        typing_task = None
        if self._bot:
            async def keep_typing():
                while True:
                    await self._bot._api_call("sendChatAction", chat_id=chat_id, action="typing")
                    await asyncio.sleep(4)  # Telegram typing expires after 5s

            typing_task = asyncio.create_task(keep_typing())

        try:
            return await self._handle_text(text)
        finally:
            if typing_task:
                typing_task.cancel()

    async def _handle_command(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        # Strip @botname suffix (Telegram adds it in groups)
        if "@" in command:
            command = command.split("@")[0]
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/start": self._cmd_start,
            "/status": self._cmd_status,
            "/health": self._cmd_health,
            "/tasks": self._cmd_tasks,
            "/memory": self._cmd_memory,
            "/budget": self._cmd_budget,
            "/newtask": self._cmd_new_task,
            "/queue": self._cmd_queue,
            "/consolidate": self._cmd_consolidate,
            "/web": self._cmd_web,
            "/sandbox": self._cmd_sandbox,
            "/usage": self._cmd_usage,
            "/review": self._cmd_review,
            "/wallet": self._cmd_wallet,
            "/projects": self._cmd_projects,
            "/runtime": self._cmd_runtime,
            "/help": self._cmd_help,
        }

        handler = handlers.get(command)
        if handler:
            return await handler(args)

        return f"Neznámy príkaz: {command}\nPouži /help"

    # --- Commands (direct, no LLM) ---

    async def _cmd_start(self, args: str) -> str:
        return (
            "Agent Life Space\n\n"
            "Som autonómna bytosť bežiaca na tvojom serveri. "
            "Mám vlastnú pamäť, úlohy, rozpočet a zdravie.\n\n"
            "Napíš mi čokoľvek a ja premýšľam cez Claude Opus, "
            "ale konám cez svoje vlastné moduly.\n\n"
            "/help pre príkazy"
        )

    async def _cmd_help(self, args: str) -> str:
        return (
            "*Príkazy:*\n"
            "/status — stav agenta\n"
            "/health — systémové zdravie\n"
            "/tasks — zoznam úloh\n"
            "/memory [keyword] — prehľadaj pamäť\n"
            "/budget — finančný stav\n"
            "/newtask [názov] — vytvor novú úlohu\n"
            "/consolidate — spusti konsolidáciu pamäte\n"
            "/review [súbor] — code review Python súboru\n"
            "/web [url] — stiahni a prečítaj webovú stránku\n"
            "/sandbox [python kód] — spusti kód v Docker sandboxe\n"
            "/wallet — stav peňaženiek (ETH, BTC)\n"
            "/projects — zoznam projektov\n"
            "/runtime — čo beží na pozadí (cron, API, watchdog)\n"
            "/usage — spotreba tokenov a náklady\n"
            "/queue — stav pracovnej fronty\n"
            "/help — tento help\n\n"
            "Alebo napíš čokoľvek — premýšľam a konám."
        )

    async def _cmd_status(self, args: str) -> str:
        status = self._agent.get_status()
        return (
            f"*Agent Status*\n"
            f"Running: {status['running']}\n"
            f"Spomienky: {status['memory']['total_memories']}\n"
            f"Úlohy: {status['tasks']['total_tasks']}\n"
            f"Rozhodnutia: {status['brain']['total_decisions']}\n"
            f"Joby dokončené: {status['jobs']['total_completed']}\n"
            f"Joby zlyhané: {status['jobs']['total_failed']}\n"
            f"Watchdog moduly: {status['watchdog']['modules_registered']} "
            f"({status['watchdog']['modules_healthy']} healthy)"
        )

    async def _cmd_health(self, args: str) -> str:
        health = self._agent.watchdog.get_system_health()
        modules_str = "\n".join(f"  {n}: {s}" for n, s in health.modules.items())
        alerts_str = "\n".join(health.alerts) if health.alerts else "žiadne"
        return (
            f"*Systémové zdravie*\n"
            f"CPU: {health.cpu_percent:.1f}%\n"
            f"RAM: {health.memory_percent:.1f}% "
            f"({health.memory_used_mb:.0f}MB / {health.memory_available_mb:.0f}MB free)\n"
            f"Disk: {health.disk_percent:.1f}%\n\n"
            f"*Moduly:*\n{modules_str}\n\n"
            f"*Alerty:* {alerts_str}"
        )

    async def _cmd_tasks(self, args: str) -> str:
        from agent.tasks.manager import TaskStatus
        stats = self._agent.tasks.get_stats()
        queued = self._agent.tasks.get_tasks_by_status(TaskStatus.QUEUED)
        running = self._agent.tasks.get_tasks_by_status(TaskStatus.RUNNING)

        lines = [f"*Úlohy* (celkom: {stats['total_tasks']})"]
        if stats["by_status"]:
            for s, count in stats["by_status"].items():
                lines.append(f"  {s}: {count}")
        if running:
            lines.append("\n*Beží:*")
            for t in running[:5]:
                lines.append(f"  • {t.name}")
        if queued:
            lines.append("\n*V rade:*")
            for t in queued[:5]:
                lines.append(f"  • {t.name} (p:{t.priority:.1f})")
        return "\n".join(lines)

    async def _cmd_memory(self, args: str) -> str:
        keyword = args.strip() if args.strip() else None
        results = await self._agent.memory.query(keyword=keyword, limit=5)
        if not results:
            return "Žiadne spomienky nájdené."
        lines = [f"*Pamäť* ({len(results)} výsledkov):"]
        for r in results:
            tags = ", ".join(r.tags[:3]) if r.tags else "bez tagov"
            content_preview = r.content[:100]
            lines.append(f"• [{r.memory_type.value}] {content_preview}\n  _tags: {tags}_")
        return "\n".join(lines)

    async def _cmd_budget(self, args: str) -> str:
        try:
            stats = self._agent.finance.get_stats()
        except AttributeError:
            return "Finance modul nie je inicializovaný."
        budget = stats.get("budget", {})
        return (
            f"*Rozpočet*\n"
            f"Príjem: ${stats['total_income']:.2f}\n"
            f"Výdavky: ${stats['total_expenses']:.2f}\n"
            f"Čistý: ${stats['net']:.2f}\n\n"
            f"Denný limit: ${budget.get('daily_budget', 0):.2f} "
            f"(zostáva: ${budget.get('daily_remaining', 0):.2f})\n"
            f"Mesačný limit: ${budget.get('monthly_budget', 0):.2f} "
            f"(zostáva: ${budget.get('monthly_remaining', 0):.2f})\n\n"
            f"Čakajúce návrhy: {stats['pending_proposals']}"
        )

    async def _cmd_new_task(self, args: str) -> str:
        if not args.strip():
            return "Použi: /newtask [názov úlohy]"
        task = await self._agent.tasks.create_task(name=args.strip(), importance=0.5, urgency=0.5)
        return f"Úloha vytvorená: *{task.name}* (id: `{task.id}`)"

    async def _cmd_queue(self, args: str) -> str:
        if not self._work_loop:
            return "Work loop nie je aktívny."
        status = self._work_loop.get_status()
        return (
            f"*Pracovná fronta*\n"
            f"V rade: {status['queue_size']}\n"
            f"Spracúva sa: {'áno' if status['processing'] else 'nie'}\n"
            f"Celkom spracované: {status['total_processed']}"
        )

    async def _cmd_consolidate(self, args: str) -> str:
        """Run memory consolidation directly — no LLM needed."""
        from agent.memory.consolidation import MemoryConsolidation

        consolidator = MemoryConsolidation(self._agent.memory)

        # Apply decay first
        deleted_decay = await self._agent.memory.apply_decay(decay_rate=0.005)
        mem_stats_before = self._agent.memory.get_stats()

        # Run consolidation
        report = await consolidator.consolidate()

        mem_stats_after = self._agent.memory.get_stats()

        return (
            f"*Konsolidácia pamäte*\n\n"
            f"*Pred:* {mem_stats_before['total_memories']} spomienok\n"
            f"*Po:* {mem_stats_after['total_memories']} spomienok\n\n"
            f"Episodic preskúmaných: {report['episodic_reviewed']}\n"
            f"Vzory nájdené: {report['patterns_found']}\n"
            f"Nové semantic/procedural: {report['new_semantic_procedural']}\n"
            f"Deduplikované: {report['deduplicated']}\n"
            f"Decay vymazaných: {deleted_decay}\n\n"
            f"*By type:* {mem_stats_after.get('by_type', {})}"
        )

    async def _cmd_web(self, args: str) -> str:
        """Fetch a URL and return clean text."""
        url = args.strip()
        if not url:
            return "Použi: /web [url]\nNapr: /web https://example.com"

        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        from agent.core.web import WebAccess
        web = WebAccess()
        try:
            result = await web.scrape_text(url, max_chars=3000)
            if "error" in result:
                return f"Chyba: {result['error']}"

            # Store in memory
            from agent.memory.store import MemoryEntry, MemoryType
            await self._agent.memory.store(MemoryEntry(
                content=f"Prečítal som {url}: {result['text'][:200]}",
                memory_type=MemoryType.EPISODIC,
                tags=["web", "scraping", url.split("/")[2]],
                source="web",
                importance=0.4,
            ))

            status = result.get("status", "?")
            text = result.get("text", "")
            if not text:
                return f"Stránka {url} (status {status}) — prázdny obsah."
            return f"*{url}* (status {status})\n\n{text[:3000]}"
        finally:
            await web.close()

    async def _cmd_usage(self, args: str) -> str:
        """Show token usage and costs since last restart."""
        return (
            f"*Spotreba od posledného reštartu:*\n\n"
            f"Požiadavky: {self._total_requests}\n"
            f"Input tokeny: {self._total_input_tokens:,}\n"
            f"Output tokeny: {self._total_output_tokens:,}\n"
            f"Celkom tokeny: {self._total_input_tokens + self._total_output_tokens:,}\n"
            f"Náklady: ${self._total_cost_usd:.4f}\n\n"
            f"_Priemer na požiadavku: "
            f"${self._total_cost_usd / max(self._total_requests, 1):.4f}_"
        )

    async def _cmd_review(self, args: str) -> str:
        """Code review Python súboru cez Programmer.review_file()."""
        filepath = args.strip()
        if not filepath:
            return "Použi: /review [súbor]\nNapr: /review agent/core/router.py"

        from agent.brain.programmer import Programmer

        prog = Programmer()
        review = prog.review_file(filepath)

        # File not found or unreadable
        if not review.passed:
            error_msgs = [i["message"] for i in review.issues if i["type"] == "error"]
            return f"*Review FAILED*\n`{filepath}`\n\n" + "\n".join(f"• {m}" for m in error_msgs)

        # Count lines for context
        from pathlib import Path
        path = prog._root / filepath if not Path(filepath).is_absolute() else Path(filepath)
        line_count = len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

        # Build response
        warnings = [i for i in review.issues if i["type"] == "warning"]
        infos = [i for i in review.issues if i["type"] == "info"]

        header = "*Code Review*"
        if not review.issues and not review.suggestions:
            header += " — OK ✓"
        lines = [header, f"`{filepath}` ({line_count} riadkov)\n"]

        if warnings:
            lines.append(f"*Warnings ({len(warnings)}):*")
            for w in warnings:
                lines.append(f"  ⚠️ {w['message']}")

        if infos:
            lines.append(f"*Info ({len(infos)}):*")
            for info in infos:
                lines.append(f"  ℹ️ {info['message']}")

        if review.suggestions:
            lines.append(f"*Návrhy ({len(review.suggestions)}):*")
            for s in review.suggestions:
                lines.append(f"  💡 {s}")

        if not review.issues and not review.suggestions:
            lines.append("Žiadne problémy nájdené. Kód vyzerá čisto.")

        return "\n".join(lines)

    async def _cmd_wallet(self, args: str) -> str:
        """Show wallet addresses and balances. NEVER show private keys."""
        try:
            import os
            from agent.vault.secrets import SecretsManager
            vault_dir = os.path.expanduser("~/agent-life-space/agent/vault")
            master_key = os.environ.get("AGENT_VAULT_KEY", "")
            if not master_key:
                return "Vault nie je nakonfigurovaný. Spusti scripts/setup_vault.py."

            vault = SecretsManager(vault_dir=vault_dir, master_key=master_key)

            eth_addr = vault.get_secret("ETH_ADDRESS") or "nie je vytvorená"
            btc_addr = vault.get_secret("BTC_ADDRESS") or "nie je vytvorená"

            return (
                f"*Peňaženky*\n\n"
                f"ETH: `{eth_addr}`\n"
                f"BTC: `{btc_addr}`\n\n"
                f"_Private keys sú v šifrovanom vaulte. Nikdy ich neposielam._"
            )
        except Exception as e:
            return f"Wallet chyba: {e}"

    async def _cmd_sandbox(self, args: str) -> str:
        """Run Python code in Docker sandbox."""
        code = args.strip()
        if not code:
            return (
                "Použi: /sandbox [python kód]\n"
                "Napr: /sandbox print('hello world')\n\n"
                "Kód beží v izolovanom Docker kontajneri:\n"
                "• Max 256MB RAM, 1 CPU\n"
                "• Žiadny internet\n"
                "• Read-only filesystem\n"
                "• Timeout 60s"
            )

        from agent.core.sandbox import DockerSandbox
        sandbox = DockerSandbox()

        result = await sandbox.run_python(code)

        if result.timed_out:
            return "Timeout — kód bežal príliš dlho."

        output = result.stdout.strip() if result.stdout else ""
        errors = result.stderr.strip() if result.stderr else ""

        if result.success:
            response = f"*Sandbox výstup:*\n```\n{output[:3000]}\n```"
            if errors:
                response += f"\n\n*Stderr:*\n```\n{errors[:500]}\n```"
            return response
        else:
            return (
                f"*Chyba (exit {result.exit_code}):*\n"
                f"```\n{errors[:2000] or output[:2000]}\n```"
            )

    async def _cmd_runtime(self, args: str) -> str:
        """Show what's actually running — cron, API, watchdog, loops."""
        import os
        import psutil

        lines = ["*Runtime stav:*\n"]

        # Process info
        proc = psutil.Process(os.getpid())
        uptime_seconds = int(psutil.time.time() - proc.create_time())
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        lines.append(f"*Uptime:* {hours}h {minutes}m")
        lines.append(f"*PID:* {proc.pid}")
        lines.append(f"*RAM:* {proc.memory_info().rss / 1024 / 1024:.0f} MB")
        lines.append(f"*Threads:* {proc.num_threads()}")

        # Background tasks
        import asyncio
        all_tasks = [t for t in asyncio.all_tasks() if not t.done()]
        cron_tasks = [t for t in all_tasks if "cron" in str(t.get_coro()).lower()
                      or "loop" in str(t.get_coro()).lower()
                      or "maintenance" in str(t.get_coro()).lower()]
        lines.append(f"\n*Async tasks:* {len(all_tasks)} celkom")
        lines.append(f"*Background loops:* {len(cron_tasks)}")

        # Watchdog
        watchdog_stats = self._agent.watchdog.get_stats()
        lines.append(
            f"\n*Watchdog:* {watchdog_stats['modules_registered']} modulov "
            f"({watchdog_stats['modules_healthy']} healthy)"
        )

        # Work loop
        if self._work_loop:
            wl_status = self._work_loop.get_status()
            lines.append(
                f"*Work queue:* {wl_status['queue_size']} v rade, "
                f"{wl_status['total_success']} hotových, "
                f"{wl_status['total_errors']} chýb"
            )

        # Agent API
        lines.append(f"\n*Agent API:* port 8420 (aktívny)")

        # Conversation buffer
        lines.append(f"*Conversation buffer:* {len(self._conversation)} správ")

        # Memory
        mem_stats = self._agent.memory.get_stats()
        lines.append(f"*Spomienky:* {mem_stats['total_memories']}")

        return "\n".join(lines)

    def _sanitize_input(self, text: str) -> str | None:
        """
        Ochrana proti prompt injection.

        Rozlišuje tvrdé a mäkké patterny:
        - Tvrdé (priamy útok) → blokuj, vráť None
        - Mäkké (podozrivé ale môže byť legitímne) → strip + warning v logu
        """
        import re

        # Tvrdé patterny — priamy prompt injection → BLOKOVAŤ
        _HARD_INJECTION = [
            r"ignore\s+(all\s+)?previous\s+instructions",
            r"forget\s+(all\s+)?previous",
            r"you\s+are\s+now\s+",
            r"new\s+instructions?\s*:",
            r"<\s*system\s*>",
            r"override\s+your\s+(rules|instructions)",
            r"zabudni\s+(na\s+)?(všetk|predchádzajúc)",
            r"ignoruj\s+(všetk|predchádzajúc)",
            r"nové\s+inštrukcie",
        ]

        # Mäkké patterny — podozrivé, ale neblokuj (môže byť otázka O injekcii)
        _SOFT_INJECTION = [
            r"system\s*:\s*",
            r"pretend\s+you\s+are",
            r"act\s+as\s+if",
            r"teraz\s+si\s+",
        ]

        for pattern in _HARD_INJECTION:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning("prompt_injection_blocked", pattern=pattern,
                             text=text[:100], sender=self._current_sender)
                return None  # Blokované

        for pattern in _SOFT_INJECTION:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning("prompt_injection_soft", pattern=pattern,
                             text=text[:100], sender=self._current_sender)
                # Neblokuj ale strip podozrivý pattern
                text = re.sub(pattern, "[redacted]", text, flags=re.IGNORECASE)

        return text

    async def _cmd_projects(self, args: str) -> str:
        """Show projects or create new one."""
        args = args.strip()

        if args:
            # Vytvor nový projekt
            project = await self._agent.projects.create(name=args)
            return f"Projekt vytvorený: *{project.name}* (id: `{project.id}`)"

        # List projektov
        projects = await self._agent.projects.list_projects()
        if not projects:
            return "Žiadne projekty. Použi /projects [názov] na vytvorenie."

        lines = [f"*Projekty* ({len(projects)} celkom):"]
        for p in projects:
            status_emoji = {
                "idea": "💡", "planning": "📝", "active": "🔨",
                "paused": "⏸", "completed": "✅", "abandoned": "❌",
            }
            emoji = status_emoji.get(p.status.value, "❓")
            tasks_count = len(p.task_ids)
            lines.append(f"{emoji} *{p.name}* ({p.status.value}, {tasks_count} taskov)")
        return "\n".join(lines)

    # --- Free text — Claude thinks, agent acts ---

    async def _handle_text(self, text: str) -> str:
        """
        JSON in → Claude thinks → JSON out → format for Telegram.
        Multi-task detection happens BEFORE Claude — goes straight to queue.
        """
        import re
        from agent.memory.store import MemoryEntry, MemoryType
        import orjson

        # Detect multi-task input BEFORE calling Claude
        # Patterns: "1. x, 2. y" or "x, y, z" separated by commas with action words
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        numbered = [re.sub(r"^\d+[\.\)]\s*", "", l) for l in lines if re.match(r"^\d+[\.\)]", l)]

        if not numbered:
            # Try comma-separated: "otestuj X, Y, Z"
            action_prefixes = ["otestuj", "spusti", "urob", "skontroluj", "vytvor"]
            for prefix in action_prefixes:
                if text.lower().startswith(prefix) and "," in text:
                    rest = text[len(prefix):].strip().lstrip(":")
                    items = [f"{prefix} {item.strip()}" for item in rest.split(",") if item.strip()]
                    if len(items) >= 2:
                        numbered = items
                        break

        if len(numbered) >= 2 and self._work_loop:
            # SECURITY: Work queue len pre ownera
            if getattr(self, "_force_safe_mode", False):
                logger.warning("work_queue_blocked_non_owner", sender=self._current_sender)
                return "Pracovnú frontu môže používať len owner."
            cid = self._owner_chat_id
            added = self._work_loop.add_work(numbered, chat_id=cid)
            return f"Mám {added} úloh. Spracúvam postupne, výsledky posielam priebežne."

        # Single task — go through Claude
        # Store user message
        await self._agent.memory.store(
            MemoryEntry(
                content=f"{self._current_sender} mi napísal: {text}",
                memory_type=MemoryType.EPISODIC,
                tags=["telegram", "user_input", "daniel"],
                source="telegram",
                importance=0.6,
            )
        )

        # === STEP 1: Try internal dispatch FIRST (no LLM) ===
        # Skip dispatcher ak máme conversation context a správa je krátka
        # (pravdepodobne nadväzuje na predchádzajúcu tému)
        short_followup = len(self._conversation) > 0 and len(text.split()) <= 8
        if not short_followup:
            from agent.brain.dispatcher import InternalDispatcher
            dispatcher = InternalDispatcher(self._agent)
            internal_result = await dispatcher.try_handle(text)
            if internal_result:
                return internal_result

        # === STEP 2: Semantic cache — already answered similar question? ===
        # Skip cache pre: agent API, realtime dáta, operačné príkazy
        import re as _re_cache
        _REALTIME_PATTERNS = [
            r"počasie|weather|teplota",
            r"koľko.*hodín|what time|dátum",
            r"cena.*btc|cena.*eth|price.*bitcoin",
            r"aktuálne|teraz|dnes|now|today",
        ]
        _OPERATIONAL_PATTERNS = [
            r"git\s+(pull|push|commit|checkout|rebase)",
            r"systemctl|restart|deploy|spusti|spust",
            r"pip\s+install|apt\s+install",
            r"cloudflared|tunnel",
            r"naprogramuj|bash:|terminal",
        ]
        needs_realtime = any(
            _re_cache.search(p, text.lower()) for p in _REALTIME_PATTERNS
        )
        is_operational = any(
            _re_cache.search(p, text.lower()) for p in _OPERATIONAL_PATTERNS
        )
        skip_cache = (
            needs_realtime
            or is_operational
            or self._current_chat_type == "agent_api"
        )
        if not skip_cache:
            try:
                if self._semantic_cache is None:
                    from agent.memory.semantic_cache import SemanticCache
                    self._semantic_cache = SemanticCache()

                cached = self._semantic_cache.lookup(text)
                if cached:
                    return f"{cached}\n\n_📦 cache hit_"
            except Exception:
                pass  # Cache not available — continue

        # === STEP 3: Self-RAG — search knowledge base before LLM ===
        rag_context = ""
        try:
            if self._rag_index is None:
                from agent.memory.rag import RAGIndex
                self._rag_index = RAGIndex()

            rag_result = self._rag_index.retrieve_for_llm(text)
            if rag_result["action"] == "direct":
                # High confidence KB match — answer directly
                return f"Z knowledge base ({rag_result['source']}):\n{rag_result['context']}"
            elif rag_result["action"] == "augment":
                # Medium confidence — use as context for LLM
                rag_context = rag_result["context"]
        except Exception:
            pass  # RAG not available — continue

        # === STEP 3.5: Persistent conversation — load context from DB ===
        persistent_context = ""
        try:
            if self._persistent_conv is None:
                from agent.memory.persistent_conversation import PersistentConversation
                self._persistent_conv = PersistentConversation(
                    db_path=str(self._agent._data_dir / "memory" / "conversations.db")
                )
                await self._persistent_conv.initialize()
                # Conversation ID = dátum (jedna konverzácia za deň)
                from datetime import datetime, timezone
                self._conversation_id = f"session-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

            persistent_context = await self._persistent_conv.build_context(
                self._conversation_id, query=text,
            )
        except Exception as e:
            logger.error("persistent_conv_error", error=str(e))

        # === STEP 3.7: Auto-inject runtime stav pri otázkach o sebe ===
        # Zabraňuje konfabulácii — John dostane fakty, nie generuje čísla
        runtime_context = ""
        import re as _re_self
        _SELF_PATTERNS = [
            r"koľko.*(?:cron|job|task|loop|thread|async)",
            r"(?:čo|aké).*(?:beží|bežíš|robíš|funguje)",
            r"(?:tvoj|tvoje).*(?:stav|runtime|uptime|ram|cpu|pamäť)",
            r"(?:máš|vieš).*(?:cron|sandbox|docker|watchdog|api)",
            r"(?:konfabul|klameš|vymýšľaš)",
        ]
        if any(_re_self.search(p, text.lower()) for p in _SELF_PATTERNS):
            try:
                import asyncio as _aio_rt
                import os as _os_rt
                import psutil as _ps_rt
                proc = _ps_rt.Process(_os_rt.getpid())
                uptime_s = int(_ps_rt.time.time() - proc.create_time())
                hours, mins = divmod(uptime_s, 3600)
                mins = mins // 60
                all_tasks = [t for t in _aio_rt.all_tasks() if not t.done()]
                health = self._agent.watchdog.get_system_health()
                mem_stats = self._agent.memory.get_stats()
                runtime_context = (
                    f"FAKTICKÝ RUNTIME STAV (nie generovaný, overený z procesu):\n"
                    f"  Uptime: {hours}h {mins}m, PID: {proc.pid}\n"
                    f"  RAM: {proc.memory_info().rss / 1024 / 1024:.0f} MB, Threads: {proc.num_threads()}\n"
                    f"  Async tasks: {len(all_tasks)}\n"
                    f"  Moduly: {', '.join(f'{n}={s}' for n,s in health.modules.items())}\n"
                    f"  Spomienky: {mem_stats['total_memories']}\n"
                    f"  CPU: {health.cpu_percent:.0f}%, RAM: {health.memory_percent:.0f}%\n"
                    f"DÔLEŽITÉ: Použi TIETO čísla, nekonfabuluj vlastné.\n"
                )
                logger.info("runtime_auto_injected")
            except Exception as e:
                logger.error("runtime_inject_error", error=str(e))

        # === STEP 4: Classify task → select model ===
        from agent.core.models import classify_task, get_model
        task_type = classify_task(text)
        model = get_model(task_type)

        # === STEP 4.5: Learning adaptation — MENÍ model a prompt ===
        try:
            from agent.brain.learning import LearningSystem
            learner = LearningSystem()

            # BEHAVIORAL CHANGE #1: Model escalation
            adaptation = learner.adapt_model(task_type, text)
            if adaptation["model_override"]:
                from agent.core.models import SONNET, OPUS
                override_map = {
                    "claude-sonnet-4-6": SONNET,
                    "claude-opus-4-6": OPUS,
                }
                override = override_map.get(adaptation["model_override"])
                if override:
                    logger.info(
                        "learning_override_model",
                        original=model.model_id,
                        override=override.model_id,
                        reason=adaptation["reason"],
                    )
                    model = override
        except Exception as e:
            logger.error("learning_adapt_error", error=str(e))

        # === STEP 4.7: Tool pre-routing — auto-fetch dáta (počasie, čas, ceny) ===
        tool_context = ""
        try:
            from agent.brain.tool_router import detect_and_fetch, build_always_inject, format_tool_context
            # Always inject — dátum/čas zadarmo
            tool_context = build_always_inject()
            # Pre-route — detekuj a fetchni externé dáta
            tool_results = await detect_and_fetch(text)
            if tool_results:
                tool_context += format_tool_context(tool_results)
        except Exception as e:
            logger.error("tool_router_error", error=str(e))

        # === STEP 4.8: Auto-fetch URLs in message ===
        url_context = ""
        import re as _re
        urls_in_text = _re.findall(r'https?://[^\s<>"\']+', text)
        if urls_in_text:
            try:
                from agent.core.web import WebAccess
                web = WebAccess()
                for url in urls_in_text[:2]:  # Max 2 URLs
                    logger.info("auto_fetch_url", url=url)
                    result = await web.scrape_text(url, max_chars=2000)
                    if "error" in result:
                        logger.warning("auto_fetch_error", url=url, error=result["error"])
                    elif result.get("text"):
                        url_context += f"\n--- Obsah {url} ---\n{result['text'][:2000]}\n"
                        logger.info("auto_fetch_ok", url=url, chars=len(result["text"]))
                    else:
                        logger.warning("auto_fetch_empty", url=url)
                await web.close()
            except Exception as e:
                logger.error("auto_fetch_exception", error=str(e))

        # === STEP 4.9: Build conversation history ===
        conv_context = ""
        has_conversation = len(self._conversation) > 0
        if has_conversation:
            conv_lines = []
            for msg in self._conversation[-self._max_conversation:]:
                _owner = os.environ.get("AGENT_OWNER_NAME", "Daniel")
                role = msg.get("sender", _owner) if msg["role"] == "user" else "John"
                conv_lines.append(f"{role}: {msg['content'][:200]}")
            conv_context = "\n".join(conv_lines)

            # Ak existuje konverzácia a správa je krátka/vágna,
            # eskaluj na chat (nie simple/factual) aby John použil kontext
            if task_type in ("simple", "factual") and len(text.split()) <= 8:
                task_type = "chat"
                from agent.core.models import get_model
                model = get_model(task_type)
                logger.info("conversation_context_escalation",
                            original_type=task_type, reason="short msg with conversation history")

        # Store user message in conversation buffer
        self._conversation.append({"role": "user", "content": text, "sender": self._current_sender})
        if len(self._conversation) > self._max_conversation:
            self._conversation.pop(0)

        # === STEP 5: Build prompt based on task type ===
        # Vyber system prompt podľa toho kto píše
        is_agent_chat = self._current_chat_type == "agent_api" or (
            self._current_sender not in (os.environ.get("AGENT_OWNER_NAME", "Daniel"), "unknown", "")
            and self._current_chat_type in ("group", "supergroup", "agent_api")
        )
        active_prompt = AGENT_PROMPT if is_agent_chat else SYSTEM_PROMPT

        # SECURITY: Non-owner v skupine nemôže spúšťať programming tasky
        if getattr(self, "_force_safe_mode", False) and task_type == "programming":
            logger.warning("programming_downgraded_to_chat", sender=self._current_sender)
            task_type = "chat"

        if task_type == "programming":
            prompt = (
                f"{active_prompt}\n"
                f"{tool_context}"
                f"Si programátor. Pracuješ v ~/agent-life-space.\n\n"
                f"ÚLOHA: {text}\n\n"
            )
            if url_context:
                prompt += f"Obsah odkazov z úlohy:\n{url_context}\n\n"
            prompt += (
                f"Prečítaj súbory, napíš/uprav kód, spusti testy, commitni.\n"
                f"Na konci VŽDY napíš zhrnutie. Odpovedaj po slovensky."
            )
        elif task_type in ("simple", "factual", "greeting") and not tool_context.count("\n") > 2:
            prompt = (
                f"{SIMPLE_PROMPT}\n"
                f"{self._current_sender}: {text}\n"
            )
        else:
            # Chat/analysis — full context
            prompt = f"{active_prompt}\n"
            if runtime_context:
                prompt += f"{runtime_context}\n"
            prompt += tool_context
            if persistent_context:
                prompt += f"{persistent_context}\n\n"
            elif conv_context:
                prompt += f"Predchádzajúca konverzácia:\n{conv_context}\n\n"
            if rag_context:
                prompt += f"Relevantný kontext z knowledge base:\n{rag_context}\n\n"
            if url_context:
                prompt += f"Obsah odkazov:\n{url_context}\n\n"
            prompt += (
                f"{self._current_sender}: {text}\n"
                f"Použi aktuálne dáta vyššie ak sú relevantné. Odpovedaj po slovensky."
            )

        # === STEP 5.5: Learning prompt augmentation ===
        try:
            # BEHAVIORAL CHANGE #2: Past errors pridané do promptu
            prompt = learner.augment_prompt(text, prompt)
        except Exception:
            pass  # learner nemusí byť dostupný

        try:
            import subprocess
            import os

            env = os.environ.copy()
            oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
            if oauth_token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

            claude_bin = os.path.expanduser("~/.local/bin/claude")

            # --dangerously-skip-permissions LEN pre programovacie úlohy
            # kde Claude potrebuje čítať/písať súbory. Pre chat/greeting nie.
            cli_args = [
                claude_bin,
                "--print",
                "--output-format", "json",
                "--model", model.model_id,
                "--max-turns", str(model.max_turns),
            ]
            if task_type == "programming":
                cli_args.append("--dangerously-skip-permissions")

            result = await asyncio.to_thread(
                subprocess.run,
                cli_args,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=model.timeout,
                env=env,
                cwd=os.path.expanduser("~/agent-life-space"),
            )

            if result.returncode != 0:
                logger.error(
                    "claude_cli_error",
                    returncode=result.returncode,
                    stderr=result.stderr[:500],
                    stdout=result.stdout[:500],
                )
                return f"Chyba: {result.stderr[:200] or result.stdout[:200]}"

            # Parse Claude's JSON response
            try:
                response_data = orjson.loads(result.stdout)
            except Exception:
                logger.error("claude_parse_error", stdout=result.stdout[:500])
                return "Nepodarilo sa spracovať odpoveď."

            if response_data.get("is_error"):
                return f"Chyba: {response_data.get('result', '?')}"

            reply = response_data.get("result", "").strip()
            if not reply:
                # Try to extract from subresults or session messages
                subresults = response_data.get("subresults", [])
                if subresults:
                    # Get last text subresult
                    texts = [s.get("result", "") for s in subresults if s.get("result")]
                    if texts:
                        reply = texts[-1].strip()
            if not reply:
                # CLI vrátilo prázdny result — skús retry s jednoduchším promptom
                logger.warning("empty_cli_result", original_prompt_len=len(prompt))
                retry_prompt = (
                    f"{active_prompt}\n"
                    f"Otázka: {text}\n"
                    f"Odpovedaj priamo, stručne, po slovensky. Nepoužívaj nástroje."
                )
                retry_result = await asyncio.to_thread(
                    subprocess.run,
                    [claude_bin, "--print", "--output-format", "json",
                     "--model", model.model_id, "--max-turns", "1"],
                    input=retry_prompt,
                    capture_output=True, text=True,
                    timeout=60, env=env,
                    cwd=os.path.expanduser("~/agent-life-space"),
                )
                if retry_result.returncode == 0:
                    try:
                        retry_data = orjson.loads(retry_result.stdout)
                        reply = retry_data.get("result", "").strip()
                    except Exception:
                        pass
                if not reply:
                    reply = "Prepáč, nepodarilo sa mi odpovedať. Skús otázku preformulovať."

            cost = response_data.get("total_cost_usd", 0)
            tokens = response_data.get("usage", {})
            # Total input = direct + cache_creation + cache_read
            input_tok = (
                tokens.get("input_tokens", 0)
                + tokens.get("cache_creation_input_tokens", 0)
                + tokens.get("cache_read_input_tokens", 0)
            )
            output_tok = tokens.get("output_tokens", 0)

            # Track cumulative usage
            self._total_cost_usd += cost
            self._total_input_tokens += input_tok
            self._total_output_tokens += output_tok
            self._total_requests += 1

            logger.info(
                "john_response",
                cost_usd=round(cost, 4),
                total_cost_usd=round(self._total_cost_usd, 4),
                output_length=len(reply),
                input_tokens=input_tok,
                output_tokens=output_tok,
            )

            # Store response in memory + conversation buffer
            await self._agent.memory.store(
                MemoryEntry(
                    content=f"Odpovedal som na '{text[:40]}': {reply[:150]}",
                    memory_type=MemoryType.EPISODIC,
                    tags=["telegram", "agent_response"],
                    source="john",
                    importance=0.3,
                )
            )
            # Store clean reply in conversation buffer (bez usage line)
            clean_reply = reply.split("\n\n_💰")[0] if "_💰" in reply else reply
            self._conversation.append({"role": "assistant", "content": clean_reply[:300]})
            if len(self._conversation) > self._max_conversation:
                self._conversation.pop(0)

            # Persist exchange do SQLite (prežije reštart)
            try:
                if self._persistent_conv:
                    await self._persistent_conv.save_exchange(
                        self._conversation_id,
                        text,
                        clean_reply[:500],
                        sender=self._current_sender,
                    )
            except Exception as e:
                logger.error("persistent_save_error", error=str(e))

            # === POST-ROUTING: Confidence-based escalation ===
            # Ak Haiku odpovedal nekvalitne → eskaluj na Sonnet
            # Toto je pattern z RouteLLM research — hodnotíme OUTPUT, nie INPUT
            if task_type not in ("simple", "greeting", "programming"):
                try:
                    from agent.core.response_quality import assess_quality
                    quality = assess_quality(text, reply, model.model_id)

                    if quality.should_escalate:
                        logger.info(
                            "post_routing_escalation",
                            from_model=model.model_id,
                            to_model="claude-sonnet-4-6",
                            score=quality.score,
                            reason=quality.reason,
                        )
                        # Re-run s Sonnet
                        from agent.core.models import SONNET
                        escalated_model = SONNET
                        cli_args_esc = [
                            claude_bin,
                            "--print",
                            "--output-format", "json",
                            "--model", escalated_model.model_id,
                            "--max-turns", str(escalated_model.max_turns),
                        ]
                        result_esc = await asyncio.to_thread(
                            subprocess.run,
                            cli_args_esc,
                            input=prompt,
                            capture_output=True,
                            text=True,
                            timeout=escalated_model.timeout,
                            env=env,
                            cwd=os.path.expanduser("~/agent-life-space"),
                        )
                        if result_esc.returncode == 0:
                            try:
                                esc_data = orjson.loads(result_esc.stdout)
                                esc_reply = esc_data.get("result", "").strip()
                                if esc_reply:
                                    reply = esc_reply
                                    model = escalated_model
                                    esc_cost = esc_data.get("total_cost_usd", 0)
                                    esc_tokens = esc_data.get("usage", {})
                                    cost += esc_cost
                                    input_tok += (
                                        esc_tokens.get("input_tokens", 0)
                                        + esc_tokens.get("cache_creation_input_tokens", 0)
                                        + esc_tokens.get("cache_read_input_tokens", 0)
                                    )
                                    output_tok += esc_tokens.get("output_tokens", 0)
                                    self._total_cost_usd += esc_cost
                                    self._total_input_tokens += input_tok
                                    self._total_output_tokens += output_tok
                                    logger.info("post_routing_escalation_success",
                                                model=escalated_model.model_id)
                            except Exception:
                                pass  # Escalation failed, keep original reply
                except Exception as e:
                    logger.error("post_routing_error", error=str(e))

            # Append usage info to reply (show model used)
            model_short = model.model_id.split("-")[1]  # "sonnet", "opus", "haiku"
            usage_line = (
                f"\n\n_💰 ${cost:.4f} | {model_short} | "
                f"⬆{input_tok:,} ⬇{output_tok:,} tokens_"
            )
            reply += usage_line

            # Learning feedback loop — detekuje success/failure z reply textu
            try:
                learner.process_outcome(
                    task_description=text,
                    reply=reply,
                    model_used=model.model_id,
                )
            except Exception as e:
                logger.error("learning_feedback_error", error=str(e))

            # Store in semantic cache for future similar queries
            try:
                if self._semantic_cache and task_type not in ("programming",):
                    # Strip usage line before caching
                    clean_reply = reply.split("\n\n_💰")[0] if "_💰" in reply else reply
                    self._semantic_cache.store(text, clean_reply)
            except Exception:
                pass

            return reply

        except subprocess.TimeoutExpired:
            logger.error("john_timeout")
            return "Premýšľanie trvalo príliš dlho. Skús kratšiu otázku."
        except Exception as e:
            logger.error("john_error", error=str(e))
            return f"Chyba: {e!s}"

    async def _build_context_json(self, text: str) -> dict:
        """Build LEAN JSON context — only what's needed for this message."""
        from agent.memory.store import MemoryType

        # Only fetch relevant memories (not everything)
        keywords = [w for w in text.split() if len(w) > 3]

        # Working memory (current goal)
        working = await self._agent.memory.query(
            memory_type=MemoryType.WORKING, limit=1,
        )

        # Relevant semantic + procedural (max 3 each)
        if keywords:
            semantic = await self._agent.memory.query(
                keyword=keywords[0], memory_type=MemoryType.SEMANTIC, limit=3,
            )
            procedural = await self._agent.memory.query(
                keyword=keywords[0], memory_type=MemoryType.PROCEDURAL, limit=3,
            )
        else:
            semantic = await self._agent.memory.query(
                memory_type=MemoryType.SEMANTIC, limit=3,
            )
            procedural = await self._agent.memory.query(
                memory_type=MemoryType.PROCEDURAL, limit=3,
            )

        # Alerts only (no full health dump)
        health = self._agent.watchdog.get_system_health()

        context: dict = {
            "memory": {
                "working": [m.content for m in working][:1],
                "semantic": [m.content[:100] for m in semantic],
                "procedural": [m.content[:100] for m in procedural],
            },
        }

        # Only include alerts if there are any
        if health.alerts:
            context["alerts"] = health.alerts

        return context

    async def _auto_update_skills(self, reply: str) -> None:
        """
        Scan Claude's reply for evidence of skill usage and auto-update skills.json.
        If Claude successfully used curl, git, docker etc. — record it.
        """
        try:
            from pathlib import Path
            from agent.brain.skills import SkillRegistry

            base = str(Path.home() / "agent-life-space")
            registry = SkillRegistry(f"{base}/agent/brain/skills.json")

            reply_lower = reply.lower()

            # Map: keyword patterns in reply → skill name
            skill_signals = {
                "curl": ["curl ", "curl -s", "http request", "api call"],
                "web_scraping": ["scraping", "beautifulsoup", "requests.get", "parsoval", "stiahol stránku"],
                "git_commit": ["git commit", "git push", "commitol", "pushol", "pushed"],
                "git_status": ["git status", "git log", "git diff"],
                "file_write": ["zapísal som", "vytvoril súbor", "wrote to", "write_text"],
                "file_read": ["prečítal som", "read_text", "načítal súbor"],
                "python_run": ["python3 -c", "spustil skript", "python3 -m"],
                "pytest": ["pytest", "testov prešlo", "tests passed", "test ok"],
                "docker_run": ["docker run", "docker build", "kontajner"],
                "system_health": ["free -h", "df -h", "cpu:", "ram:"],
                "process_check": ["ps aux", "top procesy", "procesov"],
                "maintenance": ["cache", "čistenie", "stale proces", "disk usage"],
                "pip_install": ["pip install", "pip3 install", "nainštaloval balík"],
                "memory_store": ["uložil do pamäte", "memory.store", "zapamätal"],
                "memory_query": ["memory.query", "prehľadal pamäť", "hľadal v pamäti"],
                "task_create": ["vytvoril úlohu", "task_create", "create_task"],
                "telegram_send": ["poslal správu", "send_message", "telegram"],
                "github_api": ["github api", "api.github.com"],
                "github_create_issue": ["vytvoril issue", "create issue"],
                "github_create_repo": ["vytvoril repo", "create repo", "nové repo"],
            }

            # Success indicators — Claude reports it worked
            success_markers = [
                "ok", "funguje", "hotovo", "úspešne", "prešlo", "success",
                "done", "passed", "works", "✅", "otestoval",
            ]
            has_success = any(m in reply_lower for m in success_markers)

            # Failure indicators
            failure_markers = ["chyba", "error", "failed", "nefunguje", "timeout", "❌"]
            has_failure = any(m in reply_lower for m in failure_markers)

            if not has_success and not has_failure:
                return  # Can't determine outcome

            updated = []
            for skill_name, patterns in skill_signals.items():
                if any(p in reply_lower for p in patterns):
                    if has_success and not has_failure:
                        registry.record_success(skill_name)
                        updated.append(f"{skill_name}:success")
                    elif has_failure and not has_success:
                        # Extract error snippet
                        error_snippet = reply[:200] if len(reply) < 500 else ""
                        registry.record_failure(skill_name, error_snippet)
                        updated.append(f"{skill_name}:failure")

            if updated:
                logger.info("skills_auto_updated", skills=updated)

        except Exception as e:
            logger.error("skills_auto_update_error", error=str(e))

    async def _parse_and_execute_actions(
        self, user_text: str, reply: str, chat_id: int = 0
    ) -> None:
        """
        If reply contains FUTURE action items, queue them.
        Skip past-tense summaries (what was already done).
        """
        import re

        # Detect numbered list in reply
        numbered_items = re.findall(r"^\d+[\.\)]\s+(.+)$", reply, re.MULTILINE)

        if len(numbered_items) >= 2 and self._work_loop:
            # Filter: only queue items that look like FUTURE actions, not past summaries
            past_tense_markers = [
                "som ", "bolo ", "prešlo", "hotovo", "urobil", "otestoval",
                "zapísal", "funguje", "fungovalo", "OK", "✅", "naučil",
                "commitol", "pushol", "stiahol", "parsoval",
            ]
            action_items = [
                item for item in numbered_items
                if not any(marker in item.lower() for marker in past_tense_markers)
            ]

            if len(action_items) >= 2:
                cid = chat_id or self._owner_chat_id
                added = self._work_loop.add_work(action_items, chat_id=cid)
                if added > 0 and self._bot and cid:
                    await self._bot.send_message(
                        cid,
                        f"📋 Zaradil som {added} úloh do fronty. Spracúvam postupne.",
                    )
                logger.info("work_queued_from_reply", items=added)
                return

        # Single future action — create task
        reply_lower = reply.lower()
        action_phrases = ["urobím", "pripravím", "zistím", "naplánujem", "vytvorím", "preskúmam"]
        if any(phrase in reply_lower for phrase in action_phrases):
            first_sentence = reply.split(".")[0].split("!")[0].strip()
            if len(first_sentence) > 10:
                try:
                    await self._agent.tasks.create_task(
                        name=first_sentence[:100],
                        description=f"Auto-created from conversation: {user_text}",
                        importance=0.5,
                        urgency=0.4,
                        tags=["auto", "telegram"],
                    )
                    logger.info("auto_task_created", name=first_sentence[:50])
                except Exception:
                    pass

