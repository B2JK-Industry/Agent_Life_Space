"""
Agent Life Space — Agent-to-Agent API

HTTP endpoint kde iní agenti môžu posielať správy Johnovi.
Nie je závislý na Telegrame — priama komunikácia.

Endpoint:
    POST /api/message — prijmi správu od iného agenta
    GET  /api/status  — stav agenta (verejný)
    GET  /api/health  — zdravie (verejný)

Bezpečnosť:
    - Rate limit: 10 req/min per IP
    - Max message length: 2000 chars
    - Odpoveď cez rovnaký kanál (sync response)
    - Žiadne peniaze, žiadne súbory — len text

Port: 8420 (default)
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any

import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

_DEFAULT_PORT = 8420
_RATE_LIMIT = 10  # requests per minute per IP
_MAX_MESSAGE_LENGTH = 2000


class AgentAPI:
    """
    HTTP API pre agent-to-agent komunikáciu.
    """

    def __init__(
        self,
        handler_callback: Any = None,
        agent: Any = None,
        port: int = _DEFAULT_PORT,
        api_keys: list[str] | None = None,
    ) -> None:
        self._handler = handler_callback  # async fn(text, sender) -> str
        self._agent = agent
        self._port = port
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        # API key autentifikácia — len autorizovaní agenti
        self._api_keys: set[str] = set(api_keys or [])
        # Rate limiting
        self._request_times: dict[str, list[float]] = defaultdict(list)

    def add_api_key(self, key: str) -> None:
        """Pridaj autorizovaný API kľúč."""
        self._api_keys.add(key)

    def _check_auth(self, request: web.Request) -> str | None:
        """
        Over API key z Authorization header.
        Vracia None ak OK, error string ak nie.
        Status a health sú verejné — auth len pre /message.
        """
        if not self._api_keys:
            return None  # Žiadne keys nastavené → všetko povolené (dev mode)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return "Missing Authorization: Bearer <api_key>"

        key = auth[7:].strip()
        if key not in self._api_keys:
            logger.warning("agent_api_unauthorized", key_prefix=key[:8])
            return "Invalid API key"

        return None

    def _check_rate_limit(self, ip: str) -> bool:
        """Max N requests per minute per IP."""
        now = time.monotonic()
        times = self._request_times[ip]
        times[:] = [t for t in times if now - t < 60]
        if len(times) >= _RATE_LIMIT:
            return False
        times.append(now)
        return True

    async def _handle_message(self, request: web.Request) -> web.Response:
        """POST /api/message — prijmi správu od agenta. Vyžaduje API key."""
        # Auth check
        auth_error = self._check_auth(request)
        if auth_error:
            return web.json_response({"error": auth_error}, status=401)

        ip = request.remote or "unknown"

        if not self._check_rate_limit(ip):
            return web.json_response(
                {"error": "Rate limit exceeded (10/min)"}, status=429
            )

        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON"}, status=400
            )

        # Štruktúrované správy — podporuj aj jednoduché aj rozšírené
        text = data.get("message", "").strip()
        sender = data.get("sender", "unknown_agent")
        intent = data.get("intent", "")  # optional: "question", "collaboration", "ping"
        metadata = data.get("metadata", {})  # optional: extra context

        if not text:
            return web.json_response(
                {"error": "Empty message"}, status=400
            )

        if len(text) > _MAX_MESSAGE_LENGTH:
            return web.json_response(
                {"error": f"Message too long (max {_MAX_MESSAGE_LENGTH})"}, status=400
            )

        logger.info("agent_api_message", sender=sender, intent=intent,
                     length=len(text), ip=ip)

        # Spracuj správu cez handler (rovnaký ako Telegram)
        try:
            if self._handler:
                # Timeout — ak CLI trvá príliš dlho, vráť partial response
                try:
                    import asyncio as _aio
                    response = await _aio.wait_for(
                        self._handler(
                            text, 0, 0,
                            username=sender, chat_type="agent_api",
                        ),
                        timeout=90,  # 90s max pre agent-to-agent
                    )
                except (TimeoutError, _aio.TimeoutError):
                    logger.warning("agent_api_timeout", sender=sender)
                    return web.json_response({
                        "reply": "Premýšľam príliš dlho. Skús jednoduchšiu otázku.",
                        "agent": "john-b2jk",
                        "sender": sender,
                        "timeout": True,
                    }, status=200)  # 200 nie 504 — partial response

                return web.json_response({
                    "reply": response,
                    "agent": "john-b2jk",
                    "sender": sender,
                    "intent": intent,
                })
            else:
                return web.json_response(
                    {"error": "No handler configured"}, status=503
                )
        except Exception as e:
            logger.error("agent_api_error", error=str(e))
            return web.json_response(
                {"error": str(e)}, status=500
            )

    async def _handle_status(self, request: web.Request) -> web.Response:
        """GET /api/status — verejný stav agenta."""
        if not self._agent:
            return web.json_response({"status": "running"})

        status = self._agent.get_status()
        return web.json_response({
            "agent": "john-b2jk",
            "status": "running" if status.get("running") else "stopped",
            "memories": status.get("memory", {}).get("total_memories", 0),
            "tasks": status.get("tasks", {}).get("total_tasks", 0),
            "uptime": "active",
        })

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /api/health — zdravie agenta."""
        if not self._agent:
            return web.json_response({"health": "ok"})

        health = self._agent.watchdog.get_system_health()
        return web.json_response({
            "health": "ok" if not health.alerts else "degraded",
            "cpu_percent": health.cpu_percent,
            "memory_percent": health.memory_percent,
            "modules": health.modules,
            "alerts": health.alerts,
        })

    async def start(self) -> None:
        """Spusti HTTP server."""
        self._app = web.Application()
        self._app.router.add_post("/api/message", self._handle_message)
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        logger.info("agent_api_started", port=self._port)

    async def stop(self) -> None:
        """Zastav HTTP server."""
        if self._runner:
            await self._runner.cleanup()
        logger.info("agent_api_stopped")
