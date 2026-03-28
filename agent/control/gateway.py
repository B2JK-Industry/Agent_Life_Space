"""
Agent Life Space — External Gateway Service

Approval-gated boundary for external delivery and future provider-backed
capabilities. Keeps auth, timeout, retry, rate-limit, trace, and cost handling
inside one deterministic runtime surface.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import aiohttp

from agent.control.denials import make_denial
from agent.control.models import JobKind, TraceRecordKind, UsageSummary
from agent.control.policy import (
    evaluate_external_gateway_access,
    get_external_capability_route,
    get_external_gateway_contract,
    get_external_gateway_policy,
    list_external_capability_providers,
    list_external_capability_routes,
    resolve_external_capability_routes,
)


class ExternalGatewayService:
    """Run approval-gated external delivery through one deterministic boundary."""

    def __init__(
        self,
        *,
        control_plane_state: Any = None,
        approval_queue: Any = None,
        request_executor: Any = None,
        monotonic: Any = None,
        environment: dict[str, str] | None = None,
        secret_lookup: Any = None,
    ) -> None:
        self._control_plane_state = control_plane_state
        self._approval_queue = approval_queue
        self._request_executor = request_executor
        self._monotonic = monotonic or time.monotonic
        self._environment = environment or {}
        self._secret_lookup = secret_lookup
        self._rate_limit_hits: dict[tuple[str, str], list[float]] = {}

    async def send_delivery(
        self,
        *,
        bundle: dict[str, Any],
        job_kind: JobKind,
        target_url: str,
        approval_request_id: str = "",
        gateway_policy_id: str = "approval_before_gateway",
        gateway_contract_id: str = "external_capability_gateway_v1",
        auth_token: str = "",
        target_kind: str = "webhook_json",
        delivery_policy_id: str = "",
        estimated_cost_usd: float = 0.0,
        export_mode: str = "internal",
        provider_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a delivery bundle through the explicit external gateway."""
        contract = get_external_gateway_contract(gateway_contract_id)
        approval_status = self._approval_status(approval_request_id)
        allowed, reason, policy = evaluate_external_gateway_access(
            policy_id=gateway_policy_id,
            target_kind=target_kind,
            target_url=target_url,
            approval_status=approval_status,
            auth_token_provided=bool(auth_token),
        )
        if target_kind not in contract.supported_target_kinds:
            allowed = False
            reason = (
                f"Gateway contract '{contract.id}' does not support target kind "
                f"'{target_kind}'."
            )

        gateway_run_id = f"gateway-{uuid.uuid4().hex[:12]}"
        bundle_id = str(bundle.get("bundle_id", ""))
        metadata = {
            "gateway_run_id": gateway_run_id,
            "policy_id": policy.id,
            "contract_id": contract.id,
            "target_kind": target_kind,
            "target_url": target_url,
            "approval_request_id": approval_request_id,
            "approval_status": approval_status,
            "delivery_policy_id": delivery_policy_id,
            "export_mode": export_mode,
        }
        if provider_context:
            metadata["provider_context"] = dict(provider_context)

        if not allowed:
            denial = make_denial(
                code="gateway_delivery_blocked",
                summary="External gateway delivery blocked",
                detail=reason,
                scope=bundle_id or target_url,
                policy_id=policy.id,
                environment_profile_id=policy.environment_profile_id,
                suggested_action=(
                    "Approve the delivery, provide auth, or use a supported "
                    "gateway target and policy."
                ),
                metadata=metadata,
            )
            self._record_trace(
                title="Gateway delivery blocked",
                detail=reason,
                job_id=str(bundle.get("job_id", "")),
                bundle_id=bundle_id,
                metadata={**metadata, "blocked": True},
            )
            return {
                "ok": False,
                "gateway_run_id": gateway_run_id,
                "error": denial.message,
                "denial": denial.to_dict(),
            }

        allowed_rate, rate_reason = self._allow_rate_limited_call(
            policy=policy,
            target_url=target_url,
        )
        if not allowed_rate:
            denial = make_denial(
                code="gateway_rate_limited",
                summary="External gateway delivery blocked by rate limit",
                detail=rate_reason,
                scope=bundle_id or target_url,
                policy_id=policy.id,
                environment_profile_id=policy.environment_profile_id,
                suggested_action="Wait for the rate-limit window to clear and retry.",
                metadata=metadata,
            )
            self._record_trace(
                title="Gateway delivery rate limited",
                detail=rate_reason,
                job_id=str(bundle.get("job_id", "")),
                bundle_id=bundle_id,
                metadata={**metadata, "blocked": True},
            )
            return {
                "ok": False,
                "gateway_run_id": gateway_run_id,
                "error": denial.message,
                "denial": denial.to_dict(),
            }

        request_payload = self._build_request_payload(
            bundle=bundle,
            contract_id=contract.id,
            gateway_run_id=gateway_run_id,
            target_kind=target_kind,
            target_url=target_url,
            approval_request_id=approval_request_id,
            approval_status=approval_status,
            delivery_policy_id=delivery_policy_id,
            estimated_cost_usd=estimated_cost_usd,
            export_mode=export_mode,
            provider_context=provider_context or {},
        )
        self._record_trace(
            title="Gateway delivery requested",
            detail=(
                f"target={self._display_target(target_url)}; "
                f"policy={policy.id}; contract={contract.id}"
            ),
            job_id=str(bundle.get("job_id", "")),
            bundle_id=bundle_id,
            metadata={**metadata, "request_fields": list(contract.request_fields)},
        )

        result = await self._execute_with_retry(
            target_url=target_url,
            payload=request_payload,
            auth_header_name=policy.auth_header_name,
            auth_token=auth_token,
            timeout_seconds=policy.timeout_seconds,
            max_retries=policy.max_retries,
            retry_backoff_seconds=policy.retry_backoff_seconds,
        )

        base_response = {
            "ok": result["ok"],
            "gateway_run_id": gateway_run_id,
            "attempts": result["attempts"],
            "target_url": target_url,
            "target_kind": target_kind,
            "policy_id": policy.id,
            "contract_id": contract.id,
            "approval_request_id": approval_request_id,
            "approval_status": approval_status,
        }
        if result["ok"]:
            resolved_provider_context = provider_context or {}
            provider_receipt = self._extract_provider_receipt(
                result=result,
                provider_context=resolved_provider_context,
            )
            missing_receipt_fields = self._missing_provider_receipt_fields(
                provider_receipt=provider_receipt,
                provider_context=resolved_provider_context,
            )
            if missing_receipt_fields:
                detail = (
                    "Provider response missing required receipt field(s): "
                    + ", ".join(missing_receipt_fields)
                )
                denial = make_denial(
                    code="gateway_provider_receipt_invalid",
                    summary="Provider-backed gateway delivery returned an incomplete receipt",
                    detail=detail,
                    scope=bundle_id or target_url,
                    policy_id=policy.id,
                    environment_profile_id=policy.environment_profile_id,
                    suggested_action=(
                        "Check the provider response contract or retry through a healthy route."
                    ),
                    metadata={**metadata, "missing_receipt_fields": missing_receipt_fields},
                )
                self._record_trace(
                    title="Gateway provider receipt invalid",
                    detail=detail,
                    job_id=str(bundle.get("job_id", "")),
                    bundle_id=bundle_id,
                    metadata={
                        **metadata,
                        "status_code": result["status_code"],
                        "attempts": result["attempts"],
                        "response_json": result["response_json"],
                        "response_text": result["response_text"],
                        "missing_receipt_fields": missing_receipt_fields,
                    },
                )
                return {
                    **base_response,
                    "ok": False,
                    "status": "failed",
                    "status_code": result["status_code"],
                    "error": denial.message,
                    "denial": denial.to_dict(),
                    "errors": [*result["errors"], detail],
                    "response_json": result["response_json"],
                    "response_text": result["response_text"],
                }
            self._record_trace(
                title="Gateway delivery succeeded",
                detail=(
                    f"target={self._display_target(target_url)}; "
                    f"status={result['status_code']}; attempts={result['attempts']}"
                ),
                job_id=str(bundle.get("job_id", "")),
                bundle_id=bundle_id,
                metadata={
                    **metadata,
                    "status_code": result["status_code"],
                    "attempts": result["attempts"],
                    "response_json": result["response_json"],
                    "response_text": result["response_text"],
                    "provider_receipt": provider_receipt,
                },
            )
            if policy.record_cost:
                self._record_cost(
                    bundle=bundle,
                    job_kind=job_kind,
                    gateway_run_id=gateway_run_id,
                    estimated_cost_usd=estimated_cost_usd,
                    target_url=target_url,
                    attempts=result["attempts"],
                    provider_context=resolved_provider_context,
                    provider_receipt=provider_receipt,
                )
            return {
                **base_response,
                "status": "sent",
                "status_code": result["status_code"],
                "response_json": result["response_json"],
                "response_text": result["response_text"],
                "provider_receipt": provider_receipt,
            }

        denial = make_denial(
            code="gateway_delivery_failed",
            summary="External gateway delivery failed",
            detail=result["error"],
            scope=bundle_id or target_url,
            policy_id=policy.id,
            environment_profile_id=policy.environment_profile_id,
            suggested_action=(
                "Check the gateway target, approval state, and auth token, then retry the send."
            ),
            metadata={**metadata, "attempts": result["attempts"]},
        )
        self._record_trace(
            title="Gateway delivery failed",
            detail=result["error"],
            job_id=str(bundle.get("job_id", "")),
            bundle_id=bundle_id,
            metadata={
                **metadata,
                "attempts": result["attempts"],
                "errors": list(result["errors"]),
                "status_code": result["status_code"],
            },
        )
        return {
            **base_response,
            "status": "failed",
            "status_code": result["status_code"],
            "error": denial.message,
            "denial": denial.to_dict(),
            "errors": result["errors"],
        }

    def describe_capability_catalog(
        self,
        *,
        provider_id: str = "",
        capability_id: str = "",
        job_kind: JobKind | str | None = None,
        export_mode: str = "",
    ) -> dict[str, Any]:
        """Describe configured external providers, routes, and route readiness."""
        routes = list_external_capability_routes(
            provider_id=provider_id,
            capability_id=capability_id,
            job_kind=job_kind,
            export_mode=export_mode,
        )
        providers = [
            provider
            for provider in list_external_capability_providers()
            if not provider_id or provider.id == provider_id
        ]

        route_items: list[dict[str, Any]] = []
        configured_routes = 0
        for route in routes:
            readiness = self._route_readiness(route)
            if readiness["configured"]:
                configured_routes += 1
            route_items.append(
                {
                    "route_id": route.route_id,
                    "provider_id": route.provider_id,
                    "capability_id": route.capability_id,
                    "label": route.label,
                    "description": route.description,
                    "priority": route.priority,
                    "target_kind": route.target_kind,
                    "target_env_var": route.target_env_var,
                    "auth_token_env_var": route.auth_token_env_var,
                    "auth_token_secret_name": route.auth_token_secret_name,
                    "allowed_job_kinds": [kind.value for kind in route.allowed_job_kinds],
                    "allowed_export_modes": list(route.allowed_export_modes),
                    "gateway_contract_id": route.gateway_contract_id,
                    "gateway_policy_id": route.gateway_policy_id,
                    "request_mode": route.request_mode,
                    "response_mode": route.response_mode,
                    "receipt_fields": list(route.receipt_fields),
                    "estimated_cost_usd": route.estimated_cost_usd,
                    "notes": list(route.notes),
                    "configured": readiness["configured"],
                    "target_source": readiness["target_source"],
                    "auth_source": readiness["auth_source"],
                    "target_display": readiness["target_display"],
                    "missing": list(readiness["missing"]),
                    "errors": list(readiness["errors"]),
                }
            )

        provider_items: list[dict[str, Any]] = []
        for provider in providers:
            provider_routes = [route for route in route_items if route["provider_id"] == provider.id]
            provider_items.append(
                {
                    "id": provider.id,
                    "label": provider.label,
                    "description": provider.description,
                    "contract_id": provider.contract_id,
                    "gateway_policy_id": provider.gateway_policy_id,
                    "capability_ids": list(provider.capability_ids),
                    "notes": list(provider.notes),
                    "route_count": len(provider_routes),
                    "configured_route_count": sum(
                        1 for route in provider_routes if route["configured"]
                    ),
                }
            )

        return {
            "summary": {
                "total_providers": len(provider_items),
                "total_routes": len(route_items),
                "configured_routes": configured_routes,
                "unconfigured_routes": len(route_items) - configured_routes,
            },
            "providers": provider_items,
            "routes": route_items,
        }

    async def send_delivery_via_capability(
        self,
        *,
        bundle: dict[str, Any],
        job_kind: JobKind,
        provider_id: str,
        capability_id: str,
        approval_request_id: str = "",
        route_id: str = "",
        target_url: str = "",
        auth_token: str = "",
        delivery_policy_id: str = "",
        estimated_cost_usd: float = 0.0,
        export_mode: str = "internal",
    ) -> dict[str, Any]:
        """Send a bundle through an explicit provider capability route."""
        provider = next(
            (
                item
                for item in list_external_capability_providers()
                if item.id == provider_id
            ),
            None,
        )
        if provider is None:
            denial = make_denial(
                code="gateway_provider_not_found",
                summary="Gateway provider not found",
                detail=f"Unknown external provider '{provider_id}'.",
                scope=bundle.get("bundle_id", "") or provider_id,
                suggested_action="Choose a configured external provider from the gateway catalog.",
                metadata={"provider_id": provider_id, "capability_id": capability_id},
            )
            return {
                "ok": False,
                "provider_id": provider_id,
                "capability_id": capability_id,
                "error": denial.message,
                "denial": denial.to_dict(),
                "attempted_routes": [],
            }
        if route_id:
            route = get_external_capability_route(route_id)
            if (
                route is not None
                and (route.provider_id != provider_id or route.capability_id != capability_id)
            ):
                route = None
            routes = [route] if route is not None else []
        else:
            routes = resolve_external_capability_routes(
                provider_id=provider_id,
                capability_id=capability_id,
                job_kind=job_kind,
                export_mode=export_mode,
            )
        if not routes:
            denial = make_denial(
                code="gateway_capability_not_found",
                summary="Gateway capability route not found",
                detail=(
                    f"No external gateway route exists for provider '{provider_id}' "
                    f"capability '{capability_id}'."
                ),
                scope=bundle.get("bundle_id", "") or provider_id,
                suggested_action=(
                    "Define a provider capability route or choose a supported capability."
                ),
                metadata={
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "route_id": route_id,
                    "export_mode": export_mode,
                },
            )
            return {
                "ok": False,
                "provider_id": provider_id,
                "capability_id": capability_id,
                "error": denial.message,
                "denial": denial.to_dict(),
                "attempted_routes": [],
            }

        attempted_routes: list[dict[str, Any]] = []
        bundle_id = str(bundle.get("bundle_id", ""))
        job_id = str(bundle.get("job_id", ""))
        for fallback_index, route in enumerate(routes):
            readiness = self._route_readiness(
                route,
                target_url_override=target_url,
                auth_token_override=auth_token,
            )
            attempt = {
                "route_id": route.route_id,
                "provider_id": route.provider_id,
                "capability_id": route.capability_id,
                "priority": route.priority,
                "configured": readiness["configured"],
                "target_source": readiness["target_source"],
                "auth_source": readiness["auth_source"],
                "request_mode": route.request_mode,
                "response_mode": route.response_mode,
                "receipt_fields": list(route.receipt_fields),
                "missing": list(readiness["missing"]),
                "errors": list(readiness["errors"]),
            }
            if not readiness["configured"]:
                detail = (
                    f"Route '{route.route_id}' is not ready: "
                    + "; ".join(readiness["missing"] + readiness["errors"])
                )
                self._record_trace(
                    title="Gateway route unavailable",
                    detail=detail,
                    job_id=job_id,
                    bundle_id=bundle_id,
                    metadata={
                        "provider_id": route.provider_id,
                        "capability_id": route.capability_id,
                        "route_id": route.route_id,
                        "fallback_index": fallback_index,
                        "missing": list(readiness["missing"]),
                        "errors": list(readiness["errors"]),
                    },
                )
                attempted_routes.append({**attempt, "status": "unavailable"})
                continue

            provider_context = {
                "provider_id": route.provider_id,
                "provider_label": provider.label,
                "capability_id": route.capability_id,
                "route_id": route.route_id,
                "fallback_index": fallback_index,
                "target_source": readiness["target_source"],
                "auth_source": readiness["auth_source"],
                "request_mode": route.request_mode,
                "response_mode": route.response_mode,
                "receipt_fields": list(route.receipt_fields),
            }
            result = await self.send_delivery(
                bundle=bundle,
                job_kind=job_kind,
                target_url=readiness["target_url"],
                approval_request_id=approval_request_id,
                gateway_policy_id=route.gateway_policy_id,
                gateway_contract_id=route.gateway_contract_id,
                auth_token=readiness["auth_token"],
                target_kind=route.target_kind,
                delivery_policy_id=delivery_policy_id,
                estimated_cost_usd=estimated_cost_usd or route.estimated_cost_usd,
                export_mode=export_mode,
                provider_context=provider_context,
            )
            if result.get("ok"):
                attempted_routes.append(
                    {
                        **attempt,
                        "status": "sent",
                        "target_url": readiness["target_url"],
                        "attempts": result.get("attempts", 0),
                    }
                )
                return {
                    **result,
                    "provider_id": route.provider_id,
                    "capability_id": route.capability_id,
                    "route_id": route.route_id,
                    "fallback_used": fallback_index > 0,
                    "attempted_routes": attempted_routes,
                }

            attempted_routes.append(
                {
                    **attempt,
                    "status": "failed",
                    "target_url": readiness["target_url"],
                    "attempts": result.get("attempts", 0),
                    "error": result.get("error", ""),
                    "status_code": result.get("status_code", 0),
                }
            )
            if not self._should_try_next_route(result):
                return {
                    **result,
                    "provider_id": route.provider_id,
                    "capability_id": route.capability_id,
                    "route_id": route.route_id,
                    "fallback_used": fallback_index > 0,
                    "attempted_routes": attempted_routes,
                }

        denial_code = (
            "gateway_provider_not_configured"
            if attempted_routes and all(item["status"] == "unavailable" for item in attempted_routes)
            else "gateway_provider_delivery_failed"
        )
        denial = make_denial(
            code=denial_code,
            summary="Provider-backed gateway delivery failed",
            detail=(
                f"All configured routes failed or were unavailable for provider "
                f"'{provider_id}' capability '{capability_id}'."
            ),
            scope=bundle_id or provider_id,
            policy_id=provider.gateway_policy_id,
            suggested_action=(
                "Configure a ready provider route, ensure approval/auth are present, "
                "or retry after the downstream endpoint recovers."
            ),
            metadata={
                "provider_id": provider_id,
                "capability_id": capability_id,
                "attempted_routes": attempted_routes,
            },
        )
        return {
            "ok": False,
            "provider_id": provider_id,
            "capability_id": capability_id,
            "error": denial.message,
            "denial": denial.to_dict(),
            "attempted_routes": attempted_routes,
        }

    def _approval_status(self, approval_request_id: str) -> str:
        if not approval_request_id or self._approval_queue is None:
            return ""
        request = self._approval_queue.get_request(approval_request_id)
        if not request:
            return ""
        return str(request.get("status", ""))

    def _allow_rate_limited_call(
        self,
        *,
        policy: Any,
        target_url: str,
    ) -> tuple[bool, str]:
        window = max(1, int(policy.rate_limit_window_seconds))
        call_limit = max(1, int(policy.rate_limit_calls))
        now = float(self._monotonic())
        key = (policy.id, self._rate_limit_key(target_url))
        recent = [
            ts
            for ts in self._rate_limit_hits.get(key, [])
            if now - ts < window
        ]
        if len(recent) >= call_limit:
            self._rate_limit_hits[key] = recent
            return (
                False,
                f"Gateway policy '{policy.id}' allows only {call_limit} call(s) "
                f"per {window} second window for {key[1]}.",
            )
        recent.append(now)
        self._rate_limit_hits[key] = recent
        return True, ""

    def _rate_limit_key(self, target_url: str) -> str:
        parsed = urlparse(target_url)
        return parsed.netloc or target_url

    def _route_readiness(
        self,
        route: Any,
        *,
        target_url_override: str = "",
        auth_token_override: str = "",
    ) -> dict[str, Any]:
        policy = get_external_gateway_policy(route.gateway_policy_id)
        target_url, target_source = self._resolve_target_url(
            route,
            target_url_override=target_url_override,
        )
        auth_token, auth_source = self._resolve_auth_token(
            route,
            auth_token_override=auth_token_override,
        )
        missing: list[str] = []
        errors: list[str] = []
        if not target_url:
            missing.append(route.target_env_var or "target_url")
        else:
            parsed = urlparse(target_url)
            if not parsed.scheme or not parsed.netloc:
                errors.append("configured target URL is not absolute")
        if policy.auth_required and not auth_token:
            missing.append(
                route.auth_token_env_var
                or route.auth_token_secret_name
                or "auth_token"
            )
        return {
            "configured": not missing and not errors,
            "target_url": target_url,
            "target_source": target_source,
            "auth_token": auth_token,
            "auth_source": auth_source,
            "target_display": self._display_target(target_url) if target_url else "",
            "missing": missing,
            "errors": errors,
        }

    def _resolve_target_url(
        self,
        route: Any,
        *,
        target_url_override: str = "",
    ) -> tuple[str, str]:
        if target_url_override:
            return target_url_override, "override"
        if route.target_env_var:
            value = self._environment.get(route.target_env_var, "")
            if value:
                return str(value), "env"
        return "", "unset"

    def _resolve_auth_token(
        self,
        route: Any,
        *,
        auth_token_override: str = "",
    ) -> tuple[str, str]:
        if auth_token_override:
            return auth_token_override, "override"
        if route.auth_token_env_var:
            value = self._environment.get(route.auth_token_env_var, "")
            if value:
                return str(value), "env"
        if route.auth_token_secret_name and callable(self._secret_lookup):
            value = self._secret_lookup(route.auth_token_secret_name)
            if value:
                return str(value), "vault"
        return "", "unset"

    def _should_try_next_route(self, result: dict[str, Any]) -> bool:
        if result.get("ok"):
            return False
        denial_code = str(result.get("denial", {}).get("code", ""))
        status_code = int(result.get("status_code", 0) or 0)
        return (
            denial_code == "gateway_rate_limited"
            or denial_code == "gateway_provider_receipt_invalid"
            or status_code == 0
            or status_code == 429
            or status_code >= 500
        )

    def _build_request_payload(
        self,
        *,
        bundle: dict[str, Any],
        contract_id: str,
        gateway_run_id: str,
        target_kind: str,
        target_url: str,
        approval_request_id: str,
        approval_status: str,
        delivery_policy_id: str,
        estimated_cost_usd: float,
        export_mode: str,
        provider_context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "request_id": gateway_run_id,
            "job_id": bundle.get("job_id", ""),
            "bundle_id": bundle.get("bundle_id", ""),
            "capability_kind": bundle.get("package_type", ""),
            "objective": f"Deliver {bundle.get('package_type', 'package')} externally",
            "constraints": {
                "delivery_policy_id": delivery_policy_id,
                "export_mode": export_mode,
                "artifact_count": bundle.get("artifact_count", 0),
            },
            "approval_context": {
                "approval_request_id": approval_request_id,
                "approval_status": approval_status,
            },
            "budget_context": {
                "record_cost": True,
                "estimated_cost_usd": estimated_cost_usd,
            },
            "input_artifact_ids": list(bundle.get("artifact_ids", [])),
            "provider_context": dict(provider_context),
            "target": {
                "kind": target_kind,
                "url": target_url,
            },
            "delivery_bundle": bundle,
            "contract_id": contract_id,
        }
        if provider_context.get("request_mode") == "obolos_handoff_v1":
            return {
                "request_id": gateway_run_id,
                "provider": {
                    "provider_id": provider_context.get("provider_id", ""),
                    "provider_label": provider_context.get("provider_label", ""),
                    "capability_id": provider_context.get("capability_id", ""),
                    "route_id": provider_context.get("route_id", ""),
                    "request_mode": provider_context.get("request_mode", ""),
                    "response_mode": provider_context.get("response_mode", ""),
                },
                "handoff": {
                    "job_id": bundle.get("job_id", ""),
                    "bundle_id": bundle.get("bundle_id", ""),
                    "package_type": bundle.get("package_type", ""),
                    "export_mode": export_mode,
                    "target_kind": target_kind,
                    "delivery_policy_id": delivery_policy_id,
                    "approval_request_id": approval_request_id,
                    "approval_status": approval_status,
                },
                "artifacts": {
                    "count": bundle.get("artifact_count", 0),
                    "artifact_ids": list(bundle.get("artifact_ids", [])),
                },
                "workspace": {
                    "workspace_id": bundle.get("workspace_id", ""),
                },
                "summary": dict(bundle.get("summary", {})),
                "bundle_ref": {
                    "contract_id": contract_id,
                    "target": {"kind": target_kind, "url": target_url},
                },
                "gateway_request": payload,
            }
        return payload

    def _extract_provider_receipt(
        self,
        *,
        result: dict[str, Any],
        provider_context: dict[str, Any],
    ) -> dict[str, Any]:
        if provider_context.get("response_mode") != "obolos_receipt_v1":
            return {}
        payload = dict(result.get("response_json", {}))
        return {
            "delivery_id": str(
                payload.get("delivery_id")
                or payload.get("receipt_id")
                or payload.get("id")
                or ""
            ),
            "status": str(payload.get("status") or ""),
            "accepted": bool(payload.get("accepted", False)),
            "message": str(payload.get("message") or payload.get("detail") or ""),
            "handoff_url": str(payload.get("handoff_url") or payload.get("url") or ""),
        }

    def _missing_provider_receipt_fields(
        self,
        *,
        provider_receipt: dict[str, Any],
        provider_context: dict[str, Any],
    ) -> list[str]:
        required_fields = [
            str(field)
            for field in provider_context.get("receipt_fields", [])
            if str(field)
        ]
        missing = [
            field for field in required_fields if not provider_receipt.get(field)
        ]
        return missing

    async def _execute_with_retry(
        self,
        *,
        target_url: str,
        payload: dict[str, Any],
        auth_header_name: str,
        auth_token: str,
        timeout_seconds: int,
        max_retries: int,
        retry_backoff_seconds: float,
    ) -> dict[str, Any]:
        errors: list[str] = []
        last_status_code = 0
        attempts = 0
        for attempt in range(1, max_retries + 2):
            attempts = attempt
            try:
                result = await self._post_json(
                    target_url=target_url,
                    payload=payload,
                    auth_header_name=auth_header_name,
                    auth_token=auth_token,
                    timeout_seconds=timeout_seconds,
                )
                if 200 <= int(result["status_code"]) < 300:
                    return {
                        "ok": True,
                        "attempts": attempt,
                        "status_code": int(result["status_code"]),
                        "response_json": result["response_json"],
                        "response_text": result["response_text"],
                        "errors": errors,
                    }
                last_status_code = int(result["status_code"])
                error = f"Gateway returned HTTP {last_status_code}"
                errors.append(error)
                if last_status_code < 500 or attempt > max_retries:
                    break
            except TimeoutError:
                errors.append(f"Gateway request timed out after {timeout_seconds}s")
            except aiohttp.ClientError as e:
                errors.append(f"Gateway network error: {e}")
            if attempt <= max_retries:
                await asyncio.sleep(retry_backoff_seconds * attempt)
        return {
            "ok": False,
            "attempts": attempts,
            "status_code": last_status_code,
            "response_json": {},
            "response_text": "",
            "error": errors[-1] if errors else "Gateway request failed",
            "errors": errors,
        }

    async def _post_json(
        self,
        *,
        target_url: str,
        payload: dict[str, Any],
        auth_header_name: str,
        auth_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if self._request_executor is not None:
            result = await self._request_executor(
                target_url=target_url,
                payload=payload,
                auth_header_name=auth_header_name,
                auth_token=auth_token,
                timeout_seconds=timeout_seconds,
            )
            return {
                "status_code": int(result.get("status_code", 0)),
                "response_json": dict(result.get("response_json", {})),
                "response_text": str(result.get("response_text", "")),
            }

        headers = {}
        if auth_token:
            token_value = (
                auth_token
                if auth_header_name != "Authorization"
                or auth_token.lower().startswith("bearer ")
                else f"Bearer {auth_token}"
            )
            headers[auth_header_name] = token_value
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                target_url,
                json=payload,
                headers=headers,
            ) as response:
                text = await response.text()
                json_payload: dict[str, Any] = {}
                if text:
                    try:
                        candidate = await response.json(content_type=None)
                        if isinstance(candidate, dict):
                            json_payload = candidate
                    except Exception:
                        json_payload = {}
                return {
                    "status_code": response.status,
                    "response_json": json_payload,
                    "response_text": text[:1000],
                }

    def _record_trace(
        self,
        *,
        title: str,
        detail: str,
        job_id: str,
        bundle_id: str,
        metadata: dict[str, Any],
    ) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_trace(
            trace_kind=TraceRecordKind.GATEWAY,
            title=title,
            detail=detail,
            job_id=job_id,
            bundle_id=bundle_id,
            metadata=metadata,
        )

    def _record_cost(
        self,
        *,
        bundle: dict[str, Any],
        job_kind: JobKind,
        gateway_run_id: str,
        estimated_cost_usd: float,
        target_url: str,
        attempts: int,
        provider_context: dict[str, Any],
        provider_receipt: dict[str, Any],
    ) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_cost_entry(
            entry_id=gateway_run_id,
            job_id=str(bundle.get("job_id", "")),
            job_kind=job_kind,
            title=f"External gateway delivery for {bundle.get('bundle_id', '')}",
            workspace_id=str(bundle.get("workspace_id", "")),
            usage=UsageSummary(
                total_tokens=0,
                total_cost_usd=estimated_cost_usd,
                model_used="",
                llm_calls=0,
            ),
            source_type="external_gateway_call",
            metadata={
                "gateway_run_id": gateway_run_id,
                "target_url": target_url,
                "attempts": attempts,
                "provider_context": dict(provider_context),
                "provider_receipt": dict(provider_receipt),
            },
        )

    def _display_target(self, target_url: str) -> str:
        parsed = urlparse(target_url)
        if parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return target_url
