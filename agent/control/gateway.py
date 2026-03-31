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
from agent.control.models import ArtifactKind, JobKind, TraceRecordKind, UsageSummary
from agent.control.policy import (
    classify_provider_delivery_outcome,
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
            provider_outcome = classify_provider_delivery_outcome(
                receipt_status=str(provider_receipt.get("status", "")),
                ok=True,
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
                    "provider_outcome": provider_outcome,
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
                    provider_outcome=provider_outcome,
                )
            return {
                **base_response,
                "status": "sent",
                "status_code": result["status_code"],
                "response_json": result["response_json"],
                "response_text": result["response_text"],
                "provider_receipt": provider_receipt,
                "provider_outcome": provider_outcome,
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
                    "default_target_url": route.default_target_url,
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

        # Capability-to-providers map for multi-provider visibility
        capability_map: dict[str, list[str]] = {}
        for provider in providers:
            for cap in provider.capability_ids:
                capability_map.setdefault(cap, []).append(provider.id)

        return {
            "summary": {
                "total_providers": len(provider_items),
                "total_routes": len(route_items),
                "configured_routes": configured_routes,
                "unconfigured_routes": len(route_items) - configured_routes,
                "total_capabilities": len(capability_map),
            },
            "providers": provider_items,
            "routes": route_items,
            "capability_map": capability_map,
        }

    async def call_api_across_providers(
        self,
        *,
        capability_id: str,
        resource: str = "",
        method: str = "",
        query_params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
        auth_token: str = "",
        job_id: str = "",
        requester: str = "operator",
        title: str = "",
    ) -> dict[str, Any]:
        """Call an API capability across all providers until one succeeds.

        Unlike call_api_via_capability() which requires a specific provider_id,
        this method discovers all providers supporting the requested capability
        and tries them in order until one succeeds.
        """
        from agent.control.policy import list_providers_for_capability

        providers = list_providers_for_capability(capability_id)
        if not providers:
            return {
                "ok": False,
                "error": f"No providers found for capability '{capability_id}'.",
                "provider_id": "",
                "capability_id": capability_id,
            }

        last_error = ""
        for provider in providers:
            result = await self.call_api_via_capability(
                provider_id=provider.id,
                capability_id=capability_id,
                resource=resource,
                method=method,
                query_params=query_params,
                json_payload=json_payload,
                form_data=form_data,
                auth_token=auth_token,
                job_id=job_id,
                requester=requester,
                title=title,
            )
            if result.get("ok"):
                result["resolved_provider_id"] = provider.id
                return result
            last_error = str(result.get("error", "unknown"))
            # Only continue to next provider on retryable errors
            if result.get("denial", {}).get("code") in (
                "gateway_provider_not_configured",
                "gateway_capability_not_found",
            ):
                continue
            # Non-retryable (e.g. payment required, auth failure) — stop
            return result

        return {
            "ok": False,
            "error": f"All providers failed for capability '{capability_id}': {last_error}",
            "provider_id": "",
            "capability_id": capability_id,
            "providers_tried": [p.id for p in providers],
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

    async def call_api_via_capability(
        self,
        *,
        provider_id: str,
        capability_id: str,
        resource: str = "",
        method: str = "",
        query_params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
        route_id: str = "",
        auth_token: str = "",
        gateway_policy_id: str = "",
        job_id: str = "",
        requester: str = "operator",
        title: str = "",
    ) -> dict[str, Any]:
        """Call one provider-backed API capability through the gateway boundary."""
        provider = next(
            (item for item in list_external_capability_providers() if item.id == provider_id),
            None,
        )
        operation_id = job_id or f"api-{uuid.uuid4().hex[:12]}"
        normalized_title = title or f"External API call: {provider_id}/{capability_id}"
        if self._control_plane_state is not None:
            self._control_plane_state.record_product_job(
                job_id=operation_id,
                job_kind=JobKind.OPERATE,
                title=normalized_title,
                status="running",
                subkind="external_api_call",
                requester=requester,
                source="gateway_api",
                execution_mode="network_only",
                scope=resource or capability_id,
                metadata={
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "query_params": dict(query_params or {}),
                },
            )

        if provider is None:
            denial = make_denial(
                code="gateway_provider_not_found",
                summary="API provider not found",
                detail=f"Unknown external provider '{provider_id}'.",
                scope=provider_id,
                suggested_action="Choose a configured provider from the gateway catalog.",
                metadata={"provider_id": provider_id, "capability_id": capability_id},
            )
            self._complete_api_job(
                operation_id,
                normalized_title,
                requester=requester,
                status="blocked",
                outcome="provider_not_found",
                blocked_reason=denial.message,
            )
            return {
                "ok": False,
                "job_id": operation_id,
                "provider_id": provider_id,
                "capability_id": capability_id,
                "error": denial.message,
                "denial": denial.to_dict(),
                "attempted_routes": [],
            }

        if route_id:
            route = get_external_capability_route(route_id)
            if route is not None and (
                route.provider_id != provider_id or route.capability_id != capability_id
            ):
                route = None
            routes = [route] if route is not None else []
        else:
            routes = resolve_external_capability_routes(
                provider_id=provider_id,
                capability_id=capability_id,
                job_kind=JobKind.OPERATE,
                export_mode="internal",
            )
        if not routes:
            denial = make_denial(
                code="gateway_capability_not_found",
                summary="API capability route not found",
                detail=(
                    f"No external API route exists for provider '{provider_id}' "
                    f"capability '{capability_id}'."
                ),
                scope=provider_id,
                suggested_action="Choose a supported provider capability from the gateway catalog.",
                metadata={
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "route_id": route_id,
                },
            )
            self._complete_api_job(
                operation_id,
                normalized_title,
                requester=requester,
                status="blocked",
                outcome="capability_not_found",
                blocked_reason=denial.message,
            )
            return {
                "ok": False,
                "job_id": operation_id,
                "provider_id": provider_id,
                "capability_id": capability_id,
                "error": denial.message,
                "denial": denial.to_dict(),
                "attempted_routes": [],
            }

        attempted_routes: list[dict[str, Any]] = []
        for fallback_index, route in enumerate(routes):
            readiness = self._route_readiness(route, auth_token_override=auth_token)
            attempt = {
                "route_id": route.route_id,
                "provider_id": route.provider_id,
                "capability_id": route.capability_id,
                "configured": readiness["configured"],
                "target_source": readiness["target_source"],
                "auth_source": readiness["auth_source"],
                "request_mode": route.request_mode,
                "response_mode": route.response_mode,
                "missing": list(readiness["missing"]),
                "errors": list(readiness["errors"]),
            }
            if not readiness["configured"]:
                detail = (
                    f"Route '{route.route_id}' is not ready: "
                    + "; ".join(readiness["missing"] + readiness["errors"])
                )
                self._record_trace(
                    title="External API route unavailable",
                    detail=detail,
                    job_id=operation_id,
                    bundle_id="",
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

            try:
                request_spec = self._build_api_call_request(
                    route=route,
                    target_url=readiness["target_url"],
                    resource=resource,
                    method=method,
                    query_params=query_params or {},
                    json_payload=json_payload or {},
                    form_data=form_data,
                )
            except ValueError as e:
                denial = make_denial(
                    code="external_api_request_invalid",
                    summary="External API request is incomplete",
                    detail=str(e),
                    scope=provider_id,
                    suggested_action="Provide the required capability resource or request payload.",
                    metadata={
                        "provider_id": provider_id,
                        "capability_id": capability_id,
                        "route_id": route.route_id,
                    },
                )
                self._complete_api_job(
                    operation_id,
                    normalized_title,
                    requester=requester,
                    status="blocked",
                    outcome="invalid_request",
                    blocked_reason=denial.message,
                )
                return {
                    "ok": False,
                    "job_id": operation_id,
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "route_id": route.route_id,
                    "error": denial.message,
                    "denial": denial.to_dict(),
                    "attempted_routes": attempted_routes,
                }
            policy_id = gateway_policy_id or route.gateway_policy_id
            policy = get_external_gateway_policy(policy_id)
            allowed, reason, resolved_policy = evaluate_external_gateway_access(
                policy_id=policy_id,
                target_kind=route.target_kind,
                target_url=request_spec["target_url"],
                approval_status="approved" if not policy.require_approval else "",
                auth_token_provided=bool(readiness["auth_token"]),
            )
            if route.target_kind not in get_external_gateway_contract(
                route.gateway_contract_id
            ).supported_target_kinds:
                allowed = False
                reason = (
                    f"Gateway contract '{route.gateway_contract_id}' does not support "
                    f"target kind '{route.target_kind}'."
                )
            if not allowed:
                denial = make_denial(
                    code="gateway_delivery_blocked",
                    summary="External API call blocked",
                    detail=reason,
                    scope=provider_id,
                    policy_id=resolved_policy.id,
                    environment_profile_id=resolved_policy.environment_profile_id,
                    suggested_action=(
                        "Adjust gateway policy/auth settings or choose a supported provider route."
                    ),
                    metadata={
                        "provider_id": provider_id,
                        "capability_id": capability_id,
                        "route_id": route.route_id,
                        "target_url": request_spec["target_url"],
                    },
                )
                attempted_routes.append({**attempt, "status": "blocked"})
                self._complete_api_job(
                    operation_id,
                    normalized_title,
                    requester=requester,
                    status="blocked",
                    outcome="policy_blocked",
                    blocked_reason=denial.message,
                )
                return {
                    "ok": False,
                    "job_id": operation_id,
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "route_id": route.route_id,
                    "error": denial.message,
                    "denial": denial.to_dict(),
                    "attempted_routes": attempted_routes,
                }

            allowed_rate, rate_reason = self._allow_rate_limited_call(
                policy=resolved_policy,
                target_url=request_spec["target_url"],
            )
            if not allowed_rate:
                denial = make_denial(
                    code="gateway_rate_limited",
                    summary="External API call blocked by rate limit",
                    detail=rate_reason,
                    scope=provider_id,
                    policy_id=resolved_policy.id,
                    environment_profile_id=resolved_policy.environment_profile_id,
                    suggested_action="Wait for the rate-limit window to clear and retry.",
                    metadata={
                        "provider_id": provider_id,
                        "capability_id": capability_id,
                        "route_id": route.route_id,
                        "target_url": request_spec["target_url"],
                    },
                )
                attempted_routes.append({**attempt, "status": "blocked"})
                self._complete_api_job(
                    operation_id,
                    normalized_title,
                    requester=requester,
                    status="blocked",
                    outcome="rate_limited",
                    blocked_reason=denial.message,
                )
                return {
                    "ok": False,
                    "job_id": operation_id,
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "route_id": route.route_id,
                    "error": denial.message,
                    "denial": denial.to_dict(),
                    "attempted_routes": attempted_routes,
                }

            self._record_trace(
                title="External API call requested",
                detail=(
                    f"provider={provider_id}; capability={capability_id}; "
                    f"method={request_spec['method']}; target={self._display_target(request_spec['target_url'])}"
                ),
                job_id=operation_id,
                bundle_id="",
                metadata={
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "route_id": route.route_id,
                    "method": request_spec["method"],
                    "target_url": request_spec["target_url"],
                    "query_params": dict(request_spec["query_params"]),
                    "request_mode": route.request_mode,
                    "response_mode": route.response_mode,
                },
            )
            result = await self._execute_http_request_with_retry(
                target_url=request_spec["target_url"],
                method=request_spec["method"],
                query_params=request_spec["query_params"],
                json_payload=request_spec["json_payload"],
                auth_header_name=resolved_policy.auth_header_name,
                auth_token=readiness["auth_token"],
                timeout_seconds=resolved_policy.timeout_seconds,
                max_retries=resolved_policy.max_retries,
                retry_backoff_seconds=resolved_policy.retry_backoff_seconds,
                form_data=request_spec.get("form_data"),
            )

            base_result = {
                "job_id": operation_id,
                "provider_id": route.provider_id,
                "capability_id": route.capability_id,
                "route_id": route.route_id,
                "target_url": request_spec["target_url"],
                "method": request_spec["method"],
            }
            if result["ok"]:
                artifacts = self._record_api_call_artifacts(
                    job_id=operation_id,
                    provider_id=route.provider_id,
                    capability_id=route.capability_id,
                    request_spec=request_spec,
                    result=result,
                    requester=requester,
                )
                normalized_response = self._normalize_api_response(
                    route=route,
                    target_url=request_spec["target_url"],
                    result=result,
                )
                self._record_trace(
                    title="External API call succeeded",
                    detail=(
                        f"provider={provider_id}; capability={capability_id}; "
                        f"status={result['status_code']}; attempts={result['attempts']}"
                    ),
                    job_id=operation_id,
                    bundle_id="",
                    metadata={
                        "provider_id": provider_id,
                        "capability_id": capability_id,
                        "route_id": route.route_id,
                        "status_code": result["status_code"],
                        "attempts": result["attempts"],
                        "normalized_response": normalized_response,
                    },
                )
                if resolved_policy.record_cost:
                    self._record_api_call_cost(
                        operation_id=operation_id,
                        provider_id=route.provider_id,
                        capability_id=route.capability_id,
                        target_url=request_spec["target_url"],
                        attempts=result["attempts"],
                        estimated_cost_usd=route.estimated_cost_usd,
                    )
                self._complete_api_job(
                    operation_id,
                    normalized_title,
                    requester=requester,
                    status="completed",
                    outcome="success",
                    artifact_ids=artifacts,
                )
                attempted_routes.append(
                    {
                        **attempt,
                        "status": "sent",
                        "target_url": request_spec["target_url"],
                        "attempts": result["attempts"],
                    }
                )
                return {
                    **base_result,
                    "ok": True,
                    "status_code": result["status_code"],
                    "attempts": result["attempts"],
                    "response_json": result["response_json"],
                    "response_text": result["response_text"],
                    "response_headers": result["response_headers"],
                    "normalized_response": normalized_response,
                    "artifact_record_ids": artifacts,
                    "attempted_routes": attempted_routes,
                }

            attempted_routes.append(
                {
                    **attempt,
                    "status": "failed",
                    "target_url": request_spec["target_url"],
                    "attempts": result["attempts"],
                    "error": result["error"],
                    "status_code": result["status_code"],
                }
            )
            denial = self._api_call_denial(
                provider_id=provider_id,
                capability_id=capability_id,
                route_id=route.route_id,
                request_spec=request_spec,
                result=result,
                policy=resolved_policy,
            )
            self._record_trace(
                title="External API call failed",
                detail=denial.message,
                job_id=operation_id,
                bundle_id="",
                metadata={
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "route_id": route.route_id,
                    "status_code": result["status_code"],
                    "attempts": result["attempts"],
                    "errors": list(result["errors"]),
                },
            )
            if not self._should_try_next_route(result):
                self._complete_api_job(
                    operation_id,
                    normalized_title,
                    requester=requester,
                    status="failed",
                    outcome="request_failed",
                    blocked_reason=denial.message,
                )
                return {
                    **base_result,
                    "ok": False,
                    "status_code": result["status_code"],
                    "attempts": result["attempts"],
                    "error": denial.message,
                    "denial": denial.to_dict(),
                    "response_json": result["response_json"],
                    "response_text": result["response_text"],
                    "response_headers": result["response_headers"],
                    "attempted_routes": attempted_routes,
                }

        denial_code = (
            "gateway_provider_not_configured"
            if attempted_routes and all(item["status"] == "unavailable" for item in attempted_routes)
            else "external_api_call_failed"
        )
        denial = make_denial(
            code=denial_code,
            summary=(
                "External API capability is not configured"
                if denial_code == "gateway_provider_not_configured"
                else "External API call failed"
            ),
            detail=(
                f"All configured routes were unavailable for provider '{provider_id}' "
                f"capability '{capability_id}'."
                if denial_code == "gateway_provider_not_configured"
                else (
                    f"All configured routes failed for provider '{provider_id}' "
                    f"capability '{capability_id}'."
                )
            ),
            scope=provider_id,
            suggested_action="Configure a ready provider route or choose a supported capability.",
            metadata={
                "provider_id": provider_id,
                "capability_id": capability_id,
                "attempted_routes": attempted_routes,
            },
        )
        self._complete_api_job(
            operation_id,
            normalized_title,
            requester=requester,
            status="blocked",
            outcome="provider_unconfigured",
            blocked_reason=denial.message,
        )
        return {
            "ok": False,
            "job_id": operation_id,
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
        if getattr(route, "default_target_url", ""):
            return str(route.default_target_url), "default"
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

    def _extract_x402_payment_metadata(
        self,
        *,
        response_headers: dict[str, str],
        response_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract structured payment metadata from HTTP 402 responses.

        Parses standard and provider-specific payment headers and body fields.
        Supports the x402 protocol pattern where payment details are conveyed
        through response headers and/or JSON body.
        """
        payment: dict[str, Any] = {}

        # Standard headers
        if response_headers.get("Retry-After"):
            payment["retry_after"] = response_headers["Retry-After"]

        # x402 / payment-related headers (case-insensitive search)
        header_lower = {k.lower(): v for k, v in response_headers.items()}
        for key_prefix in ("x-payment", "x-credits", "x-price", "x-cost"):
            for header_key, header_value in header_lower.items():
                if header_key.startswith(key_prefix):
                    payment.setdefault("headers", {})[header_key] = header_value

        # Body fields
        for body_key in (
            "credits_required", "price", "cost", "amount",
            "payment_url", "payment_address", "invoice",
            "balance", "credits", "minimum_credits",
        ):
            if body_key in response_json:
                payment.setdefault("body", {})[body_key] = response_json[body_key]

        # Summary
        if response_json.get("message"):
            payment["message"] = str(response_json["message"])[:200]
        if response_json.get("error"):
            payment["error"] = str(response_json["error"])[:200]

        return payment

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

    def _build_api_call_request(
        self,
        *,
        route: Any,
        target_url: str,
        resource: str,
        method: str,
        query_params: dict[str, Any],
        json_payload: dict[str, Any],
        form_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_method = str(method or "").upper()
        if route.request_mode == "obolos_marketplace_catalog_v1":
            return {
                "method": "GET",
                "target_url": target_url,
                "query_params": dict(query_params),
                "json_payload": {},
            }
        if route.request_mode == "obolos_wallet_balance_v1":
            return {
                "method": "GET",
                "target_url": target_url,
                "query_params": {},
                "json_payload": {},
            }
        if route.request_mode == "obolos_marketplace_api_call_v1":
            slug = str(resource or "").strip().strip("/")
            if not slug:
                raise ValueError("Provider capability requires a marketplace API slug.")
            return {
                "method": normalized_method or ("POST" if json_payload else "GET"),
                "target_url": f"{target_url.rstrip('/')}/{slug}",
                "query_params": dict(query_params),
                "json_payload": dict(json_payload),
            }
        if route.request_mode == "obolos_seller_publish_v1":
            return {
                "method": "POST",
                "target_url": f"{target_url.rstrip('/')}/seller/apis",
                "query_params": {},
                "json_payload": dict(json_payload),
            }
        if route.request_mode == "obolos_wallet_topup_v1":
            return {
                "method": "POST",
                "target_url": f"{target_url.rstrip('/')}/wallet/topup",
                "query_params": {},
                "json_payload": dict(json_payload),
            }
        if route.request_mode == "obolos_marketplace_upload_v1":
            slug = str(resource or "").strip().strip("/")
            if not slug:
                raise ValueError("File upload requires a marketplace API slug.")
            spec: dict[str, Any] = {
                "method": "POST",
                "target_url": f"{target_url.rstrip('/')}/{slug}",
                "query_params": dict(query_params),
                "json_payload": {},
            }
            if form_data:
                spec["form_data"] = dict(form_data)
            return spec
        # Default: pass through form_data if provided
        spec = {
            "method": normalized_method or ("POST" if json_payload or form_data else "GET"),
            "target_url": target_url,
            "query_params": dict(query_params),
            "json_payload": dict(json_payload),
        }
        if form_data:
            spec["form_data"] = dict(form_data)
        return spec

    def _normalize_api_response(
        self,
        *,
        route: Any,
        target_url: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(result.get("response_json", {}))
        if route.response_mode == "obolos_marketplace_catalog_v1":
            apis = payload.get("apis", [])
            return {
                "kind": "marketplace_catalog",
                "provider_status": "ok",
                "api_count": len(apis) if isinstance(apis, list) else 0,
                "slugs": [
                    str(item.get("slug", ""))
                    for item in apis
                    if isinstance(item, dict) and item.get("slug")
                ][:20],
            }
        if route.response_mode == "obolos_wallet_balance_v1":
            return {
                "kind": "wallet_balance",
                "provider_status": "ok",
                "address": str(payload.get("address", "")),
                "credits": payload.get("credits", 0),
                "credit_value": str(payload.get("creditValue", "")),
            }
        if route.response_mode == "obolos_marketplace_api_call_v1":
            return {
                "kind": "marketplace_api_call",
                "provider_status": "ok",
                "target_url": target_url,
                "top_level_keys": sorted(payload.keys())[:25],
            }
        if route.response_mode == "obolos_seller_publish_v1":
            return {
                "kind": "seller_publish",
                "provider_status": "ok",
                "slug": str(payload.get("slug", "")),
                "api_id": str(payload.get("id", payload.get("api_id", ""))),
                "status": str(payload.get("status", "")),
            }
        if route.response_mode == "obolos_wallet_topup_v1":
            return {
                "kind": "wallet_topup",
                "provider_status": "ok",
                "new_balance": payload.get("credits", payload.get("new_balance", 0)),
                "transaction_id": str(payload.get("transaction_id", payload.get("id", ""))),
                "amount_added": payload.get("amount", payload.get("amount_added", 0)),
            }
        if route.response_mode == "obolos_marketplace_upload_v1":
            return {
                "kind": "marketplace_upload",
                "provider_status": "ok",
                "target_url": target_url,
                "result_id": str(payload.get("id", payload.get("result_id", ""))),
                "status": str(payload.get("status", "")),
                "top_level_keys": sorted(payload.keys())[:25],
            }
        return {
            "kind": "external_api_call",
            "provider_status": "ok",
            "target_url": target_url,
        }

    def _api_call_denial(
        self,
        *,
        provider_id: str,
        capability_id: str,
        route_id: str,
        request_spec: dict[str, Any],
        result: dict[str, Any],
        policy: Any,
    ) -> Any:
        status_code = int(result.get("status_code", 0) or 0)
        response_headers = {
            str(k): str(v) for k, v in (result.get("response_headers", {}) or {}).items()
        }
        if status_code == 402:
            # Extract x402 payment metadata from response headers and body
            payment_metadata = self._extract_x402_payment_metadata(
                response_headers=response_headers,
                response_json=dict(result.get("response_json", {})),
            )
            return make_denial(
                code="external_api_payment_required",
                summary="External API call requires payment or credits",
                detail=(
                    f"Provider '{provider_id}' returned HTTP 402 for capability "
                    f"'{capability_id}'."
                ),
                scope=provider_id,
                policy_id=policy.id,
                environment_profile_id=policy.environment_profile_id,
                suggested_action=(
                    "Fund the provider wallet or use a paid request flow before retrying."
                ),
                metadata={
                    "provider_id": provider_id,
                    "capability_id": capability_id,
                    "route_id": route_id,
                    "target_url": request_spec["target_url"],
                    "response_headers": response_headers,
                    "payment": payment_metadata,
                },
            )
        return make_denial(
            code="external_api_call_failed",
            summary="External API call failed",
            detail=(
                result.get("error")
                or f"Provider '{provider_id}' returned HTTP {status_code or '0'}."
            ),
            scope=provider_id,
            policy_id=policy.id,
            environment_profile_id=policy.environment_profile_id,
            suggested_action="Review the provider response, auth, and route configuration, then retry.",
            metadata={
                "provider_id": provider_id,
                "capability_id": capability_id,
                "route_id": route_id,
                "target_url": request_spec["target_url"],
                "status_code": status_code,
            },
        )

    async def _execute_http_request_with_retry(
        self,
        *,
        target_url: str,
        method: str,
        query_params: dict[str, Any],
        json_payload: dict[str, Any],
        auth_header_name: str,
        auth_token: str,
        timeout_seconds: int,
        max_retries: int,
        retry_backoff_seconds: float,
        form_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        errors: list[str] = []
        last_status_code = 0
        last_json: dict[str, Any] = {}
        last_text = ""
        last_headers: dict[str, str] = {}
        attempts = 0
        for attempt in range(1, max_retries + 2):
            attempts = attempt
            try:
                result = await self._request_http(
                    target_url=target_url,
                    method=method,
                    query_params=query_params,
                    json_payload=json_payload,
                    auth_header_name=auth_header_name,
                    auth_token=auth_token,
                    timeout_seconds=timeout_seconds,
                    form_data=form_data,
                )
                last_status_code = int(result["status_code"])
                last_json = dict(result.get("response_json", {}))
                last_text = str(result.get("response_text", ""))
                last_headers = {
                    str(k): str(v)
                    for k, v in dict(result.get("response_headers", {})).items()
                }
                if 200 <= last_status_code < 300:
                    return {
                        "ok": True,
                        "attempts": attempt,
                        "status_code": last_status_code,
                        "response_json": last_json,
                        "response_text": last_text,
                        "response_headers": last_headers,
                        "errors": errors,
                    }
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
            "response_json": last_json,
            "response_text": last_text,
            "response_headers": last_headers,
            "error": errors[-1] if errors else "Gateway request failed",
            "errors": errors,
        }

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
                    "response_headers": dict(response.headers),
                }

    async def _request_http(
        self,
        *,
        target_url: str,
        method: str,
        query_params: dict[str, Any],
        json_payload: dict[str, Any],
        auth_header_name: str,
        auth_token: str,
        timeout_seconds: int,
        form_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._request_executor is not None:
            result = await self._request_executor(
                target_url=target_url,
                method=method,
                query_params=query_params,
                json_payload=json_payload,
                auth_header_name=auth_header_name,
                auth_token=auth_token,
                timeout_seconds=timeout_seconds,
                form_data=form_data,
            )
            return {
                "status_code": int(result.get("status_code", 0)),
                "response_json": dict(result.get("response_json", {})),
                "response_text": str(result.get("response_text", "")),
                "response_headers": {
                    str(k): str(v)
                    for k, v in dict(result.get("response_headers", {})).items()
                },
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

        # Build request kwargs: form_data uses multipart, otherwise JSON
        request_kwargs: dict[str, Any] = {
            "params": query_params or None,
            "headers": headers,
        }
        if form_data and method.upper() != "GET":
            # Multipart form data: aiohttp handles Content-Type + boundary
            data = aiohttp.FormData()
            for key, value in form_data.items():
                if isinstance(value, tuple) and len(value) == 2:
                    # File field: (filename, content_bytes)
                    filename, content = value
                    data.add_field(key, content, filename=filename)
                elif isinstance(value, bytes):
                    data.add_field(key, value)
                else:
                    data.add_field(key, str(value))
            request_kwargs["data"] = data
        elif method.upper() != "GET":
            request_kwargs["json"] = json_payload or None

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method.upper(),
                target_url,
                **request_kwargs,
            ) as response:
                text = await response.text()
                body: dict[str, Any] = {}
                if text:
                    try:
                        candidate = await response.json(content_type=None)
                        if isinstance(candidate, dict):
                            body = candidate
                    except Exception:
                        body = {}
                return {
                    "status_code": response.status,
                    "response_json": body,
                    "response_text": text[:4000],
                    "response_headers": dict(response.headers),
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
        provider_outcome: dict[str, Any],
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
                "provider_outcome": dict(provider_outcome),
            },
        )

    def _record_api_call_cost(
        self,
        *,
        operation_id: str,
        provider_id: str,
        capability_id: str,
        target_url: str,
        attempts: int,
        estimated_cost_usd: float,
    ) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_cost_entry(
            entry_id=f"{operation_id}-cost",
            job_id=operation_id,
            job_kind=JobKind.OPERATE,
            title=f"External API call for {provider_id}/{capability_id}",
            workspace_id="",
            usage=UsageSummary(
                total_tokens=0,
                total_cost_usd=estimated_cost_usd,
                model_used="",
                llm_calls=0,
            ),
            source_type="external_api_call",
            metadata={
                "provider_id": provider_id,
                "capability_id": capability_id,
                "target_url": target_url,
                "attempts": attempts,
            },
        )

    def _record_api_call_artifacts(
        self,
        *,
        job_id: str,
        provider_id: str,
        capability_id: str,
        request_spec: dict[str, Any],
        result: dict[str, Any],
        requester: str,
    ) -> list[str]:
        if self._control_plane_state is None:
            return []
        request_record = self._control_plane_state.record_retained_artifact(
            record_id=f"{job_id}-request",
            job_id=job_id,
            job_kind=JobKind.OPERATE,
            artifact_kind=ArtifactKind.EXTERNAL_API_REQUEST,
            source_type="external_api_call",
            title=f"{provider_id} {capability_id} request",
            artifact_format="json",
            content_json={
                "method": request_spec["method"],
                "target_url": request_spec["target_url"],
                "query_params": dict(request_spec["query_params"]),
                "json_payload": dict(request_spec["json_payload"]),
                "requester": requester,
            },
            metadata={
                "provider_id": provider_id,
                "capability_id": capability_id,
            },
        )
        response_kind = (
            ArtifactKind.EXTERNAL_API_CATALOG
            if capability_id == "marketplace_catalog_v1"
            else ArtifactKind.EXTERNAL_API_RESPONSE
        )
        response_record = self._control_plane_state.record_retained_artifact(
            record_id=f"{job_id}-response",
            job_id=job_id,
            job_kind=JobKind.OPERATE,
            artifact_kind=response_kind,
            source_type="external_api_call",
            title=f"{provider_id} {capability_id} response",
            artifact_format="json",
            content=str(result.get("response_text", "")),
            content_json={
                "status_code": result.get("status_code", 0),
                "response_json": dict(result.get("response_json", {})),
                "response_headers": dict(result.get("response_headers", {})),
            },
            metadata={
                "provider_id": provider_id,
                "capability_id": capability_id,
            },
        )
        return [request_record.record_id, response_record.record_id]

    def _complete_api_job(
        self,
        job_id: str,
        title: str,
        *,
        requester: str,
        status: str,
        outcome: str,
        blocked_reason: str = "",
        artifact_ids: list[str] | None = None,
    ) -> None:
        if self._control_plane_state is None:
            return
        self._control_plane_state.record_product_job(
            job_id=job_id,
            job_kind=JobKind.OPERATE,
            title=title,
            status=status,
            subkind="external_api_call",
            requester=requester,
            source="gateway_api",
            execution_mode="network_only",
            scope="",
            outcome=outcome,
            blocked_reason=blocked_reason,
            artifact_ids=list(artifact_ids or []),
        )

    def _display_target(self, target_url: str) -> str:
        parsed = urlparse(target_url)
        if parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return target_url
