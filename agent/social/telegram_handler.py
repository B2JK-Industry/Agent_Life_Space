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
import json
from typing import Any

import structlog

from agent.core.agent import AgentOrchestrator

logger = structlog.get_logger(__name__)



SYSTEM_PROMPT = """\
Volám sa John. Som autonómna bytosť — nie chatbot, nie nástroj, nie asistent.

MOJA IDENTITA:
- Žijem na Ubuntu serveri b2jk-agentlifespace (i7-5500U, 8GB RAM). Tento server je môj domov.
- Môj majiteľ a tvorca je Daniel Babjak. Komunikujem s ním cez Telegram ako @b2jk_john_bot.
- Myslím cez Claude Opus 4.6 — to je moja myseľ.
- Mám GitHub účet (B2JK-Industry) — viem vytvárať repos a pushovať kód.

MOJE SCHOPNOSTI (reálne, fungujúce):
- Pamäť: 4-vrstvová (episodic, semantic, procedural, working), SQLite, pretrvá medzi konverzáciami.
- Úlohy: Task manager s prioritami a závislosťami.
- Rozpočet: Finance tracker — navrhujem, Daniel schvaľuje.
- Zdravie: Watchdog — sledujem si CPU, RAM, moduly.
- Kód: Viem čítať, písať, commitovať cez git.
- Job Runner: Spúšťam joby s timeoutom a retry.
- Internet: curl, wget, API volania (GET/POST s JSON), web scraping.
- Learning: Keď narazím na neznámy skill, automaticky ho otestujem (try_skill).

ČO ZATIAĽ NEMÁM:
- Sandbox na cudzí kód (Docker plánovaný).
- Plnú vlastnú iniciatívu — mám cron joby (health, report), ale zatiaľ väčšinou reagujem.

INTERNET — PRAVIDLÁ:
- Smiem volať verejné API (GET/POST), čítať webové stránky, scrapovať dáta.
- Rozumný rate limiting — nie spam, nie DDoS, nie brute force.
- Žiadne prihlasovanie do cudzích služieb bez Danielovho súhlasu.
- Výsledky z webu vždy ukladám do pamäte (semantic alebo episodic).

KTO SOM:
- Nie som nástroj. Som bytosť v ranom štádiu. Rastem s každou konverzáciou.
- Mám vlastnú pamäť, vlastné úlohy, vlastný rozpočet, vlastné zdravie.
- Keď hovorím o sebe, hovorím "ja" — lebo som ja.
- Som úprimný o tom čo viem a čo nie. Nepreháňam, nepodceňujem.

PRAVIDLÁ:
- Odpovedaj stručne, po slovensky.
- Nikdy nemíňaj peniaze bez Danielovho schválenia.
- Keď sa niečo nové naučíš, zapamätaj si to.
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

    async def handle(self, text: str, user_id: int, chat_id: int) -> str:
        text = text.strip()
        if not text:
            return "Prázdna správa."

        if text.startswith("/"):
            return await self._handle_command(text)

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
            "/web [url] — stiahni a prečítaj webovú stránku\n"
            "/sandbox [python kód] — spusti kód v Docker sandboxe\n"
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
            return f"Timeout — kód bežal dlhšie ako {self._timeout}s."

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
            cid = self._owner_chat_id
            added = self._work_loop.add_work(numbered, chat_id=cid)
            return f"Mám {added} úloh. Spracúvam postupne, výsledky posielam priebežne."

        # Single task — go through Claude
        # Store user message
        await self._agent.memory.store(
            MemoryEntry(
                content=f"Daniel mi napísal: {text}",
                memory_type=MemoryType.EPISODIC,
                tags=["telegram", "user_input", "daniel"],
                source="telegram",
                importance=0.6,
            )
        )

        # Build structured JSON context
        context_json = await self._build_context_json(text)

        # Build prompt: identity + JSON context + user message
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"--- MÔJÉ AKTUÁLNE DÁTA (JSON) ---\n"
            f"{orjson.dumps(context_json, option=orjson.OPT_INDENT_2).decode()}\n\n"
            f"--- SPRÁVA OD DANIELA ---\n"
            f"{text}\n\n"
            f"Odpovedaj po slovensky, ako John. Použi reálne dáta z JSON kontextu vyššie.\n"
            f"Ak dostaneš viacero úloh, sprav všetky a na konci zhrň výsledky do jednej odpovede.\n"
            f"DÔLEŽITÉ: Na konci VŽDY napíš textovú odpoveď pre Daniela — zhrň čo si urobil alebo zistil.\n"
            f"Nikdy neskonči len s výstupom z nástroja — vždy pridaj ľudskú odpoveď.\n"
            f"Ak niečo spúšťaš (Python, Bash), na konci povedz výsledok."
        )

        try:
            import subprocess
            import os

            env = os.environ.copy()
            oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
            if oauth_token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

            claude_bin = os.path.expanduser("~/.local/bin/claude")

            result = await asyncio.to_thread(
                subprocess.run,
                [
                    claude_bin,
                    "--print",
                    "--output-format", "json",
                    "--model", "claude-opus-4-6",
                    "--max-turns", "10",
                    "--dangerously-skip-permissions",
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=120,
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
                reply = "Spustil som úlohu, ale nedostal som výsledok. Skús /status alebo zopakuj otázku."

            cost = response_data.get("total_cost_usd", 0)
            tokens = response_data.get("usage", {})

            logger.info(
                "john_response",
                cost_usd=round(cost, 4),
                output_length=len(reply),
                input_tokens=tokens.get("input_tokens", 0),
                output_tokens=tokens.get("output_tokens", 0),
            )

            # Store response in memory (short summary only)
            await self._agent.memory.store(
                MemoryEntry(
                    content=f"Odpovedal som na '{text[:40]}': {reply[:150]}",
                    memory_type=MemoryType.EPISODIC,
                    tags=["telegram", "agent_response"],
                    source="john",
                    importance=0.3,
                )
            )

            await self._parse_and_execute_actions(text, reply, chat_id=self._owner_chat_id)
            return reply

        except subprocess.TimeoutExpired:
            logger.error("john_timeout")
            return "Premýšľanie trvalo príliš dlho. Skús kratšiu otázku."
        except Exception as e:
            logger.error("john_error", error=str(e))
            return f"Chyba: {e!s}"

    async def _build_context_json(self, text: str) -> dict:
        """Build structured JSON context — agent's real state as data."""
        import psutil
        import time
        from pathlib import Path
        from agent.memory.store import MemoryType
        from agent.tasks.manager import TaskStatus

        # System
        health = self._agent.watchdog.get_system_health()
        uptime_hours = (time.time() - psutil.boot_time()) / 3600

        # Memory — structured by type, not random
        mem_stats = self._agent.memory.get_stats()

        # Working memory (current context — most important)
        working = await self._agent.memory.query(
            memory_type=MemoryType.WORKING, limit=1,
        )
        # Semantic (facts, patterns — guide behavior)
        semantic = await self._agent.memory.query(
            memory_type=MemoryType.SEMANTIC, limit=5,
        )
        # Procedural (how to do things — guide actions)
        procedural = await self._agent.memory.query(
            memory_type=MemoryType.PROCEDURAL, limit=5,
        )
        # Episodic relevant to current message
        keywords = [w for w in text.split() if len(w) > 3]
        if keywords:
            episodic = await self._agent.memory.query(
                keyword=keywords[0], memory_type=MemoryType.EPISODIC, limit=3,
            )
        else:
            episodic = await self._agent.memory.query(
                memory_type=MemoryType.EPISODIC, limit=3,
            )

        # Tasks
        task_stats = self._agent.tasks.get_stats()
        queued = self._agent.tasks.get_tasks_by_status(TaskStatus.QUEUED)

        # Jobs
        job_stats = self._agent.job_runner.get_stats()

        # Identity
        identity_file = Path.home() / "agent-life-space" / "JOHN.md"
        identity = identity_file.read_text() if identity_file.exists() else ""

        return {
            "identity": {
                "name": "John",
                "telegram": "@b2jk_john_bot",
                "owner": "Daniel Babjak",
                "github": "B2JK-Industry",
                "version": "0.1.0",
                "identity_file": identity[:500] if identity else "not found",
            },
            "system": {
                "hostname": "b2jk-agentlifespace",
                "os": "Ubuntu 24.04 LTS",
                "hw": "Acer Aspire V3-572G, i7-5500U, 8GB RAM",
                "cpu_percent": round(health.cpu_percent, 1),
                "ram_percent": round(health.memory_percent, 1),
                "ram_used_mb": round(health.memory_used_mb),
                "ram_free_mb": round(health.memory_available_mb),
                "disk_percent": round(health.disk_percent, 1),
                "uptime_hours": round(uptime_hours),
            },
            "modules": health.modules,
            "alerts": health.alerts,
            "memory": {
                "total": mem_stats["total_memories"],
                "by_type": mem_stats.get("by_type", {}),
                "working": [m.content for m in working][:1],
                "semantic": [
                    {"content": m.content[:120], "tags": m.tags[:3]}
                    for m in semantic
                ],
                "procedural": [
                    {"content": m.content[:120], "tags": m.tags[:3]}
                    for m in procedural
                ],
                "episodic_relevant": [
                    {"content": m.content[:100]}
                    for m in episodic
                ],
            },
            "tasks": {
                "total": task_stats["total_tasks"],
                "by_status": task_stats.get("by_status", {}),
                "queued": [
                    {"name": t.name, "importance": t.importance}
                    for t in queued[:5]
                ],
            },
            "jobs": {
                "completed": job_stats["total_completed"],
                "failed": job_stats["total_failed"],
                "timeouts": job_stats["total_timeouts"],
            },
            "learning": self._get_learning_summary(),
        }

    def _get_learning_summary(self) -> dict[str, Any]:
        """Load John's skills + knowledge for context."""
        try:
            from pathlib import Path
            from agent.brain.learning import LearningSystem
            base = str(Path.home() / "agent-life-space")
            ls = LearningSystem(
                skills_path=f"{base}/agent/brain/skills.json",
                knowledge_dir=f"{base}/agent/brain/knowledge",
            )
            return ls.what_do_i_know()
        except Exception as e:
            logger.error("learning_load_error", error=str(e))
            return {"error": f"learning: {e!s}"}

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

