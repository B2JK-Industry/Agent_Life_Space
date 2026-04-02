"""
Agent Life Space — Agent-to-Agent API

HTTP endpoint kde iní agenti môžu posielať správy agentovi.
Nie je závislý na Telegrame — priama komunikácia.

Endpoint:
    POST /api/message — prijmi správu od iného agenta
    POST /api/review  — spusti štruktúrovaný review job
    GET  /api/status  — stav agenta (verejný)
    GET  /api/health  — zdravie (verejný)

Bezpečnosť:
    - Rate limit: 10 req/min per IP (60/min for localhost)
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

from agent.control.denials import make_denial
from agent.core.identity import get_agent_identity

logger = structlog.get_logger(__name__)

_DEFAULT_PORT = 8420
_RATE_LIMIT_EXTERNAL = 10  # requests per minute per IP (external)
_RATE_LIMIT_LOCAL = 60  # requests per minute for localhost (terminal/CLI)
_MAX_MESSAGE_LENGTH = 2000

_LOCAL_IPS = frozenset({"127.0.0.1", "::1", "localhost"})


def _runtime_agent_slug() -> str:
    return get_agent_identity().agent_name.lower().replace(" ", "-")


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
        """Max N requests per minute per IP. Localhost gets a higher limit."""
        now = time.monotonic()
        times = self._request_times[ip]
        times[:] = [t for t in times if now - t < 60]
        limit = _RATE_LIMIT_LOCAL if ip in _LOCAL_IPS else _RATE_LIMIT_EXTERNAL
        if len(times) >= limit:
            return False
        times.append(now)
        return True

    def _json_error_response(
        self,
        *,
        status: int,
        code: str,
        summary: str,
        detail: str,
        scope: str,
        suggested_action: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> web.Response:
        denial = make_denial(
            code=code,
            summary=summary,
            detail=detail,
            scope=scope,
            suggested_action=suggested_action,
            metadata=metadata or {},
        )
        return web.json_response(
            {
                "error": detail or summary,
                "denial": denial.to_dict(),
            },
            status=status,
        )

    async def _handle_message(self, request: web.Request) -> web.Response:
        """POST /api/message — prijmi správu od agenta. Vyžaduje API key."""
        start = time.monotonic()
        ip = request.remote or "unknown"

        # Auth check
        auth_error = self._check_auth(request)
        is_authenticated = not auth_error
        if auth_error:
            self._audit.record(ApiAuditEntry(
                ip=ip, status_code=401, error=auth_error,
            ))
            return self._json_error_response(
                status=401,
                code="agent_api_auth_failed",
                summary="Agent API authentication failed",
                detail=auth_error,
                scope="api.message",
                suggested_action="Provide a valid Authorization bearer token.",
                metadata={"endpoint": "/api/message"},
            )

        if not self._check_rate_limit(ip):
            limit = _RATE_LIMIT_LOCAL if ip in _LOCAL_IPS else _RATE_LIMIT_EXTERNAL
            self._audit.record(ApiAuditEntry(
                ip=ip, status_code=429, error="rate_limited",
            ))
            return self._json_error_response(
                status=429,
                code="agent_api_rate_limited",
                summary="Agent API rate limit exceeded",
                detail=f"Rate limit exceeded ({limit}/min)",
                scope="api.message",
                suggested_action="Retry later or reduce request frequency.",
                metadata={"endpoint": "/api/message", "ip": ip},
            )

        try:
            data = await request.json()
        except Exception:
            self._audit.record(ApiAuditEntry(
                ip=ip, status_code=400, error="invalid_json",
            ))
            return self._json_error_response(
                status=400,
                code="agent_api_invalid_json",
                summary="Agent API request invalid",
                detail="Invalid JSON",
                scope="api.message",
                suggested_action="Send a valid JSON body.",
                metadata={"endpoint": "/api/message"},
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
            return self._json_error_response(
                status=409,
                code="agent_api_replay_blocked",
                summary="Agent API replay blocked",
                detail=replay_error,
                scope="api.message",
                suggested_action="Send a fresh nonce and current timestamp.",
                metadata={"endpoint": "/api/message", "sender": sender},
            )

        if not text:
            self._audit.record(ApiAuditEntry(
                sender=sender, ip=ip, intent=intent,
                status_code=400, error="empty_message",
            ))
            return self._json_error_response(
                status=400,
                code="agent_api_empty_message",
                summary="Agent API message rejected",
                detail="Empty message",
                scope="api.message",
                suggested_action="Provide a non-empty message payload.",
                metadata={"endpoint": "/api/message", "sender": sender},
            )

        if len(text) > _MAX_MESSAGE_LENGTH:
            self._audit.record(ApiAuditEntry(
                sender=sender, ip=ip, intent=intent,
                status_code=400, error="message_too_long",
            ))
            return self._json_error_response(
                status=400,
                code="agent_api_message_too_long",
                summary="Agent API message rejected",
                detail=f"Message too long (max {_MAX_MESSAGE_LENGTH})",
                scope="api.message",
                suggested_action="Shorten the message and retry.",
                metadata={"endpoint": "/api/message", "sender": sender, "length": len(text)},
            )

        logger.info("agent_api_message", sender=sender, intent=intent,
                     length=len(text), ip=ip)

        # Spracuj správu cez handler (rovnaký ako Telegram)
        try:
            if self._handler:
                # Timeout — scale with expected complexity
                # /build and /intake: 600s, programming: 300s, rest: 90s
                try:
                    import asyncio as _aio
                    is_build_command = text.startswith(("/build", "/intake"))
                    if is_build_command:
                        api_timeout = 600
                    else:
                        from agent.core.models import classify_task
                        task_type = classify_task(text)
                        api_timeout = 300 if task_type == "programming" else 90
                    # Authenticated local callers get terminal-level trust.
                    # Remote authenticated callers get restricted trust.
                    is_local = ip in _LOCAL_IPS
                    channel = "terminal" if (is_authenticated and is_local) else "agent_api"
                    is_owner_caller = is_authenticated and is_local
                    response = await _aio.wait_for(
                        self._handler(
                            text, 0, 0,
                            username=sender, chat_type=channel,
                            is_owner=is_owner_caller,
                        ),
                        timeout=api_timeout,
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
                        "agent": _runtime_agent_slug(),
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
                    "agent": _runtime_agent_slug(),
                    "sender": sender,
                    "intent": intent,
                })
            else:
                self._audit.record(ApiAuditEntry(
                    ip=ip, status_code=503, error="no_handler",
                ))
                return self._json_error_response(
                    status=503,
                    code="agent_api_no_handler",
                    summary="Agent API handler unavailable",
                    detail="No handler configured",
                    scope="api.message",
                    suggested_action="Configure the agent message handler before retrying.",
                    metadata={"endpoint": "/api/message"},
                )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            self._audit.record(ApiAuditEntry(
                sender=sender, ip=ip, intent=intent,
                status_code=500, duration_ms=duration, error="internal",
            ))
            logger.error("agent_api_error", error=str(e))
            return self._json_error_response(
                status=500,
                code="agent_api_internal_error",
                summary="Agent API processing failed",
                detail="Internal processing error",
                scope="api.message",
                suggested_action="Inspect the server logs and retry when the handler is healthy.",
                metadata={"endpoint": "/api/message", "sender": sender},
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
            return self._json_error_response(
                status=401,
                code="agent_api_auth_failed",
                summary="Agent API authentication failed",
                detail=auth_error,
                scope="api.review",
                suggested_action="Provide a valid Authorization bearer token.",
                metadata={"endpoint": "/api/review"},
            )

        if not self._check_rate_limit(ip):
            limit = _RATE_LIMIT_LOCAL if ip in _LOCAL_IPS else _RATE_LIMIT_EXTERNAL
            self._audit.record(ApiAuditEntry(
                ip=ip, intent="review", status_code=429, error="rate_limited",
            ))
            return self._json_error_response(
                status=429,
                code="agent_api_rate_limited",
                summary="Agent API rate limit exceeded",
                detail=f"Rate limit exceeded ({limit}/min)",
                scope="api.review",
                suggested_action="Retry later or reduce request frequency.",
                metadata={"endpoint": "/api/review", "ip": ip},
            )

        try:
            data = await request.json()
        except Exception:
            self._audit.record(ApiAuditEntry(
                ip=ip, intent="review", status_code=400, error="invalid_json",
            ))
            return self._json_error_response(
                status=400,
                code="agent_api_invalid_json",
                summary="Agent API request invalid",
                detail="Invalid JSON",
                scope="api.review",
                suggested_action="Send a valid JSON body.",
                metadata={"endpoint": "/api/review"},
            )

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
            return self._json_error_response(
                status=409,
                code="agent_api_replay_blocked",
                summary="Agent API replay blocked",
                detail=replay_error,
                scope="api.review",
                suggested_action="Send a fresh nonce and current timestamp.",
                metadata={"endpoint": "/api/review", "sender": sender},
            )

        if self._agent is None:
            self._audit.record(ApiAuditEntry(
                sender=sender,
                ip=ip,
                intent="review",
                status_code=503,
                error="no_agent",
            ))
            return self._json_error_response(
                status=503,
                code="agent_api_no_agent",
                summary="Agent API review unavailable",
                detail="No agent configured",
                scope="api.review",
                suggested_action="Configure the orchestrator before calling /api/review.",
                metadata={"endpoint": "/api/review"},
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
            return self._json_error_response(
                status=400,
                code="agent_api_invalid_review_type",
                summary="Agent API review request invalid",
                detail=f"Invalid review_type '{review_type_raw}'",
                scope="api.review",
                suggested_action="Use one of the supported review_type values.",
                metadata={"endpoint": "/api/review", "sender": sender},
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
            return self._json_error_response(
                status=400,
                code="agent_api_review_validation_failed",
                summary="Agent API review request invalid",
                detail="; ".join(errors),
                scope="api.review",
                suggested_action="Fix the review request payload and retry.",
                metadata={"endpoint": "/api/review", "sender": sender},
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
                    "denial": job.denial,
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
            return self._json_error_response(
                status=500,
                code="agent_api_internal_error",
                summary="Agent API processing failed",
                detail="Internal processing error",
                scope="api.review",
                suggested_action="Inspect the server logs and retry when the review runtime is healthy.",
                metadata={"endpoint": "/api/review", "sender": sender},
            )

    async def _handle_status(self, request: web.Request) -> web.Response:
        """GET /api/status — minimal public status (no internal details)."""
        return web.json_response({
            "agent": _runtime_agent_slug(),
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

    # ─────────────────────────────────────────────
    # Operator Dashboard (HTML)
    # ─────────────────────────────────────────────

    async def _handle_dashboard(self, request: web.Request) -> web.Response:
        """GET /dashboard — self-contained operator dashboard.

        Requires API key via Authorization header or ?key= query param.
        Without auth, returns a minimal login page.
        """
        key_param = request.query.get("key", "")
        if key_param and key_param in self._api_keys:
            from agent.social.dashboard import render_dashboard_html
            html = render_dashboard_html(api_key_hint=key_param)
            return web.Response(text=html, content_type="text/html")
        else:
            auth_error = self._check_auth(request)
            if auth_error:
                return web.Response(
                    text=self._dashboard_auth_page(),
                    content_type="text/html",
                )
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html()
        return web.Response(text=html, content_type="text/html")

    @staticmethod
    def _dashboard_auth_page() -> str:
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            "<title>Agent Dashboard</title>"
            "<style>body{font-family:monospace;background:#0f1117;color:#e1e4eb;"
            "display:flex;justify-content:center;align-items:center;height:100vh;margin:0}"
            ".box{text-align:center}input{background:#1a1d27;border:1px solid #2a2d37;"
            "color:#e1e4eb;padding:10px 16px;border-radius:6px;width:300px;font-size:14px;"
            "font-family:inherit;margin:12px 0}button{background:#6c8aff;color:#fff;"
            "border:none;padding:10px 24px;border-radius:6px;cursor:pointer;font-family:inherit}"
            '</style></head><body><div class="box"><h2>Agent Dashboard</h2>'
            '<form method="get"><input name="key" type="password" placeholder="API Key" autofocus>'
            "<br><button type=submit>Login</button></form></div></body></html>"
        )

    # ─────────────────────────────────────────────
    # Operator Control-Plane Endpoints (auth required)
    # ─────────────────────────────────────────────

    def _parse_int_param(
        self, request: web.Request, name: str, default: int, scope: str,
    ) -> int | web.Response:
        """Parse an integer query param. Returns int or a 400 Response on failure."""
        raw = request.query.get(name, str(default))
        try:
            return int(raw)
        except (ValueError, TypeError):
            return self._json_error_response(
                status=400,
                code="invalid_query_param",
                summary="Invalid query parameter",
                detail=f"'{name}' must be an integer, got '{raw}'",
                scope=scope,
            )

    async def _handle_operator_jobs(self, request: web.Request) -> web.Response:
        """GET /api/operator/jobs — list product jobs with optional filters."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.jobs",
            )
        if not self._agent or not hasattr(self._agent, "control_plane"):
            return web.json_response({"jobs": [], "total": 0})

        limit = self._parse_int_param(request, "limit", 50, "api.operator.jobs")
        if isinstance(limit, web.Response):
            return limit

        params = request.query
        jobs = self._agent.control_plane.list_product_jobs(
            job_kind=params.get("kind", ""),
            status=params.get("status", ""),
            limit=min(limit, 200),
        )
        return web.json_response({
            "jobs": [j.to_dict() for j in jobs],
            "total": len(jobs),
        })

    async def _handle_operator_job_detail(self, request: web.Request) -> web.Response:
        """GET /api/operator/jobs/{job_id} — single job detail."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.jobs",
            )
        if not self._agent or not hasattr(self._agent, "control_plane"):
            return web.json_response({"error": "No control plane"}, status=503)

        job_id = request.match_info["job_id"]
        job = self._agent.control_plane.get_product_job(job_id)
        if job is None:
            return web.json_response({"error": f"Job '{job_id}' not found"}, status=404)
        return web.json_response(job.to_dict())

    async def _handle_operator_report(self, request: web.Request) -> web.Response:
        """GET /api/operator/report — structured operator report."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.report",
            )
        if not self._agent or not hasattr(self._agent, "reporting"):
            return self._json_error_response(
                status=503, code="operator_report_unavailable",
                summary="Operator report unavailable",
                detail="Reporting service not initialized",
                scope="api.operator.report",
            )
        try:
            limit_raw = request.query.get("limit", "20")
            limit = min(int(limit_raw), 200)
        except (ValueError, TypeError):
            return self._json_error_response(
                status=400, code="invalid_query_param",
                summary="Invalid query parameter",
                detail=f"'limit' must be an integer, got '{request.query.get('limit', '')}'",
                scope="api.operator.report",
            )
        try:
            report = self._agent.reporting.get_report(limit=limit)
            return web.json_response(report)
        except Exception as e:
            logger.error("operator_report_error", error=str(e))
            return self._json_error_response(
                status=500, code="operator_report_error",
                summary="Operator report failed",
                detail="Internal error generating report",
                scope="api.operator.report",
            )

    async def _handle_operator_telemetry(self, request: web.Request) -> web.Response:
        """GET /api/operator/telemetry — telemetry summary."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.telemetry",
            )
        if not self._agent or not hasattr(self._agent, "control_plane"):
            return web.json_response({"snapshots": 0})

        window = self._parse_int_param(request, "window_hours", 24, "api.operator.telemetry")
        if isinstance(window, web.Response):
            return window
        summary = self._agent.control_plane.get_telemetry_summary(window_hours=window)
        return web.json_response(summary)

    async def _handle_operator_retention(self, request: web.Request) -> web.Response:
        """GET /api/operator/retention — retention posture + table stats."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.retention",
            )
        if not self._agent or not hasattr(self._agent, "control_plane"):
            return web.json_response({"total": 0})

        posture = self._agent.control_plane.get_retention_posture()
        stats = self._agent.control_plane.get_stats()
        return web.json_response({**posture, "table_stats": stats})

    async def _handle_operator_margin(self, request: web.Request) -> web.Response:
        """GET /api/operator/margin — margin summary."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.margin",
            )
        if not self._agent or not hasattr(self._agent, "control_plane"):
            return web.json_response({"total_jobs": 0})

        limit = self._parse_int_param(request, "limit", 100, "api.operator.margin")
        if isinstance(limit, web.Response):
            return limit
        summary = self._agent.control_plane.get_margin_summary(limit=min(limit, 500))
        return web.json_response(summary)

    async def _handle_operator_workflows(self, request: web.Request) -> web.Response:
        """GET /api/operator/workflows — list recurring workflows."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.workflows",
            )
        if not self._agent or not hasattr(self._agent, "recurring_workflows"):
            return web.json_response({"workflows": []})

        workflows = self._agent.recurring_workflows.list_workflows(
            status=request.query.get("status", ""),
        )
        return web.json_response({
            "workflows": [w.to_dict() for w in workflows],
            "total": len(workflows),
        })

    async def _handle_operator_pipelines(self, request: web.Request) -> web.Response:
        """GET /api/operator/pipelines — list job pipelines."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.pipelines",
            )
        if not self._agent or not hasattr(self._agent, "pipeline_orchestrator"):
            return web.json_response({"pipelines": []})

        pipelines = self._agent.pipeline_orchestrator.list_pipelines(
            status=request.query.get("status", ""),
        )
        return web.json_response({
            "pipelines": [p.to_dict() for p in pipelines],
            "total": len(pipelines),
        })

    async def _handle_operator_archive(self, request: web.Request) -> web.Response:
        """GET /api/operator/archive — list archives or export a table.

        ?action=list — list existing archive files
        ?action=export&table=cost_ledger_entries&older_than_days=730 — export to CSV
        """
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.archive",
            )
        if not self._agent or not hasattr(self._agent, "control_plane"):
            return web.json_response({"error": "No control plane"}, status=503)

        from agent.control.archival import ArchivalService
        storage = self._agent.control_plane.get_storage_for_archival()
        if storage is None:
            return self._json_error_response(
                status=503, code="archival_unavailable",
                summary="Archival unavailable",
                detail="Control plane storage not initialized",
                scope="api.operator.archive",
            )
        archival = ArchivalService(storage)
        action = request.query.get("action", "list")

        if action == "export":
            table = request.query.get("table", "")
            older = self._parse_int_param(request, "older_than_days", 0, "api.operator.archive")
            if isinstance(older, web.Response):
                return older
            older_than_days = older
            try:
                path = archival.export_table(
                    table, older_than_days=older_than_days,
                )
                return web.json_response({
                    "exported": bool(path),
                    "filename": path,
                    "table": table,
                })
            except (ValueError, RuntimeError) as e:
                return web.json_response({"error": str(e)}, status=400)
        else:
            return web.json_response({"archives": archival.list_archives()})

    async def _handle_operator_archive_download(self, request: web.Request) -> web.Response:
        """GET /api/operator/archive/download/{filename} — download a CSV archive."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.archive.download",
            )
        if not self._agent or not hasattr(self._agent, "control_plane"):
            return self._json_error_response(
                status=503, code="archival_unavailable",
                summary="Archival unavailable",
                detail="Control plane not initialized",
                scope="api.operator.archive.download",
            )

        from agent.control.archival import ArchivalService
        storage = self._agent.control_plane.get_storage_for_archival()
        if storage is None:
            return self._json_error_response(
                status=503, code="archival_unavailable",
                summary="Archival unavailable",
                detail="Control plane storage not initialized",
                scope="api.operator.archive.download",
            )

        filename = request.match_info["filename"]
        archival = ArchivalService(storage)
        filepath = archival.get_archive_path(filename)
        if filepath is None:
            return web.json_response(
                {"error": f"Archive '{filename}' not found or invalid"},
                status=404,
            )

        return web.FileResponse(
            filepath,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def _handle_operator_settlements(self, request: web.Request) -> web.Response:
        """GET /api/operator/settlements — list settlement requests with optional status filter."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.settlements",
            )
        if not self._agent or not hasattr(self._agent, "settlement"):
            return web.json_response({"settlements": [], "note": "Settlement service not initialized"})

        status_filter = request.query.get("status", "")
        settlements = self._agent.settlement.list_settlements(status=status_filter)
        return web.json_response({
            "settlements": [s.to_dict() for s in settlements],
            "total": len(settlements),
        })

    async def _handle_operator_settlement_action(self, request: web.Request) -> web.Response:
        """POST /api/operator/settlements/{id}/{action} — approve, deny, or execute a settlement."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.settlements",
            )
        if not self._agent or not hasattr(self._agent, "settlement"):
            return self._json_error_response(
                status=503, code="settlement_unavailable",
                summary="Settlement unavailable",
                detail="Settlement service not initialized",
                scope="api.operator.settlements",
            )

        settlement_id = request.match_info["settlement_id"]
        action = request.match_info["action"]
        svc = self._agent.settlement

        try:
            body = await request.json()
        except Exception:
            body = {}

        note = str(body.get("note", ""))

        if action == "approve":
            result = svc.approve_settlement(settlement_id, note=note)
            if result:
                return web.json_response({"ok": True, "settlement": result.to_dict()})
            return web.json_response(
                {"ok": False, "error": "Settlement not found or not pending"}, status=404,
            )

        if action == "deny":
            result = svc.deny_settlement(settlement_id, note=note)
            if result:
                return web.json_response({"ok": True, "settlement": result.to_dict()})
            return web.json_response(
                {"ok": False, "error": "Settlement not found or not pending"}, status=404,
            )

        if action == "execute":
            result = await svc.execute_topup(settlement_id)
            return web.json_response(result)

        return self._json_error_response(
            status=400, code="invalid_settlement_action",
            summary="Invalid action",
            detail=f"Action '{action}' not recognized. Use approve, deny, or execute.",
            scope="api.operator.settlements",
        )

    async def _handle_operator_audit(self, request: web.Request) -> web.Response:
        """GET /api/operator/audit — API audit log stats + recent entries."""
        auth_error = self._check_auth(request)
        if auth_error:
            return self._json_error_response(
                status=401, code="auth_failed", summary="Auth failed",
                detail=auth_error, scope="api.operator.audit",
            )
        limit = self._parse_int_param(request, "limit", 50, "api.operator.audit")
        if isinstance(limit, web.Response):
            return limit
        return web.json_response({
            "stats": self._audit.get_stats(),
            "recent": self._audit.get_recent(limit=min(limit, 200)),
        })

    async def start(self) -> None:
        """Spusti HTTP server."""
        self._app = web.Application()
        # Core endpoints
        self._app.router.add_post("/api/message", self._handle_message)
        self._app.router.add_post("/api/review", self._handle_review)
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/health", self._handle_health)
        # Operator dashboard
        self._app.router.add_get("/dashboard", self._handle_dashboard)
        # Operator control-plane endpoints
        self._app.router.add_get("/api/operator/jobs", self._handle_operator_jobs)
        self._app.router.add_get("/api/operator/jobs/{job_id}", self._handle_operator_job_detail)
        self._app.router.add_get("/api/operator/report", self._handle_operator_report)
        self._app.router.add_get("/api/operator/telemetry", self._handle_operator_telemetry)
        self._app.router.add_get("/api/operator/retention", self._handle_operator_retention)
        self._app.router.add_get("/api/operator/margin", self._handle_operator_margin)
        self._app.router.add_get("/api/operator/workflows", self._handle_operator_workflows)
        self._app.router.add_get("/api/operator/pipelines", self._handle_operator_pipelines)
        self._app.router.add_get("/api/operator/archive", self._handle_operator_archive)
        self._app.router.add_get("/api/operator/archive/download/{filename}", self._handle_operator_archive_download)
        self._app.router.add_get("/api/operator/settlements", self._handle_operator_settlements)
        self._app.router.add_post("/api/operator/settlements/{settlement_id}/{action}", self._handle_operator_settlement_action)
        self._app.router.add_get("/api/operator/audit", self._handle_operator_audit)

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
