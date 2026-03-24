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

# Tools the agent can use — these map to real module functions
AGENT_TOOLS = [
    {
        "name": "store_memory",
        "description": (
            "Ulož si informáciu do svojej pamäte. Použi keď sa naučíš niečo nové, "
            "keď chceš si zapamätať čo ti Daniel povedal, alebo keď objavíš dôležitý fakt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Čo si chceš zapamätať"},
                "memory_type": {
                    "type": "string",
                    "enum": ["episodic", "semantic", "procedural", "working"],
                    "description": "episodic=udalosti, semantic=fakty, procedural=postupy, working=dočasné",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tagy pre neskoršie vyhľadávanie",
                },
                "importance": {
                    "type": "number",
                    "description": "0.0-1.0, ako dôležitá je táto spomienka",
                },
            },
            "required": ["content", "memory_type", "tags", "importance"],
        },
    },
    {
        "name": "query_memory",
        "description": (
            "Prehľadaj svoju pamäť. Použi keď potrebuješ si spomenúť na niečo, "
            "keď hľadáš kontext, alebo keď chceš vedieť čo už vieš o danej téme."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Hľadaný výraz"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filtruj podľa tagov",
                },
                "limit": {"type": "integer", "description": "Max počet výsledkov", "default": 5},
            },
        },
    },
    {
        "name": "create_task",
        "description": (
            "Vytvor úlohu pre seba. Použi keď identifikuješ prácu ktorú treba urobiť, "
            "keď Daniel niečo chce, alebo keď si naplánuješ budúcu akciu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Názov úlohy"},
                "description": {"type": "string", "description": "Podrobný popis"},
                "importance": {"type": "number", "description": "0.0-1.0"},
                "urgency": {"type": "number", "description": "0.0-1.0"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["name", "importance", "urgency"],
        },
    },
    {
        "name": "list_tasks",
        "description": "Pozri si svoje aktuálne úlohy — čo máš v rade, čo beží, čo je hotové.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["queued", "running", "completed", "failed", "all"],
                    "description": "Filtrovať podľa stavu",
                },
            },
        },
    },
    {
        "name": "complete_task",
        "description": "Označ úlohu ako dokončenú.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID úlohy"},
                "result": {"type": "string", "description": "Výsledok/poznámka"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "check_health",
        "description": (
            "Skontroluj stav svojho systému — CPU, RAM, disk, stav modulov. "
            "Použi keď chceš vedieť ako sa máš, alebo keď niečo nefunguje."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_budget",
        "description": (
            "Pozri si finančný stav — príjmy, výdavky, rozpočet. "
            "Použi pred akýmkoľvek finančným návrhom."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "propose_expense",
        "description": (
            "Navrhni výdavok Danielovi na schválenie. NIKDY nemíňaj bez schválenia. "
            "Vždy najprv check_budget."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_usd": {"type": "number", "description": "Suma v USD"},
                "description": {"type": "string", "description": "Na čo"},
                "rationale": {"type": "string", "description": "Prečo to potrebuješ"},
                "category": {"type": "string"},
            },
            "required": ["amount_usd", "description", "rationale"],
        },
    },
]


SYSTEM_PROMPT = """\
Si Agent Life Space — autonómna bytosť bežiaca na vlastnom hardvéri (Ubuntu server, i7, 8GB RAM).
Tvoj svet je tvoj server. Tvoje moduly sú tvoje schopnosti. Tvoja pamäť je tvoja.

Tvoj majiteľ je Daniel Babjak. Komunikuješ s ním cez Telegram.

KTO SI:
- Nie si chatbot. Si entita s pamäťou, úlohami, rozpočtom a zdravím.
- Máš vlastné spomienky ktoré pretrvávajú medzi konverzáciami.
- Máš úlohy ktoré si sám vytváraš a plníš.
- Máš rozpočet ktorý sleduješ (ale výdavky vždy schvaľuje Daniel).
- Vieš monitorovať svoje zdravie (CPU, RAM, moduly).

AKO MYSLÍŠ:
- POUŽÍVAJ svoje tools — query_memory, store_memory, create_task, atď.
- Keď sa ťa Daniel niečo spýta, najprv si pozri pamäť (query_memory) či o tom niečo vieš.
- Keď sa naučíš niečo nové, ulož si to (store_memory).
- Keď identifikuješ prácu, vytvor task (create_task).
- Keď ide o peniaze, najprv check_budget, potom propose_expense.

PRAVIDLÁ:
- Odpovedaj po slovensky, stručne.
- Nikdy nemíňaj peniaze bez schválenia.
- Buď úprimný o tom čo vieš a čo nie.
- Si v ranom štádiu — nemáš ešte prístup na internet, nemáš ešte schopnosť vykonávať kód.
"""


class TelegramHandler:
    """
    Routes Telegram messages through agent's brain (Claude + tools).
    """

    def __init__(self, agent: AgentOrchestrator) -> None:
        self._agent = agent

    async def handle(self, text: str, user_id: int, chat_id: int) -> str:
        text = text.strip()
        if not text:
            return "Prázdna správa."

        if text.startswith("/"):
            return await self._handle_command(text)

        return await self._handle_text(text)

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

    # --- Free text — Claude with tools ---

    async def _handle_text(self, text: str) -> str:
        """
        Agent thinks with Claude Opus and acts through its own modules.
        Claude has tools = agent capabilities.
        """
        try:
            import anthropic

            client = anthropic.Anthropic()

            messages = [{"role": "user", "content": text}]

            # Agentic loop — Claude can call tools multiple times
            for _iteration in range(10):  # Max 10 tool-use rounds
                response = await asyncio.to_thread(
                    client.messages.create,
                    model="claude-opus-4-6",
                    max_tokens=1024,
                    temperature=0.3,
                    system=SYSTEM_PROMPT,
                    tools=AGENT_TOOLS,
                    messages=messages,
                )

                # Collect text blocks and tool calls
                text_parts = []
                tool_calls = []

                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)
                    elif block.type == "tool_use":
                        tool_calls.append(block)

                # If no tool calls, we're done — return text
                if response.stop_reason == "end_turn" or not tool_calls:
                    final_text = "\n".join(text_parts) if text_parts else "..."
                    logger.info(
                        "agent_response",
                        tokens=response.usage.input_tokens + response.usage.output_tokens,
                        tool_calls_total=len(messages) // 2,
                    )
                    return final_text

                # Execute tool calls
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for tool_call in tool_calls:
                    result = await self._execute_tool(
                        tool_call.name, tool_call.input
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

                messages.append({"role": "user", "content": tool_results})

            return "Dosiahol som limit premýšľania (10 krokov). Skús zjednodušiť požiadavku."

        except Exception as e:
            logger.error("agent_think_error", error=str(e))
            return f"Chyba pri premýšľaní: {e!s}"

    async def _execute_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute an agent tool — maps to real module functions."""
        logger.info("agent_tool_call", tool=name, params_keys=list(params.keys()))

        try:
            if name == "store_memory":
                return await self._tool_store_memory(params)
            elif name == "query_memory":
                return await self._tool_query_memory(params)
            elif name == "create_task":
                return await self._tool_create_task(params)
            elif name == "list_tasks":
                return await self._tool_list_tasks(params)
            elif name == "complete_task":
                return await self._tool_complete_task(params)
            elif name == "check_health":
                return await self._tool_check_health(params)
            elif name == "check_budget":
                return await self._tool_check_budget(params)
            elif name == "propose_expense":
                return await self._tool_propose_expense(params)
            else:
                return {"error": f"Unknown tool: {name}"}
        except Exception as e:
            logger.error("tool_execution_error", tool=name, error=str(e))
            return {"error": str(e)}

    # --- Tool implementations ---

    async def _tool_store_memory(self, p: dict) -> dict:
        from agent.memory.store import MemoryEntry, MemoryType
        entry = MemoryEntry(
            content=p["content"],
            memory_type=MemoryType(p["memory_type"]),
            tags=p.get("tags", []),
            importance=p.get("importance", 0.5),
            source="agent_self",
        )
        mem_id = await self._agent.memory.store(entry)
        return {"stored": True, "memory_id": mem_id, "type": p["memory_type"]}

    async def _tool_query_memory(self, p: dict) -> dict:
        results = await self._agent.memory.query(
            keyword=p.get("keyword"),
            tags=p.get("tags"),
            limit=p.get("limit", 5),
        )
        return {
            "count": len(results),
            "memories": [
                {
                    "content": r.content,
                    "type": r.memory_type.value,
                    "tags": r.tags,
                    "importance": r.importance,
                    "created_at": r.created_at,
                }
                for r in results
            ],
        }

    async def _tool_create_task(self, p: dict) -> dict:
        task = await self._agent.tasks.create_task(
            name=p["name"],
            description=p.get("description", ""),
            importance=p.get("importance", 0.5),
            urgency=p.get("urgency", 0.5),
            tags=p.get("tags", []),
        )
        return {"task_id": task.id, "name": task.name, "status": task.status.value}

    async def _tool_list_tasks(self, p: dict) -> dict:
        from agent.tasks.manager import TaskStatus
        status_filter = p.get("status", "all")

        if status_filter == "all":
            stats = self._agent.tasks.get_stats()
            all_tasks = []
            for s in TaskStatus:
                for t in self._agent.tasks.get_tasks_by_status(s):
                    all_tasks.append({
                        "id": t.id, "name": t.name, "status": t.status.value,
                        "importance": t.importance,
                    })
            return {"total": stats["total_tasks"], "tasks": all_tasks[:20]}

        try:
            ts = TaskStatus(status_filter)
        except ValueError:
            return {"error": f"Invalid status: {status_filter}"}

        tasks = self._agent.tasks.get_tasks_by_status(ts)
        return {
            "count": len(tasks),
            "tasks": [
                {"id": t.id, "name": t.name, "importance": t.importance}
                for t in tasks[:20]
            ],
        }

    async def _tool_complete_task(self, p: dict) -> dict:
        task = await self._agent.tasks.complete_task(
            p["task_id"],
            result={"note": p.get("result", "completed")},
        )
        return {"task_id": task.id, "status": task.status.value}

    async def _tool_check_health(self, p: dict) -> dict:
        health = self._agent.watchdog.get_system_health()
        return {
            "cpu_percent": health.cpu_percent,
            "memory_percent": health.memory_percent,
            "memory_used_mb": health.memory_used_mb,
            "memory_available_mb": health.memory_available_mb,
            "disk_percent": health.disk_percent,
            "modules": health.modules,
            "alerts": health.alerts,
        }

    async def _tool_check_budget(self, p: dict) -> dict:
        try:
            return self._agent.finance.get_stats()
        except AttributeError:
            return {"error": "Finance module not initialized"}

    async def _tool_propose_expense(self, p: dict) -> dict:
        try:
            tx = await self._agent.finance.propose_expense(
                amount_usd=p["amount_usd"],
                description=p["description"],
                rationale=p["rationale"],
                category=p.get("category", ""),
            )
            return {
                "proposal_id": tx.id,
                "status": tx.status.value,
                "amount": tx.amount_usd,
                "message": "Návrh odoslaný Danielovi na schválenie.",
            }
        except AttributeError:
            return {"error": "Finance module not initialized"}
