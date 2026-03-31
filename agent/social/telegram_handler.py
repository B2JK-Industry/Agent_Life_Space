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
from dataclasses import dataclass
from datetime import UTC
from typing import Any

import structlog

from agent.core.agent import AgentOrchestrator
from agent.core.identity import get_agent_identity, get_response_language_instruction
from agent.core.paths import get_project_root
from agent.core.persona import get_agent_prompt, get_simple_prompt, get_system_prompt

logger = structlog.get_logger(__name__)


@dataclass
class RequestContext:
    """Per-request context. Prevents race conditions between concurrent messages."""

    sender: str = ""
    chat_type: str = "private"
    chat_id: int = 0
    user_id: int = 0
    is_owner: bool = False
    force_safe_mode: bool = False


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
        brain: Any = None,
    ) -> None:
        self._agent = agent
        self._bot = bot
        self._work_loop = work_loop
        self._owner_chat_id = owner_chat_id
        self._brain = brain  # AgentBrain instance (channel-agnostic processing)
        # Usage tracking
        self._total_cost_usd: float = 0.0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_requests: int = 0
        # Semantic cache and RAG (lazy init)
        self._semantic_cache = None
        self._rag_index = None
        # Per-chat conversation buffers (key = chat_id)
        self._conversations: dict[int, list[dict[str, str]]] = {}
        self._max_conversation = 10
        # Persistent conversation — SQLite (prežije reštart)
        self._persistent_conv: Any = None
        # DEPRECATED: kept for backward compat in tests
        self._conversation: list[dict[str, str]] = []
        self._conversation_id = ""

    def _get_chat_conversation(self, chat_id: int) -> list[dict[str, str]]:
        """Get per-chat conversation buffer. Creates if not exists."""
        if chat_id not in self._conversations:
            self._conversations[chat_id] = []
        return self._conversations[chat_id]

    def _get_conversation_id(self, chat_id: int) -> str:
        """Per-chat session ID for persistent conversation."""
        from datetime import datetime
        return f"chat-{chat_id}-{datetime.now(UTC).strftime('%Y-%m-%d')}"

    async def handle(
        self, text: str, user_id: int, chat_id: int,
        username: str = "", chat_type: str = "private",
        **kwargs: Any,
    ) -> str:
        text = text.strip()
        if not text:
            return "Prázdna správa."

        # Build per-request context (no shared instance state — prevents race conditions)
        owner_name = get_agent_identity().owner_name
        explicit_is_owner = kwargs.get("is_owner")
        if username and username != "unknown":
            sender = username
        elif explicit_is_owner:
            sender = owner_name
        else:
            sender = "unknown" if chat_type != "private" else "user"

        is_owner = (
            bool(explicit_is_owner)
            if explicit_is_owner is not None
            else sender == owner_name
        )
        is_group = chat_type in ("group", "supergroup")

        ctx = RequestContext(
            sender=sender,
            chat_type=chat_type,
            chat_id=chat_id,
            user_id=user_id,
            is_owner=is_owner,
            force_safe_mode=is_group and not is_owner,
        )

        # DEPRECATED (v2.0): kept ONLY for backward compat with legacy _handle_text path.
        # Production path uses AgentBrain which has NO shared state.
        # These are write-only from handle() perspective — reads happen only in legacy fallback.
        self._current_sender = ctx.sender
        self._current_chat_type = ctx.chat_type
        self._force_safe_mode = ctx.force_safe_mode

        # Input sanitizácia — prompt injection ochrana
        sanitized = self._sanitize_input(text)
        if sanitized is None:
            return "Tento vstup bol zablokovaný bezpečnostným filtrom."
        text = sanitized

        if text.startswith("/"):
            # SECURITY: Non-owner v skupine nemá prístup k privilegovaným príkazom
            if ctx.force_safe_mode:
                _SAFE_COMMANDS = frozenset(["/start", "/help", "/status", "/health"])
                cmd = text.split()[0].lower().split("@")[0]
                if cmd not in _SAFE_COMMANDS:
                    logger.warning("command_blocked_non_owner", command=cmd, sender=ctx.sender)
                    return f"Príkaz {cmd} je dostupný len pre ownera."
            response = await self._handle_command(text)
            self._mirror_to_terminal(ctx.chat_type, ctx.sender, text, response)
            return response

        # Keep sending typing indicator while the agent thinks
        typing_task = None
        if self._bot:
            async def keep_typing():
                while True:
                    await self._bot._api_call("sendChatAction", chat_id=chat_id, action="typing")
                    await asyncio.sleep(4)  # Telegram typing expires after 5s

            typing_task = asyncio.create_task(keep_typing())

        try:
            # Delegate to AgentBrain if available (v3.0 path)
            if self._brain is not None:
                from agent.social.channel import IncomingMessage
                incoming = IncomingMessage(
                    text=text,
                    sender_id=str(user_id),
                    sender_name=ctx.sender,
                    channel_type=ctx.chat_type,
                    chat_id=str(chat_id),
                    is_owner=ctx.is_owner,
                    is_group=ctx.chat_type in ("group", "supergroup"),
                )
                response = await self._brain.process(incoming)
                self._mirror_to_terminal(ctx.chat_type, ctx.sender, text, response)
                return response

            # Fallback to legacy _handle_text (backward compat)
            response = await self._handle_text(text, ctx)
            self._mirror_to_terminal(ctx.chat_type, ctx.sender, text, response)
            return response
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
            "/intake": self._cmd_intake,
            "/report": self._cmd_report,
            "/build": self._cmd_build,
            "/jobs": self._cmd_jobs,
            "/deliver": self._cmd_deliver,
            "/telemetry": self._cmd_telemetry,
            "/workflow": self._cmd_workflow,
            "/pipeline": self._cmd_pipeline,
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
            "/intake — spusti review alebo build cez unified intake\n"
            "/build — shortcut pre build intake\n"
            "/jobs — zoznam product jobov (review, build)\n"
            "/deliver — delivery status, filter, retry a odoslanie\n"
            "/telemetry — runtime telemetry dashboard\n"
            "/workflow — recurring workflow management\n"
            "/pipeline — multi-job pipeline orchestration\n"
            "/report — operator report, inbox, margin\n"
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
            f"Celkom spracované: {status.get('total_attempted', status.get('total_success', 0))}"
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
        """Code review cez ReviewService (job-centric, artifact-first)."""
        args_stripped = args.strip()
        if not args_stripped:
            return (
                "Použi: /review [cesta alebo repo]\n"
                "Napr: /review agent/core/router.py\n"
                "      /review .   (celý repo)\n"
                "      /review agent/review/  (adresár)"
            )

        from agent.review.models import ReviewIntake, ReviewJobType

        # Determine review type and path
        repo_path = args_stripped
        review_type = ReviewJobType.REPO_AUDIT

        # If path is a single file, use the parent dir and focus on it
        from pathlib import Path as _Path
        target = _Path(get_project_root()) / repo_path if not _Path(repo_path).is_absolute() else _Path(repo_path)
        include_patterns: list[str] = []
        if target.is_file():
            include_patterns = [target.name]
            repo_path = str(target.parent)
        elif target.is_dir():
            repo_path = str(target)
        else:
            return f"Cesta `{args_stripped}` neexistuje."

        intake = ReviewIntake(
            repo_path=repo_path,
            review_type=review_type,
            requester=self._current_sender or "telegram",
            include_patterns=include_patterns,
            source="telegram",
        )

        # Prefer the shared runtime entrypoint, but keep a service fallback for
        # lightweight adapters and legacy test doubles that do not expose it.
        from inspect import isawaitable

        run_review_job = getattr(self._agent, "run_review_job", None)
        if callable(run_review_job):
            maybe_job = run_review_job(intake)
            if isawaitable(maybe_job):
                job = await maybe_job
            elif hasattr(maybe_job, "error"):
                job = maybe_job
            else:
                job = await self._agent.review.run_review(intake)
        else:
            job = await self._agent.review.run_review(intake)

        # Format for Telegram (channel adapter — just display, no business logic)
        if job.error:
            return f"*Review FAILED*\n{job.error}"

        report = job.report
        counts = report.finding_counts
        lines = [
            f"*Review Report* — {report.verdict}",
            f"`{args_stripped}` ({report.files_analyzed} súborov, {report.total_lines} riadkov)\n",
        ]

        if sum(counts.values()) == 0:
            lines.append("Žiadne problémy nájdené. ✓")
        else:
            lines.append(f"Nálezy: {counts['critical']}C {counts['high']}H {counts['medium']}M {counts['low']}L\n")
            for f in report.findings[:10]:  # Cap at 10 for Telegram
                severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(f.severity.value, "")
                loc = f" `{f.location}`" if f.location else ""
                lines.append(f"{severity_icon} *{f.title}*{loc}")
                if f.recommendation:
                    lines.append(f"  → {f.recommendation}")
            if len(report.findings) > 10:
                lines.append(f"\n... a ďalších {len(report.findings) - 10} nálezov.")

        lines.append(f"\n_Job {job.id} | {len(job.artifacts)} artifacts_")
        return "\n".join(lines)

    async def _cmd_wallet(self, args: str) -> str:
        """Show wallet addresses and balances. NEVER show private keys."""
        try:

            from agent.core.paths import get_project_root
            from agent.vault.secrets import SecretsManager
            _root = get_project_root()
            vault_dir = os.path.join(_root, "agent", "vault")
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
        lines.append("\n*Agent API:* port 8420 (aktívny)")

        # Conversation buffer
        lines.append(f"*Conversation buffer:* {sum(len(v) for v in self._conversations.values())} správ")

        # Memory
        mem_stats = self._agent.memory.get_stats()
        lines.append(f"*Spomienky:* {mem_stats['total_memories']}")

        return "\n".join(lines)

    @staticmethod
    def _mirror_to_terminal(
        channel_type: str, sender: str, text: str, response: str,
    ) -> None:
        """Show messages from other channels in the terminal REPL (if active)."""
        if channel_type == "terminal":
            return  # Don't echo terminal messages back
        try:
            from agent.social.terminal_repl import get_active_repl

            repl = get_active_repl()
            if repl is not None:
                repl.show_remote_message(channel_type, sender, text, response)
        except Exception:
            pass

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

    async def _handle_text(self, text: str, ctx: RequestContext | None = None) -> str:
        """
        JSON in → Claude thinks → JSON out → format for Telegram.
        Multi-task detection happens BEFORE Claude — goes straight to queue.
        """
        import re

        from agent.memory.store import MemoryEntry, MemoryType

        # Resolve context (backward compat for tests calling without ctx)
        if ctx is None:
            ctx = RequestContext(
                sender=getattr(self, "_current_sender", "unknown"),
                chat_type=getattr(self, "_current_chat_type", "private"),
                chat_id=self._owner_chat_id,
            )

        # Per-chat conversation buffer
        chat_conv = self._get_chat_conversation(ctx.chat_id)
        conv_id = self._get_conversation_id(ctx.chat_id)

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
                tags=[
                    "telegram",
                    "user_input",
                    "owner" if ctx.is_owner else "participant",
                ],
                source="telegram",
                importance=0.6,
            )
        )

        # === STEP 1: Try internal dispatch FIRST (no LLM) ===
        # Skip dispatcher ak máme conversation context a správa je krátka
        # (pravdepodobne nadväzuje na predchádzajúcu tému)
        short_followup = len(chat_conv) > 0 and len(text.split()) <= 8
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

            persistent_context = await self._persistent_conv.build_context(
                conv_id, query=text,
            )
        except Exception as e:
            logger.error("persistent_conv_error", error=str(e))

        # === STEP 3.7: Auto-inject runtime stav pri otázkach o sebe ===
        # Zabraňuje konfabulácii — agent dostane fakty, nie generuje čísla
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
                from agent.core.models import OPUS, SONNET
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
            from agent.brain.tool_router import (
                build_always_inject,
                detect_and_fetch,
                format_tool_context,
            )
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
        has_conversation = len(chat_conv) > 0
        if has_conversation:
            conv_lines = []
            identity = get_agent_identity()
            for msg in chat_conv[-self._max_conversation:]:
                role = (
                    msg.get("sender", identity.owner_name)
                    if msg["role"] == "user"
                    else identity.agent_name
                )
                conv_lines.append(f"{role}: {msg['content'][:200]}")
            conv_context = "\n".join(conv_lines)

            # Ak existuje konverzácia a správa je krátka/vágna,
            # eskaluj na chat (nie simple/factual) aby agent použil kontext
            if task_type in ("simple", "factual") and len(text.split()) <= 8:
                original_task_type = task_type
                task_type = "chat"
                from agent.core.models import get_model
                model = get_model(task_type)
                logger.info("conversation_context_escalation",
                            original_type=original_task_type, reason="short msg with conversation history")

        # Store user message in per-chat conversation buffer
        chat_conv.append({"role": "user", "content": text, "sender": ctx.sender})
        if len(chat_conv) > self._max_conversation:
            chat_conv.pop(0)

        # === STEP 5: Build prompt based on task type ===
        # Vyber system prompt podľa toho kto píše
        is_agent_chat = ctx.chat_type == "agent_api" or (
            ctx.sender not in (get_agent_identity().owner_name, "unknown", "", "user")
            and ctx.chat_type in ("group", "supergroup", "agent_api")
        )
        active_prompt = get_agent_prompt() if is_agent_chat else get_system_prompt()

        # SECURITY: Non-owner v skupine nemôže spúšťať programming tasky
        if ctx.force_safe_mode and task_type == "programming":
            logger.warning("programming_downgraded_to_chat", sender=ctx.sender)
            task_type = "chat"

        if task_type == "programming":
            prompt = (
                f"{active_prompt}\n"
                f"{tool_context}"
                f"Si programátor. Pracuješ v {get_project_root()}.\n\n"
                f"ÚLOHA: {text}\n\n"
            )
            if url_context:
                prompt += f"Obsah odkazov z úlohy:\n{url_context}\n\n"
            prompt += (
                "Prečítaj súbory, napíš/uprav kód, spusti testy, commitni.\n"
                f"Na konci VŽDY napíš zhrnutie. {get_response_language_instruction()}"
            )
        elif task_type in ("simple", "factual", "greeting") and tool_context.count("\n") <= 2:
            prompt = (
                f"{get_simple_prompt()}\n"
                f"{ctx.sender}: {text}\n"
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
                f"{ctx.sender}: {text}\n"
                "Použi aktuálne dáta vyššie ak sú relevantné. "
                f"{get_response_language_instruction()}"
            )

        # === STEP 5.5: Learning prompt augmentation ===
        try:
            # BEHAVIORAL CHANGE #2: Past errors pridané do promptu
            prompt = learner.augment_prompt(text, prompt)
        except Exception:
            pass  # learner nemusí byť dostupný

        try:
            from agent.core.llm_provider import GenerateRequest, get_provider

            # Resolve project root from config (no hardcoded paths)
            project_root = os.environ.get(
                "AGENT_PROJECT_ROOT",
                str(self._agent._data_dir.parent) if hasattr(self._agent, "_data_dir") else "",
            )

            provider = get_provider()
            response = await provider.generate(GenerateRequest(
                messages=[{"role": "user", "content": prompt}],
                model=model.model_id,
                max_tokens=model.max_turns * 4096,
                timeout=model.timeout,
                max_turns=model.max_turns,
                allow_file_access=task_type == "programming",
                cwd=project_root,
            ))

            if not response.success:
                logger.error("llm_error", error=response.error[:500])
                return f"Chyba: {response.error[:200]}"

            reply = response.text
            if not reply:
                # Retry with simpler prompt
                logger.warning("empty_result", original_prompt_len=len(prompt))
                retry_response = await provider.generate(GenerateRequest(
                    messages=[{
                        "role": "user",
                        "content": (
                            f"{active_prompt}\nOtázka: {text}\n"
                            f"Respond directly and briefly. {get_response_language_instruction()}"
                        ),
                    }],
                    model=model.model_id,
                    max_turns=1,
                    timeout=60,
                    cwd=project_root,
                ))
                reply = retry_response.text or "Prepáč, nepodarilo sa mi odpovedať. Skús otázku preformulovať."

            input_tok = response.input_tokens
            output_tok = response.output_tokens
            cost = response.cost_usd

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
                    source="agent",
                    importance=0.3,
                )
            )
            # Store clean reply in per-chat conversation buffer
            clean_reply = reply.split("\n\n_💰")[0] if "_💰" in reply else reply
            chat_conv.append({"role": "assistant", "content": clean_reply[:300]})
            if len(chat_conv) > self._max_conversation:
                chat_conv.pop(0)

            # Persist exchange do SQLite (prežije reštart)
            try:
                if self._persistent_conv:
                    await self._persistent_conv.save_exchange(
                        conv_id,
                        text,
                        clean_reply[:500],
                        sender=ctx.sender,
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
                        # Re-run s Sonnet via provider
                        from agent.core.models import SONNET
                        escalated_model = SONNET
                        esc_response = await provider.generate(GenerateRequest(
                            messages=[{"role": "user", "content": prompt}],
                            model=escalated_model.model_id,
                            max_turns=escalated_model.max_turns,
                            timeout=escalated_model.timeout,
                            cwd=project_root,
                        ))
                        if esc_response.success and esc_response.text:
                            reply = esc_response.text
                            model = escalated_model
                            cost += esc_response.cost_usd
                            input_tok += esc_response.input_tokens
                            output_tok += esc_response.output_tokens
                            self._total_cost_usd += esc_response.cost_usd
                            self._total_input_tokens += esc_response.input_tokens
                            self._total_output_tokens += esc_response.output_tokens
                            logger.info("post_routing_escalation_success",
                                        model=escalated_model.model_id)
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

        except TimeoutError:
            logger.error("john_timeout")
            return "Premýšľanie trvalo príliš dlho. Skús kratšiu otázku."
        except Exception as e:
            logger.error("john_error", error=str(e))
            return f"Chyba: {e!s}"

    # --- Phase 3: Operator commands ---

    async def _cmd_report(self, args: str) -> str:
        """Operator report — stručný prehľad pre Telegram."""
        try:
            report = self._agent.reporting.get_report(limit=10)
        except Exception as e:
            return f"*Report error:* {e!s}"

        summary = report.get("summary", {})
        inbox = report.get("inbox", [])

        section = args.strip().lower()

        if section == "inbox":
            if not inbox:
                return "*Inbox:* Žiadne attention items. ✓"
            lines = ["*Operator Inbox:*"]
            for item in inbox[:15]:
                kind = item.get("kind", "?")
                title = item.get("title", item.get("detail", "?"))
                lines.append(f"• `{kind}` — {title}")
            return "\n".join(lines)

        if section == "budget":
            bp = report.get("budget_posture", {})
            return (
                f"*Budget Posture:*\n"
                f"Daily spent: ${bp.get('daily_spent_usd', 0):.2f} "
                f"/ ${bp.get('daily_hard_cap', 50):.0f}\n"
                f"Monthly spent: ${bp.get('monthly_spent_usd', 0):.2f} "
                f"/ ${bp.get('monthly_hard_cap', 500):.0f}\n"
                f"Pending proposals: {bp.get('pending_proposals', 0)}"
            )

        if section == "cost":
            ca = report.get("cost_accuracy", {})
            if not ca or ca.get("sample_size", 0) == 0:
                return "*Cost Accuracy:* Žiadne dáta. Spusti joby cez `/intake` aby sa zbierali odhady vs skutočné náklady."
            lines = [
                "*Cost Accuracy:*",
                f"Sample: {ca['sample_size']} jobov",
                f"Celkový odhad: ${ca['total_estimated_usd']:.2f}",
                f"Skutočné náklady: ${ca['total_actual_usd']:.2f}",
                f"Priemerný pomer: {ca['avg_ratio']:.2f}x",
                f"Presnosť: {ca['accuracy_pct']:.0f}%",
            ]
            for comp in ca.get("comparisons", [])[:5]:
                lines.append(
                    f"• `{comp['job_id'][:12]}` est=${comp['estimated_usd']:.2f} "
                    f"act=${comp['actual_usd']:.2f} ({comp['ratio']:.2f}x)"
                )
            return "\n".join(lines)

        if section == "delivery":
            pds = report.get("provider_delivery_summary", {})
            views = report.get("recent_provider_deliveries", [])
            if not views:
                return "*Provider Deliveries:* Žiadne provider deliveries."
            lines = [
                f"*Provider Delivery Summary* ({pds.get('total', 0)} total):",
            ]
            by_outcome = pds.get("by_outcome", {})
            if by_outcome:
                lines.append("*By outcome:*")
                for outcome, count in sorted(by_outcome.items()):
                    lines.append(f"  {outcome}: {count}")
            by_provider = pds.get("by_provider", {})
            if by_provider:
                lines.append("*By provider:*")
                for provider_id, count in sorted(by_provider.items()):
                    lines.append(f"  {provider_id}: {count}")
            attention_items = [v for v in views if v.get("attention_required")]
            if attention_items:
                lines.append(f"\n*Attention required ({len(attention_items)}):*")
                for item in attention_items[:5]:
                    job_id = item.get("job_id", "?")[:12]
                    outcome = item.get("outcome", "?")
                    lines.append(f"• `{job_id}` {item.get('title', '?')[:40]} — {outcome}")
            lines.append(
                "\n`/deliver pending` | `/deliver failed` | `/deliver delivered`"
            )
            return "\n".join(lines)

        if section == "telemetry":
            ts = report.get("telemetry_summary", {})
            if not ts or ts.get("snapshots", 0) == 0:
                latest = ts.get("latest")
                if latest:
                    return self._format_telemetry_snapshot(latest, note="Posledný snapshot (mimo okna)")
                return "*Telemetry:* Žiadne dáta. Snapshots sa zbierajú pri dokončení jobov."
            return self._format_telemetry_summary(ts)

        if section == "margin":
            try:
                ms = self._agent.control_plane.get_margin_summary(limit=50)
            except Exception as e:
                return f"*Margin error:* {e!s}"
            if ms.get("total_jobs", 0) == 0:
                return "*Margin:* Žiadne joby. Revenue sa zaznamenáva cez `/report margin set <job_id> <usd>`."
            lines = [
                "*Margin Summary:*",
                f"Jobs: {ms['total_jobs']} ({ms.get('jobs_with_revenue', 0)} with revenue)",
                f"Revenue: ${ms['total_revenue_usd']:.4f}",
                f"Cost: ${ms['total_cost_usd']:.4f}",
                f"Margin: ${ms['total_margin_usd']:.4f} ({ms['avg_margin_pct']:.1f}%)",
                f"Profitable: {ms.get('profitable_jobs', 0)}/{ms['total_jobs']}",
            ]
            return "\n".join(lines)

        if section.startswith("margin set"):
            # /report margin set <job_id> <usd> [source]
            parts = section.split()
            if len(parts) < 4:
                return "*Použitie:* `/report margin set <job_id> <usd> [source]`"
            job_id = parts[2]
            try:
                revenue = float(parts[3])
            except ValueError:
                return f"*Error:* '{parts[3]}' nie je platná suma."
            source = parts[4] if len(parts) > 4 else "manual"
            try:
                job = self._agent.control_plane.record_job_revenue(
                    job_id=job_id, revenue_usd=revenue, source=source,
                )
            except Exception as e:
                return f"*Error:* {e!s}"
            if job is None:
                return f"Job `{job_id}` nenájdený."
            return (
                f"*Revenue recorded:* ${revenue:.4f} pre job `{job_id}`\n"
                f"Margin: ${job.margin_usd:.4f}"
            )

        # Default: stručný overview
        lines = [
            "*Operator Report:*",
            f"Jobs: {summary.get('total_jobs', 0)} "
            f"({summary.get('completed_jobs', 0)} done, "
            f"{summary.get('blocked_jobs', 0)} blocked, "
            f"{summary.get('failed_jobs', 0)} failed)",
            f"Artifacts: {summary.get('total_artifacts', 0)}",
            f"Approvals pending: {summary.get('pending_approvals', 0)}",
            f"Deliveries: {summary.get('delivery_records', summary.get('total_deliveries', 0))}",
            f"Cost ledger: ${summary.get('recorded_cost_usd', summary.get('total_recorded_cost_usd', 0)):.4f}",
            f"Inbox items: {len(inbox)}",
        ]
        if inbox:
            lines.append("\n*Top attention:*")
            for item in inbox[:5]:
                kind = item.get("kind", "?")
                title = item.get("title", item.get("detail", "?"))
                lines.append(f"• `{kind}` — {title}")
        else:
            lines.append("\nŽiadne attention items. ✓")
        lines.append("\n`/report inbox` | `/report budget` | `/report cost` | `/report delivery` | `/report telemetry` | `/report margin`")
        return "\n".join(lines)

    async def _cmd_intake(self, args: str) -> str:
        """Unified operator intake — qualify, plan, and execute review or build jobs."""
        if not args.strip():
            return (
                "*Použitie:*\n"
                "`/intake [cesta] --description \"popis\"`\n"
                "`/intake . --description \"security audit\"`\n"
                "`/intake agent/build/ --type build --description \"add tests\"`\n"
                "`/intake --git URL --description \"audit repo\"`\n\n"
                "Parametre:\n"
                "• `--type review|build` (default: auto)\n"
                "• `--description \"...\"` (povinné)\n"
                "• `--git URL` (pre vzdialené repo)"
            )

        from agent.control.intake import OperatorIntake

        # Simple argument parsing (no argparse — it calls sys.exit on error)
        tokens = args.strip().split()
        repo_path = ""
        git_url = ""
        work_type = "auto"
        description = ""

        i = 0
        positional_done = False
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--type" and i + 1 < len(tokens):
                work_type = tokens[i + 1]
                i += 2
                positional_done = True
            elif tok == "--git" and i + 1 < len(tokens):
                git_url = tokens[i + 1]
                i += 2
                positional_done = True
            elif tok == "--description":
                # Collect everything after --description as the description
                description = " ".join(tokens[i + 1:])
                break
            elif not positional_done and not tok.startswith("--"):
                repo_path = tok
                i += 1
                positional_done = True
            else:
                i += 1

        if not description:
            return "Chýba `--description`. Použi: `/intake . --description \"čo chceš\"`"

        # Resolve repo_path relative to project root
        if repo_path and not repo_path.startswith("/") and not git_url:
            from pathlib import Path as _Path
            resolved = _Path(get_project_root()) / repo_path
            if resolved.exists():
                repo_path = str(resolved)
            else:
                return f"Cesta `{repo_path}` neexistuje."

        intake = OperatorIntake(
            repo_path=repo_path,
            git_url=git_url,
            work_type=work_type,
            description=description,
            requester=self._current_sender or "telegram",
        )

        try:
            result = await asyncio.wait_for(
                self._agent.submit_operator_intake(intake),
                timeout=90.0,
            )
        except TimeoutError:
            return "Intake trvá príliš dlho (>90s). Skús menší scope."
        except Exception as e:
            logger.error("intake_error", error=str(e))
            return f"*Intake error:* {e!s}"

        status = result.get("status", "unknown")
        qualification = result.get("qualification", {})
        plan = result.get("plan", {})

        lines = [f"*Intake — {status}*"]

        if status == "blocked":
            error = result.get("error", "Unknown block reason")
            lines.append(f"Blokované: {error}")
            return "\n".join(lines)

        if status == "awaiting_approval":
            lines.append("Čaká na schválenie.")
            req = result.get("approval_request", {})
            if req:
                lines.append(f"Approval ID: `{req.get('approval_request_id', '?')}`")
            return "\n".join(lines)

        # Completed or submitted
        job_kind = result.get("job_kind", "?")
        job_id = result.get("job_id", "?")
        work = qualification.get("resolved_work_type", "?")
        risk = qualification.get("risk_level", "?")
        cost = plan.get("budget", {}).get("estimated_cost_usd", 0)

        lines.extend([
            f"Typ: {work} | Risk: {risk}",
            f"Job: `{job_id}` ({job_kind})",
            f"Estimated cost: ${cost:.2f}",
        ])

        # Show job result if available
        job_data = result.get("job", {})
        if job_data:
            job_status = job_data.get("status", "?")
            lines.append(f"Job status: {job_status}")
            metadata = job_data.get("metadata", {})
            if metadata.get("verdict"):
                lines.append(f"Verdict: {metadata['verdict']}")
            if metadata.get("finding_counts"):
                fc = metadata["finding_counts"]
                counts = " ".join(f"{v}{k[0].upper()}" for k, v in fc.items() if v > 0)
                if counts:
                    lines.append(f"Findings: {counts}")

        return "\n".join(lines)

    async def _cmd_build(self, args: str) -> str:
        """Shortcut pre /intake --type build."""
        if not args.strip():
            return (
                "*Použitie:*\n"
                "`/build [cesta] --description \"čo chceš postaviť\"`\n"
                "`/build agent/review/ --description \"add verification tests\"`\n\n"
                "Skratka pre `/intake [cesta] --type build --description ...`"
            )
        # Inject --type build BEFORE --description so it gets parsed
        if "--type" not in args:
            if "--description" in args:
                args = args.replace("--description", "--type build --description", 1)
            else:
                args = f"{args} --type build"
        return await self._cmd_intake(args)

    async def _cmd_jobs(self, args: str) -> str:
        """List recent product jobs or show detail for a specific job."""
        args_stripped = args.strip()

        if args_stripped:
            # Detail pre konkrétny job
            job = self._agent.get_product_job(args_stripped)
            if job is None:
                return f"Job `{args_stripped}` nenájdený."
            job_id = job.get("job_id") or job.get("id", "?")
            lines = [
                f"*Job {job_id}*",
                f"Kind: {job.get('job_kind', job.get('kind', '?'))} | Status: {job.get('status', '?')}",
                f"Created: {job.get('created_at', '?')[:19]}",
            ]
            metadata = job.get("metadata", {})
            if metadata.get("verdict"):
                lines.append(f"Verdict: {metadata['verdict']}")
            if metadata.get("finding_counts"):
                fc = metadata["finding_counts"]
                counts = " ".join(f"{v}{k[0].upper()}" for k, v in fc.items() if v > 0)
                if counts:
                    lines.append(f"Findings: {counts}")
            if metadata.get("acceptance_met") is not None:
                lines.append(f"Acceptance: {metadata['acceptance_met']}/{metadata.get('acceptance_total', '?')}")
            if job.get("error"):
                lines.append(f"Error: {job['error'][:200]}")
            return "\n".join(lines)

        # List recent jobs
        try:
            jobs = self._agent.list_product_jobs(limit=10)
        except Exception as e:
            return f"*Jobs error:* {e!s}"

        if not jobs:
            return "Žiadne joby. Použi `/intake` na spustenie review alebo build."

        lines = ["*Recent Jobs:*"]
        for job in jobs:
            status = job.get("status", "?")
            kind = job.get("job_kind", job.get("kind", "?"))
            jid = (job.get("job_id") or job.get("id", "?"))[:12]
            title = job.get("title", "")[:40]
            created = job.get("created_at", "")[:10]
            label = title or kind
            lines.append(f"• `{jid}` {label} — {status} ({created})")
        lines.append("\n`/jobs <id>` pre detail")
        return "\n".join(lines)

    async def _cmd_workflow(self, args: str) -> str:
        """Recurring workflow management."""
        if not hasattr(self._agent, "recurring_workflows"):
            from agent.control.recurring import RecurringWorkflowManager

            self._agent.recurring_workflows = RecurringWorkflowManager(
                control_plane_state=getattr(self._agent, "control_plane", None),
            )
        mgr = self._agent.recurring_workflows
        parts = args.strip().split(maxsplit=1)
        action = parts[0] if parts else ""

        if action == "create":
            # /workflow create <name> --schedule daily --type review --repo .
            tokens = (parts[1] if len(parts) > 1 else "").split()
            name = ""
            schedule = "daily"
            work_type = "review"
            repo_path = "."
            description = ""
            i = 0
            while i < len(tokens):
                if tokens[i] == "--schedule" and i + 1 < len(tokens):
                    schedule = tokens[i + 1]
                    i += 2
                elif tokens[i] == "--type" and i + 1 < len(tokens):
                    work_type = tokens[i + 1]
                    i += 2
                elif tokens[i] == "--repo" and i + 1 < len(tokens):
                    repo_path = tokens[i + 1]
                    i += 2
                elif tokens[i] == "--description":
                    description = " ".join(tokens[i + 1:])
                    break
                elif not name:
                    name = tokens[i]
                    i += 1
                else:
                    i += 1
            if not name:
                return (
                    "*Použitie:*\n"
                    "`/workflow create <name> --schedule daily|weekly|monthly "
                    "--type review|build --repo . --description \"...\"`"
                )
            workflow = mgr.create(
                name=name,
                job_kind=work_type,
                schedule=schedule,
                intake_template={
                    "repo_path": repo_path,
                    "work_type": work_type,
                    "description": description or f"Recurring {work_type}: {name}",
                },
            )
            return (
                f"*Workflow created:* `{workflow.workflow_id}`\n"
                f"Name: {workflow.name}\n"
                f"Schedule: {schedule}\n"
                f"Next run: {workflow.next_run_at[:19]}"
            )

        if action == "pause" and len(parts) > 1:
            wid = parts[1].strip()
            if mgr.pause(wid):
                return f"Workflow `{wid}` paused."
            return f"Workflow `{wid}` nie je aktívny alebo neexistuje."

        if action == "activate" and len(parts) > 1:
            wid = parts[1].strip()
            if mgr.activate(wid):
                return f"Workflow `{wid}` activated."
            return f"Workflow `{wid}` nie je pauznutý alebo neexistuje."

        # Default: list workflows
        workflows = mgr.list_workflows()
        if not workflows:
            return (
                "*Recurring Workflows:* žiadne.\n"
                "`/workflow create <name> --schedule daily --type review --repo .`"
            )
        lines = [f"*Recurring Workflows* ({len(workflows)}):"]
        for w in workflows[:10]:
            status_mark = {"active": "▶", "paused": "⏸", "failed": "✗"}.get(w.status, "?")
            lines.append(
                f"{status_mark} `{w.workflow_id}` {w.name} — {w.schedule} "
                f"(runs: {w.run_count})"
            )
        lines.append("\n`/workflow create` | `/workflow pause <id>` | `/workflow activate <id>`")
        return "\n".join(lines)

    async def _cmd_pipeline(self, args: str) -> str:
        """Multi-job pipeline management."""
        if not hasattr(self._agent, "pipeline_orchestrator"):
            from agent.control.pipeline import PipelineOrchestrator

            self._agent.pipeline_orchestrator = PipelineOrchestrator(agent=self._agent)
        orch = self._agent.pipeline_orchestrator
        parts = args.strip().split(maxsplit=1)
        action = parts[0] if parts else ""

        if action == "create":
            # /pipeline create <name> review:. build:.
            rest = parts[1] if len(parts) > 1 else ""
            tokens = rest.split()
            if len(tokens) < 2:
                return (
                    "*Použitie:*\n"
                    "`/pipeline create <name> review:<repo> build:<repo>`\n"
                    "Príklad: `/pipeline create audit-and-fix review:. build:.`"
                )
            name = tokens[0]
            stages = []
            for token in tokens[1:]:
                if ":" in token:
                    kind, repo = token.split(":", 1)
                    stages.append({
                        "name": f"{kind} stage",
                        "job_kind": kind,
                        "intake_template": {
                            "repo_path": repo or ".",
                            "work_type": kind,
                            "description": f"Pipeline {name}: {kind} stage",
                        },
                        "condition": "on_success",
                    })
            if not stages:
                return "*Error:* Žiadne stages. Formát: `review:. build:.`"
            pipeline = orch.create_pipeline(name=name, stages=stages)
            lines = [
                f"*Pipeline created:* `{pipeline.pipeline_id}`",
                f"Name: {name}",
                f"Stages: {len(pipeline.stages)}",
            ]
            for s in pipeline.stages:
                lines.append(f"  • {s.name} ({s.job_kind.value}) [{s.condition}]")
            lines.append(f"\n`/pipeline run {pipeline.pipeline_id}` na spustenie")
            return "\n".join(lines)

        if action == "run" and len(parts) > 1:
            pid = parts[1].strip()
            try:
                result = await orch.execute_pipeline(pid)
            except Exception as e:
                return f"*Pipeline error:* {e!s}"
            if result.get("ok"):
                return (
                    f"*Pipeline completed:* `{pid}`\n"
                    f"Stages: {result['stages_executed']}/{result['stages_total']}"
                )
            return (
                f"*Pipeline failed:* `{pid}`\n"
                f"Stages: {result.get('stages_executed', 0)}/{result.get('stages_total', 0)}\n"
                f"Error: {result.get('error', 'unknown')[:200]}"
            )

        # Default: list pipelines
        pipelines = orch.list_pipelines()
        if not pipelines:
            return (
                "*Pipelines:* žiadne.\n"
                "`/pipeline create <name> review:<repo> build:<repo>`"
            )
        lines = [f"*Pipelines* ({len(pipelines)}):"]
        for p in pipelines[:10]:
            stage_summary = "/".join(s.job_kind.value[0].upper() for s in p.stages)
            lines.append(f"• `{p.pipeline_id}` {p.name} [{stage_summary}] — {p.status}")
        lines.append("\n`/pipeline create` | `/pipeline run <id>`")
        return "\n".join(lines)

    async def _cmd_telemetry(self, args: str) -> str:
        """Runtime telemetry dashboard — throughput, latency, cost, delivery health."""
        try:
            window_hours = 24
            if args.strip().isdigit():
                window_hours = max(1, min(168, int(args.strip())))  # 1h-7d

            ts = self._agent.control_plane.get_telemetry_summary(
                window_hours=window_hours,
            )
        except Exception as e:
            return f"*Telemetry error:* {e!s}"

        if not ts or ts.get("snapshots", 0) == 0:
            latest = ts.get("latest") if ts else None
            if latest:
                return self._format_telemetry_snapshot(latest, note="Posledný snapshot")
            return (
                "*Telemetry:* Žiadne dáta.\n"
                "Snapshots sa zbierajú pri dokončení jobov.\n"
                "Použi `/telemetry` po spustení jobov."
            )

        return self._format_telemetry_summary(ts)

    def _format_telemetry_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        note: str = "",
    ) -> str:
        """Format a single telemetry snapshot for Telegram."""
        lines = ["*Runtime Telemetry*"]
        if note:
            lines.append(f"_{note}_")
        lines.extend([
            "",
            f"*Jobs:* {snapshot.get('jobs_completed', 0)} completed, "
            f"{snapshot.get('jobs_failed', 0)} failed, "
            f"{snapshot.get('jobs_retried', 0)} retried",
            f"*Active:* {snapshot.get('jobs_active', 0)} "
            f"| Queue: {snapshot.get('queue_depth', 0)}",
        ])
        avg_dur = snapshot.get("avg_duration_ms", 0)
        p95_dur = snapshot.get("p95_duration_ms", 0)
        if avg_dur > 0:
            lines.append(f"*Latency:* avg={avg_dur:.0f}ms p95={p95_dur:.0f}ms")
        total_cost = snapshot.get("total_cost_usd", 0)
        avg_cost = snapshot.get("avg_cost_per_job_usd", 0)
        lines.append(f"*Cost:* ${total_cost:.4f} total, ${avg_cost:.4f}/job")
        dt = snapshot.get("deliveries_total", 0)
        if dt > 0:
            lines.append(
                f"*Deliveries:* {dt} total "
                f"({snapshot.get('deliveries_delivered', 0)} delivered, "
                f"{snapshot.get('deliveries_pending', 0)} pending, "
                f"{snapshot.get('deliveries_failed', 0)} failed)"
            )
        mem = snapshot.get("memory_percent", 0)
        cpu = snapshot.get("cpu_percent", 0)
        if mem > 0 or cpu > 0:
            lines.append(f"*System:* CPU {cpu:.1f}% | RAM {mem:.1f}%")
        if snapshot.get("circuit_breaker_open"):
            lines.append("⚠️ *Circuit breaker is OPEN*")
        return "\n".join(lines)

    def _format_telemetry_summary(self, ts: dict[str, Any]) -> str:
        """Format aggregated telemetry summary for Telegram."""
        latest = ts.get("latest", {})
        agg = ts.get("aggregated", {})
        trend = ts.get("trend", "stable")
        window = ts.get("window_hours", 24)
        snapshot_count = ts.get("snapshots", 0)

        trend_label = {"stable": "→ stable", "improving": "↑ improving", "degrading": "↓ degrading"}

        lines = [
            f"*Runtime Telemetry* ({window}h window, {snapshot_count} snapshots)",
            f"Trend: {trend_label.get(trend, trend)}",
        ]

        if latest:
            lines.append("")
            lines.append("*Latest:*")
            lines.append(
                f"  Jobs: {latest.get('jobs_completed', 0)} done, "
                f"{latest.get('jobs_failed', 0)} failed"
            )
            avg_dur = latest.get("avg_duration_ms", 0)
            p95_dur = latest.get("p95_duration_ms", 0)
            if avg_dur > 0:
                lines.append(f"  Latency: avg={avg_dur:.0f}ms p95={p95_dur:.0f}ms")
            lines.append(f"  Cost: ${latest.get('total_cost_usd', 0):.4f}")

        if agg:
            lines.append("")
            lines.append(f"*Aggregated ({window}h):*")
            lines.append(
                f"  Max jobs completed: {agg.get('max_jobs_completed', 0)}"
            )
            lines.append(
                f"  Max jobs failed: {agg.get('max_jobs_failed', 0)}"
            )
            avg_d = agg.get("avg_duration_ms", 0)
            if avg_d > 0:
                lines.append(f"  Avg duration: {avg_d:.0f}ms")
            lines.append(f"  Max cost: ${agg.get('max_cost_usd', 0):.4f}")
            fail_snapshots = agg.get("total_snapshots_with_failures", 0)
            cb_triggered = agg.get("circuit_breaker_triggered", 0)
            if fail_snapshots > 0:
                lines.append(f"  Snapshots with failures: {fail_snapshots}")
            if cb_triggered > 0:
                lines.append(f"  Circuit breaker triggered: {cb_triggered}x")

        lines.append("\n`/telemetry [hours]` na zmenu okna (default 24)")
        return "\n".join(lines)

    async def _cmd_deliver(self, args: str) -> str:
        """Delivery status, listing, filtering, retry, and gateway send."""
        parts = args.strip().split()

        # Filter by provider outcome: /deliver pending|failed|delivered
        if parts and parts[0] in ("pending", "failed", "delivered", "accepted", "unknown"):
            return await self._deliver_filter(parts[0])

        if not parts:
            # List recent deliveries
            try:
                deliveries = self._agent.list_delivery_records(limit=10)
            except Exception as e:
                return f"*Delivery error:* {e!s}"

            if not deliveries:
                return "Žiadne delivery záznamy. Deliveries sa vytvárajú po dokončení jobov."

            lines = ["*Recent Deliveries:*"]
            for d in deliveries:
                status = d.get("status", "?")
                job_id = d.get("job_id", "?")[:12]
                kind = d.get("job_kind", "?")
                title = d.get("title", "")[:50] or f"{kind} delivery"
                provider_info = self._delivery_provider_badge(d)
                lines.append(f"• `{job_id}` {title} — {status}{provider_info}")
            lines.append(
                "\n`/deliver <job_id>` detail"
                " | `/deliver pending|failed|delivered` filter"
            )
            return "\n".join(lines)

        job_id = parts[0]
        action = parts[1] if len(parts) > 1 else ""

        if action in ("send", "retry"):
            # Trigger gateway delivery (retry = same as send, re-sends)
            try:
                result = None
                review_bundle = self._agent.get_review_delivery_bundle(job_id)
                if review_bundle:
                    result = await self._agent.send_review_delivery_via_gateway(job_id=job_id)
                else:
                    build_bundle = self._agent.get_build_delivery_bundle(job_id)
                    if build_bundle:
                        result = await self._agent.send_build_delivery_via_gateway(job_id=job_id)

                if result is None:
                    return f"Job `{job_id}` nemá delivery bundle. Skontroluj `/jobs {job_id}`."

                ok = result.get("ok", False)
                if ok:
                    provider_id = result.get("provider_id", "?")
                    outcome = result.get("provider_outcome", "")
                    label = "Retry sent" if action == "retry" else "Delivery sent"
                    msg = f"*{label}* pre job `{job_id}` ✓\nProvider: {provider_id}"
                    if outcome:
                        msg += f"\nOutcome: {outcome}"
                    return msg
                error = result.get("error", "Unknown delivery error")
                return f"*Delivery failed* pre job `{job_id}`\n{error}"
            except Exception as e:
                return f"*Delivery send error:* {e!s}"

        # Show delivery detail for job_id (enriched with provider data)
        try:
            deliveries = self._agent.list_delivery_records(job_id=job_id, limit=5)
        except Exception as e:
            return f"*Delivery error:* {e!s}"

        if not deliveries:
            return f"Žiadne delivery záznamy pre job `{job_id}`."

        d = deliveries[0]  # Most recent
        lines = [
            f"*Delivery — {d.get('status', '?')}*",
            f"Job: `{d.get('job_id', '?')}` ({d.get('job_kind', '?')})",
            f"Title: {d.get('title', '?')}",
        ]
        if d.get("approval_request_id"):
            lines.append(f"Approval: `{d['approval_request_id']}`")

        # Provider delivery details (outcome, receipt, attention)
        provider = d.get("summary", {}).get("provider_delivery", {})
        if provider:
            lines.append("")
            outcome = provider.get("outcome", "unknown")
            attention = provider.get("attention_required", False)
            attention_mark = " ⚠️" if attention else ""
            lines.append(f"*Provider:* {provider.get('provider_id', '?')}{attention_mark}")
            lines.append(f"Outcome: {outcome}")
            if provider.get("provider_status"):
                lines.append(f"Provider status: {provider['provider_status']}")
            if provider.get("route_id"):
                lines.append(f"Route: {provider['route_id']}")
            if provider.get("capability_id"):
                lines.append(f"Capability: {provider['capability_id']}")
            receipt = provider.get("receipt", {})
            if receipt:
                receipt_status = receipt.get("status", "")
                receipt_ts = receipt.get("timestamp", "")
                if receipt_status:
                    lines.append(f"Receipt: {receipt_status}")
                if receipt_ts:
                    lines.append(f"Receipt time: {receipt_ts}")

        events = d.get("events", [])
        if events:
            lines.append(f"\n*Events ({len(events)}):*")
            for event in events[-5:]:
                event_type = event.get("event_type", "?")
                event_status = event.get("status", "?")
                event_detail = event.get("detail", "")
                line = f"• {event_type} — {event_status}"
                if event_detail:
                    line += f" ({event_detail[:60]})"
                lines.append(line)

        # Action hints based on state
        actions = []
        if provider and provider.get("outcome") in ("failed", "unknown"):
            actions.append(f"`/deliver {job_id} retry` na retry")
        if d.get("status") not in ("handed_off",):
            actions.append(f"`/deliver {job_id} send` na odoslanie")
        if actions:
            lines.append("\n" + " | ".join(actions))
        return "\n".join(lines)

    def _delivery_provider_badge(self, delivery: dict[str, Any]) -> str:
        """Short provider outcome badge for delivery listing."""
        provider = delivery.get("summary", {}).get("provider_delivery", {})
        if not provider:
            return ""
        outcome = provider.get("outcome", "")
        if not outcome:
            return ""
        attention = " ⚠️" if provider.get("attention_required") else ""
        return f" [{outcome}{attention}]"

    async def _deliver_filter(self, outcome_filter: str) -> str:
        """Filter deliveries by provider outcome."""
        try:
            deliveries = self._agent.list_delivery_records(limit=50)
        except Exception as e:
            return f"*Delivery error:* {e!s}"

        filtered = []
        for d in deliveries:
            provider = d.get("summary", {}).get("provider_delivery", {})
            outcome = provider.get("outcome", "unknown") if provider else ""
            if outcome == outcome_filter:
                filtered.append(d)

        if not filtered:
            return f"Žiadne deliveries s outcome `{outcome_filter}`."

        lines = [f"*Deliveries — {outcome_filter}* ({len(filtered)}):"]
        for d in filtered[:15]:
            job_id = d.get("job_id", "?")[:12]
            title = d.get("title", "")[:50] or d.get("job_kind", "delivery")
            provider = d.get("summary", {}).get("provider_delivery", {})
            provider_id = provider.get("provider_id", "")
            provider_part = f" via {provider_id}" if provider_id else ""
            lines.append(f"• `{job_id}` {title}{provider_part}")
        if len(filtered) > 15:
            lines.append(f"... a {len(filtered) - 15} ďalších")
        lines.append("\n`/deliver <job_id>` pre detail")
        return "\n".join(lines)

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

            from agent.brain.skills import SkillRegistry

            base = get_project_root()
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
