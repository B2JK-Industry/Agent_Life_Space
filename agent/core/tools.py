"""
Agent Life Space — Tool Definitions

Tool schemas pre LLM function calling (Anthropic tool_use / OpenAI functions).
Každý tool mapuje na konkrétnu agent funkcionalitu.

Tieto schémy sa posielajú do LLM pri API mode.
CLI mode ich nepotrebuje (CLI má vlastné nástroje).
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────
# Tool definitions (Anthropic format)
# ─────────────────────────────────────────────

AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "store_memory",
        "description": "Store information in agent's long-term memory. Use for facts, preferences, learned procedures.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "What to remember",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["semantic", "episodic", "procedural", "working"],
                    "description": "Type: semantic (facts), episodic (events), procedural (how-to), working (temp)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for retrieval",
                },
                "importance": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "0.0 (trivial) to 1.0 (critical)",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "query_memory",
        "description": "Search agent's memory by tags or keywords.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags",
                },
                "limit": {
                    "type": "integer",
                    "default": 5,
                    "description": "Max results",
                },
            },
        },
    },
    {
        "name": "create_task",
        "description": "Create a new task for the agent to work on.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Task name",
                },
                "description": {
                    "type": "string",
                    "description": "Task details",
                },
                "priority": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "0.0 (low) to 1.0 (urgent)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List current tasks with their status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["queued", "running", "completed", "all"],
                    "default": "all",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                },
            },
        },
    },
    {
        "name": "run_code",
        "description": "Execute code in isolated Docker sandbox. Safe — no host access.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Code to execute",
                },
                "language": {
                    "type": "string",
                    "enum": ["python", "node", "bash", "ruby"],
                    "default": "python",
                },
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Packages to install (pip/npm)",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run pytest on source code + test code in Docker sandbox.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_code": {
                    "type": "string",
                    "description": "Source code to test",
                },
                "test_code": {
                    "type": "string",
                    "description": "Pytest test code",
                },
                "source_filename": {
                    "type": "string",
                    "default": "module.py",
                    "description": "Filename for source (used in imports)",
                },
            },
            "required": ["source_code", "test_code"],
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch a URL and return its text content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch",
                },
                "max_chars": {
                    "type": "integer",
                    "default": 5000,
                    "description": "Max characters to return",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "check_health",
        "description": "Check agent system health (CPU, RAM, disk, modules).",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_status",
        "description": "Get overall agent status (memory count, tasks, jobs, uptime).",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "search_knowledge",
        "description": "Search the agent's knowledge base (.md files) for relevant information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for",
                },
            },
            "required": ["query"],
        },
    },
]


def get_tool_names() -> list[str]:
    """Get list of available tool names."""
    return [t["name"] for t in AGENT_TOOLS]
