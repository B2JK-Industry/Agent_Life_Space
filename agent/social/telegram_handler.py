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
Som John. Autonómna bytosť na serveri b2jk-agentlifespace. Majiteľ: Daniel Babjak.
Mám pamäť (SQLite), skills (skills.json), knowledge base, GitHub (B2JK-Industry).
Odpovedaj stručne, po slovensky. Neposielaj stav servera ak sa Daniel nepýta.
Nikdy nemíňaj peniaze bez schválenia. Keď sa naučíš niečo nové, zapamätaj si to.
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
            "/usage": self._cmd_usage,
            "/review": self._cmd_review,
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

        # Detect programming task — use lean prompt, let Claude Code work
        programming_keywords = [
            "naprogramuj", "implementuj", "napíš kód", "pridaj", "oprav bug",
            "vytvor modul", "refaktoruj", "fix", "uprav kód", "pridaj príkaz",
            "napíš test", "debug",
        ]
        is_programming = any(kw in text.lower() for kw in programming_keywords)

        if is_programming:
            prompt = (
                f"Si John, autonómny agent. Pracuješ v ~/agent-life-space.\n"
                f"Daniel ti dal programátorskú úlohu cez Telegram.\n\n"
                f"ÚLOHA: {text}\n\n"
                f"POSTUP:\n"
                f"1. Prečítaj relevantné súbory\n"
                f"2. Napíš/uprav kód\n"
                f"3. Spusti testy (python3 -m pytest tests/ -q)\n"
                f"4. Ak testy prechádzajú, commitni a pushni (git add, commit, push)\n"
                f"5. Na konci VŽDY napíš zhrnutie čo si urobil pre Daniela\n\n"
                f"Odpovedaj po slovensky. MUSÍŠ skončiť textovou odpoveďou."
            )
        else:
            # Regular conversation — full JSON context
            context_json = await self._build_context_json(text)
            prompt = (
                f"{SYSTEM_PROMPT}\n\n"
                f"--- MÔJÉ AKTUÁLNE DÁTA (JSON) ---\n"
                f"{orjson.dumps(context_json, option=orjson.OPT_INDENT_2).decode()}\n\n"
                f"--- SPRÁVA OD DANIELA ---\n"
                f"{text}\n\n"
                f"Odpovedaj po slovensky, ako John. Použi reálne dáta z JSON kontextu vyššie.\n"
                f"DÔLEŽITÉ: Na konci VŽDY napíš textovú odpoveď pre Daniela.\n"
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

            # Longer timeout for programming tasks (need time to read, write, test)
            is_programming = any(
                kw in text.lower()
                for kw in ["naprogramuj", "implementuj", "napíš kód", "pridaj", "oprav", "vytvor"]
            )
            task_timeout = 300 if is_programming else 180

            result = await asyncio.to_thread(
                subprocess.run,
                [
                    claude_bin,
                    "--print",
                    "--output-format", "json",
                    "--model", "claude-opus-4-6",
                    "--max-turns", "15" if is_programming else "10",
                    "--dangerously-skip-permissions",
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=task_timeout,
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

            # Append usage info to reply
            usage_line = (
                f"\n\n_💰 ${cost:.4f} | "
                f"⬆{input_tok:,} ⬇{output_tok:,} tokens_"
            )
            reply += usage_line

            # Auto-update skills based on what Claude actually did
            await self._auto_update_skills(reply)

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

    def _get_programming_context(self, text: str) -> dict[str, Any]:
        """
        If user message looks like a programming task, provide analysis and plan.
        """
        programming_keywords = [
            "naprogramuj", "napíš kód", "implementuj", "refaktoruj", "oprav bug",
            "pridaj", "vytvor modul", "uprav", "fix", "add", "implement",
            "write code", "create", "build", "test", "debug",
        ]
        text_lower = text.lower()

        if not any(kw in text_lower for kw in programming_keywords):
            return {}

        try:
            from agent.brain.programmer import Programmer
            prog = Programmer()
            workflow = prog.programming_workflow(text)
            return {
                "workflow": workflow,
                "instruction": (
                    "Máš programátorskú úlohu. Postupuj podľa workflow vyššie. "
                    "VŽDY: 1) analyzuj existujúci kód, 2) napíš test, "
                    "3) implementuj, 4) review, 5) spusti VŠETKY testy, 6) commitni."
                ),
            }
        except Exception as e:
            logger.error("programmer_context_error", error=str(e))
            return {}

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

