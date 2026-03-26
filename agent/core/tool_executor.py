"""
Agent Life Space — Tool Executor

Mapuje tool_use volania z LLM na reálne agent funkcie.
Každý tool handler je async, vracia dict (serializovateľný ako tool_result).

Bezpečnosť:
    - run_code VŽDY cez sandbox (nikdy host)
    - web_fetch rešpektuje rate limity
    - Žiadny tool pre "write file to host" alebo "run shell command"
    - Finance tools vyžadujú approval flag
"""

from __future__ import annotations

from typing import Any

import structlog

from agent.core.agent import AgentOrchestrator
from agent.core.sandbox_executor import SandboxExecutor
from agent.core.tool_policy import ToolExecutionContext, ToolPolicy

logger = structlog.get_logger(__name__)


class ToolExecutor:
    """
    Executes tool calls from LLM. Maps tool names to agent module methods.
    """

    def __init__(
        self,
        agent: AgentOrchestrator,
        sandbox: SandboxExecutor | None = None,
        policy: ToolPolicy | None = None,
    ) -> None:
        self._agent = agent
        self._sandbox = sandbox or SandboxExecutor()
        self._policy = policy or ToolPolicy()
        self._handlers: dict[str, Any] = {
            "store_memory": self._store_memory,
            "query_memory": self._query_memory,
            "create_task": self._create_task,
            "list_tasks": self._list_tasks,
            "run_code": self._run_code,
            "run_tests": self._run_tests,
            "web_fetch": self._web_fetch,
            "check_health": self._check_health,
            "get_status": self._get_status,
            "search_knowledge": self._search_knowledge,
        }
        self._call_count = 0
        self._error_count = 0
        self._blocked_count = 0

    async def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolExecutionContext | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a tool call. Returns result dict."""
        if isinstance(context, dict):
            context = ToolExecutionContext(**context)

        handler = self._handlers.get(tool_name)
        if not handler:
            self._error_count += 1
            return {"error": f"Unknown tool: {tool_name}. Available: {list(self._handlers.keys())}"}

        decision = self._policy.evaluate(tool_name, context)
        if not decision.allowed:
            self._blocked_count += 1
            return {
                "error": decision.reason,
                "blocked": True,
                "risk_level": decision.risk_level.value,
                "side_effect": decision.side_effect.value,
                "audit_label": decision.audit_label,
            }

        try:
            self._call_count += 1
            result = await handler(**tool_input)
            if isinstance(result, dict):
                result.setdefault("risk_level", decision.risk_level.value)
                result.setdefault("audit_label", decision.audit_label)
            logger.info("tool_executed", tool=tool_name, audit_label=decision.audit_label, success=True)
            return result
        except Exception as e:
            self._error_count += 1
            logger.error("tool_error", tool=tool_name, error=str(e))
            return {"error": f"Tool '{tool_name}' failed: {e!s}"}

    def get_stats(self) -> dict[str, int]:
        return {
            "total_calls": self._call_count,
            "errors": self._error_count,
            "blocked": self._blocked_count,
            "available_tools": len(self._handlers),
        }

    # ─────────────────────────────────────────────
    # Tool handlers
    # ─────────────────────────────────────────────

    async def _store_memory(
        self,
        content: str,
        memory_type: str = "episodic",
        tags: list[str] | None = None,
        importance: float = 0.5,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from agent.memory.store import MemoryEntry, MemoryType

        entry = MemoryEntry(
            content=content,
            memory_type=MemoryType(memory_type),
            tags=tags or [],
            source="tool_use",
            importance=max(0.0, min(1.0, importance)),
        )
        mem_id = await self._agent.memory.store(entry)
        return {"status": "stored", "memory_id": mem_id}

    async def _query_memory(
        self,
        keyword: str | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
        **kwargs: Any,
    ) -> dict[str, Any]:
        results = await self._agent.memory.query(
            keyword=keyword, tags=tags, limit=min(limit, 20),
        )
        return {
            "count": len(results),
            "results": [
                {
                    "content": r.content[:300],
                    "type": r.memory_type.value,
                    "tags": r.tags[:5],
                    "importance": r.importance,
                }
                for r in results
            ],
        }

    async def _create_task(
        self,
        name: str,
        description: str = "",
        priority: float = 0.5,
        **kwargs: Any,
    ) -> dict[str, Any]:
        task = await self._agent.tasks.create_task(
            name=name,
            description=description,
            priority=max(0.0, min(1.0, priority)),
        )
        return {"status": "created", "task_id": task.id, "name": task.name}

    async def _list_tasks(
        self,
        status: str = "all",
        limit: int = 10,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from agent.tasks.manager import TaskStatus

        if status == "all":
            stats = self._agent.tasks.get_stats()
            return stats

        task_status = TaskStatus(status)
        tasks = self._agent.tasks.get_tasks_by_status(task_status)
        return {
            "count": len(tasks),
            "tasks": [
                {"id": t.id, "name": t.name, "priority": t.priority, "status": t.status.value}
                for t in tasks[:limit]
            ],
        }

    async def _run_code(
        self,
        code: str,
        language: str = "python",
        packages: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run code in Docker sandbox. NEVER on host."""
        if language == "python" and packages:
            result = await self._sandbox.execute_python(code, packages=packages)
        else:
            result = await self._sandbox.execute_code(language, code)
        return result.to_dict()

    async def _run_tests(
        self,
        source_code: str,
        test_code: str,
        source_filename: str = "module.py",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run pytest in Docker sandbox."""
        result = await self._sandbox.run_tests(source_code, test_code, source_filename)
        return result.to_dict()

    async def _web_fetch(
        self,
        url: str,
        max_chars: int = 5000,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from agent.core.web import WebAccess

        web = WebAccess()
        try:
            result = await web.scrape_text(url, max_chars=min(max_chars, 10000))
            return result
        finally:
            await web.close()

    async def _check_health(self, **kwargs: Any) -> dict[str, Any]:
        health = self._agent.watchdog.get_system_health()
        return {
            "cpu_percent": health.cpu_percent,
            "memory_percent": health.memory_percent,
            "disk_percent": health.disk_percent,
            "modules": health.modules,
            "alerts": health.alerts,
        }

    async def _get_status(self, **kwargs: Any) -> dict[str, Any]:
        return self._agent.get_status()

    async def _search_knowledge(
        self,
        query: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            from agent.memory.rag import RAGIndex

            rag = RAGIndex()
            if not rag._built:
                rag.build_index()
            result = rag.retrieve_for_llm(query)
            return {
                "action": result.get("action", "none"),
                "context": result.get("context", "")[:2000],
                "source": result.get("source", ""),
                "confidence": result.get("confidence", 0),
            }
        except Exception as e:
            return {"error": f"Knowledge search failed: {e!s}"}
