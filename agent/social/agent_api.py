"""
Agent Life Space — Agent-to-Agent API

HTTP endpoint kde iní agenti môžu posielať správy Johnovi.
Nie je závislý na Telegrame — priama komunikácia.

Endpoint:
    POST /api/message — prijmi správu od iného agenta
    POST /api/review  — spusti štruktúrovaný review job
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

import time
from collections import defaultdict
from typing import Any

import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

_DEFAULT_PORT = 8420
_RATE_LIMIT = 10  # requests per minute per IP
_MAX_MESSAGE_LENGTH = 2000


class ApiAuditEntry:
    """Single API request audit record."""

    __slots__ = ("timestamp", "sender", "ip", "intent", "status_code", "duration_ms", "error")

    def __init__(
        self,
        sender: str = "",
        ip: str = "",
        intent: str = "",
        status_code: int = 200,
        duration_ms: int = 0,
        error: str = "",
    ) -> None:
        self.timestamp = time.monotonic()
        self.sender = sender
        self.ip = ip
        self.intent = intent
        self.status_code = status_code
        self.duration_ms = duration_ms
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender": self.sender,
            "ip": self.ip,
            "intent": self.intent,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class ApiAuditLog:
    """Ring buffer of API request audit entries."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._entries: list[ApiAuditEntry] = []
        self._max = max_entries
        self._total_requests = 0
        self._total_errors = 0
        self._total_rate_limited = 0
        self._total_auth_failures = 0

    def record(self, entry: ApiAuditEntry) -> None:
        self._entries.append(entry)
        self._total_requests += 1
        if entry.status_code >= 400:
            self._total_errors += 1
        if entry.status_code == 429:
            self._total_rate_limited += 1
        if entry.status_code == 401:
            self._total_auth_failures += 1
        if len(self._entries) > self._max:
            self._entries.pop(0)

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries[-limit:]]

    def get_stats(self) -> dict[str, Any]:
        by_sender: dict[str, int] = {}
        for e in self._entries:
            by_sender[e.sender] = by_sender.get(e.sender, 0) + 1
        return {
            "total_requests": self._total_requests,
            "total_errors": self._total_errors,
            "total_rate_limited": self._total_rate_limited,
            "total_auth_failures": self._total_auth_failures,
            "by_sender": by_sender,
        }


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
        bind_host: str = "127.0.0.1",
    ) -> None:
        self._handler = handler_callback  # async fn(text, sender) -> str
        self._agent = agent
        self._port = port
        self._bind_host = bind_host
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        # API key autentifikácia — len autorizovaní agenti
        self._api_keys: set[str] = set(api_keys or [])
        # Rate limiting
        self._request_times: dict[str, list[float]] = defaultdict(list)
        # Audit + telemetry
        self._audit = ApiAuditLog()
        # Replay protection
        from agent.social.request_identity import ReplayProtection
        self._replay = ReplayProtection()

    @property
    def audit_log(self) -> ApiAuditLog:
        return self._audit

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
            # SECURITY: Bez API keys sa /message blokuje
            # Dev mode odstránený — vždy vyžaduj auth pre message endpoint
            logger.warning("agent_api_no_keys_configured")
            return "No API keys configured. Set AGENT_API_KEY env variable."

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
        start = time.monotonic()
        ip = request.remote or "unknown"

        # Auth check
        auth_error = self._check_auth(request)
        if auth_error:
            self._audit.record(ApiAuditEntry(
                ip=ip, status_code=401, error=auth_error,
            ))
            return web.json_response({"error": auth_error}, status=401)

        if not self._check_rate_limit(ip):
            self._audit.record(ApiAuditEntry(
                ip=ip, status_code=429, error="rate_limited",
            ))
            return web.json_response(
                {"error": "Rate limit exceeded (10/min)"}, status=429
            )

        try:
            data = await request.json()
        except Exception:
            self._audit.record(ApiAuditEntry(
                ip=ip, status_code=400, error="invalid_json",
            ))
            return web.json_response(
                {"error": "Invalid JSON"}, status=400
            )

        # Štruktúrované správy — podporuj aj jednoduché aj rozšírené
        text = data.get("message", "").strip()
        sender = data.get("sender", "unknown_agent")
        intent = data.get("intent", "")  # optional: "question", "collaboration", "ping"
        nonce = data.get("nonce", "")  # optional: replay protection
        req_timestamp = data.get("timestamp", 0.0)

        # Replay protection
        replay_error = self._replay.check_and_record(nonce, req_timestamp)
        if replay_error:
            self._audit.record(ApiAuditEntry(
                sender=sender, ip=ip, intent=intent,
                status_code=409, error=replay_error,
            ))
            return web.json_response(
                {"error": replay_error}, status=409
            )

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
                except TimeoutError:
                    duration = int((time.monotonic() - start) * 1000)
                    self._audit.record(ApiAuditEntry(
                        sender=sender, ip=ip, intent=intent,
                        status_code=200, duration_ms=duration, error="timeout",
                    ))
                    logger.warning("agent_api_timeout", sender=sender)
                    return web.json_response({
                        "reply": "Premýšľam príliš dlho. Skús jednoduchšiu otázku.",
                        "agent": "john-b2jk",
                        "sender": sender,
                        "timeout": True,
                    }, status=200)

                duration = int((time.monotonic() - start) * 1000)
                self._audit.record(ApiAuditEntry(
                    sender=sender, ip=ip, intent=intent,
                    status_code=200, duration_ms=duration,
                ))
                return web.json_response({
                    "reply": response,
                    "agent": "john-b2jk",
                    "sender": sender,
                    "intent": intent,
                })
            else:
                self._audit.record(ApiAuditEntry(
                    ip=ip, status_code=503, error="no_handler",
                ))
                return web.json_response(
                    {"error": "No handler configured"}, status=503
                )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            self._audit.record(ApiAuditEntry(
                sender=sender, ip=ip, intent=intent,
                status_code=500, duration_ms=duration, error="internal",
            ))
            logger.error("agent_api_error", error=str(e))
            return web.json_response(
                {"error": "Internal processing error"}, status=500
            )

    async def _handle_review(self, request: web.Request) -> web.Response:
        """POST /api/review — run a structured review job through ReviewService."""
        start = time.monotonic()
        ip = request.remote or "unknown"

        auth_error = self._check_auth(request)
        if auth_error:
            self._audit.record(ApiAuditEntry(
                ip=ip, intent="review", status_code=401, error=auth_error,
            ))
            return web.json_response({"error": auth_error}, status=401)

        if not self._check_rate_limit(ip):
            self._audit.record(ApiAuditEntry(
                ip=ip, intent="review", status_code=429, error="rate_limited",
            ))
            return web.json_response(
                {"error": "Rate limit exceeded (10/min)"}, status=429
            )

        try:
            data = await request.json()
        except Exception:
            self._audit.record(ApiAuditEntry(
                ip=ip, intent="review", status_code=400, error="invalid_json",
            ))
            return web.json_response({"error": "Invalid JSON"}, status=400)

        sender = str(data.get("sender", "api_review_client")).strip() or "api_review_client"
        nonce = data.get("nonce", "")
        req_timestamp = data.get("timestamp", 0.0)
        replay_error = self._replay.check_and_record(nonce, req_timestamp)
        if replay_error:
            self._audit.record(ApiAuditEntry(
                sender=sender,
                ip=ip,
                intent="review",
                status_code=409,
                error=replay_error,
            ))
            return web.json_response({"error": replay_error}, status=409)

        if self._agent is None:
            self._audit.record(ApiAuditEntry(
                sender=sender,
                ip=ip,
                intent="review",
                status_code=503,
                error="no_agent",
            ))
            return web.json_response(
                {"error": "No agent configured"}, status=503
            )

        from agent.review.models import ReviewIntake, ReviewJobType

        repo_path = str(data.get("repo_path", "")).strip()
        diff_spec = str(data.get("diff_spec", "")).strip()
        review_type_raw = str(
            data.get("review_type", ReviewJobType.REPO_AUDIT.value)
        ).strip() or ReviewJobType.REPO_AUDIT.value
        try:
            review_type = ReviewJobType(review_type_raw)
        except ValueError:
            self._audit.record(ApiAuditEntry(
                sender=sender,
                ip=ip,
                intent="review",
                status_code=400,
                error="invalid_review_type",
            ))
            return web.json_response(
                {"error": f"Invalid review_type '{review_type_raw}'"},
                status=400,
            )

        intake = ReviewIntake(
            repo_path=repo_path,
            diff_spec=diff_spec,
            review_type=review_type,
            focus_areas=[
                str(item)
                for item in data.get("focus_areas", [])
                if str(item).strip()
            ],
            max_files=int(data.get("max_files", 100) or 100),
            include_patterns=[
                str(item)
                for item in data.get("include_patterns", [])
                if str(item).strip()
            ],
            exclude_patterns=[
                str(item)
                for item in data.get("exclude_patterns", [])
                if str(item).strip()
            ],
            requester=sender,
            context=str(data.get("context", "")).strip(),
            source="api",
        )

        errors = intake.validate()
        if errors:
            self._audit.record(ApiAuditEntry(
                sender=sender,
                ip=ip,
                intent="review",
                status_code=400,
                error="validation_failed",
            ))
            return web.json_response(
                {"error": "; ".join(errors)},
                status=400,
            )

        try:
            job = await self._agent.run_review_job(intake)
            status_code = 200 if job.status.value == "completed" else 422
            duration = int((time.monotonic() - start) * 1000)
            self._audit.record(ApiAuditEntry(
                sender=sender,
                ip=ip,
                intent="review",
                status_code=status_code,
                duration_ms=duration,
                error=job.error,
            ))
            return web.json_response(
                {
                    "job_id": job.id,
                    "job_kind": job.job_kind.value,
                    "status": job.status.value,
                    "phase": job.phase.value,
                    "verdict": job.report.verdict,
                    "finding_counts": job.report.finding_counts,
                    "artifact_count": len(job.artifacts),
                    "execution_mode": job.execution_mode.value,
                    "source": job.source,
                    "error": job.error,
                },
                status=status_code,
            )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            self._audit.record(ApiAuditEntry(
                sender=sender,
                ip=ip,
                intent="review",
                status_code=500,
                duration_ms=duration,
                error="internal",
            ))
            logger.error("agent_api_review_error", error=str(e))
            return web.json_response(
                {"error": "Internal processing error"},
                status=500,
            )

    async def _handle_status(self, request: web.Request) -> web.Response:
        """GET /api/status — minimal public status (no internal details)."""
        return web.json_response({
            "agent": "john-b2jk",
            "status": "running",
        })

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /api/health — minimal health check (no internal metrics exposed)."""
        if not self._agent:
            return web.json_response({"health": "ok"})

        health = self._agent.watchdog.get_system_health()
        # SECURITY: Only expose ok/degraded — no CPU, RAM, module names, or alerts
        return web.json_response({
            "health": "ok" if not health.alerts else "degraded",
        })

    async def start(self) -> None:
        """Spusti HTTP server."""
        self._app = web.Application()
        self._app.router.add_post("/api/message", self._handle_message)
        self._app.router.add_post("/api/review", self._handle_review)
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._bind_host, self._port)
        await site.start()
        logger.info("agent_api_started", port=self._port)

    async def stop(self) -> None:
        """Zastav HTTP server."""
        if self._runner:
            await self._runner.cleanup()
        logger.info("agent_api_stopped")
