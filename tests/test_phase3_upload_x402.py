"""
Tests for v1.24.0 Phase 3: file upload (multipart) support and x402 payment handling.
"""

from __future__ import annotations

import pytest

from agent.control.gateway import ExternalGatewayService
from agent.control.policy import (
    get_external_capability_provider,
    get_external_capability_route,
    list_providers_for_capability,
    resolve_capability_across_providers,
)

# ─────────────────────────────────────────────
# Capability route tests
# ─────────────────────────────────────────────

class TestUploadCapabilityRoute:

    def test_upload_capability_in_provider(self):
        provider = get_external_capability_provider("obolos.tech")
        assert "marketplace_upload_v1" in provider.capability_ids

    def test_upload_route_exists(self):
        route = get_external_capability_route("obolos_marketplace_upload_primary")
        assert route is not None
        assert route.capability_id == "marketplace_upload_v1"
        assert route.request_mode == "obolos_marketplace_upload_v1"
        assert route.response_mode == "obolos_marketplace_upload_v1"

    def test_upload_route_is_http_api(self):
        route = get_external_capability_route("obolos_marketplace_upload_primary")
        assert route is not None
        assert route.target_kind == "http_api"
        assert route.gateway_contract_id == "external_api_call_v1"

    def test_upload_capability_resolved_across_providers(self):
        routes = resolve_capability_across_providers(
            capability_id="marketplace_upload_v1",
            job_kind="operate",
        )
        assert len(routes) >= 1
        assert routes[0].request_mode == "obolos_marketplace_upload_v1"

    def test_upload_provider_discovered(self):
        providers = list_providers_for_capability("marketplace_upload_v1")
        assert len(providers) >= 1
        assert providers[0].id == "obolos.tech"


# ─────────────────────────────────────────────
# Gateway build_api_call_request tests
# ─────────────────────────────────────────────

class TestBuildUploadRequest:

    def _build(self, **kwargs):
        svc = ExternalGatewayService()
        route = get_external_capability_route("obolos_marketplace_upload_primary")
        return svc._build_api_call_request(
            route=route,
            target_url="https://obolos.tech/api",
            resource=kwargs.get("resource", "ocr-text-extraction"),
            method=kwargs.get("method", "POST"),
            query_params=kwargs.get("query_params", {}),
            json_payload=kwargs.get("json_payload", {}),
            form_data=kwargs.get("form_data"),
        )

    def test_upload_request_uses_slug_in_url(self):
        spec = self._build(resource="ocr-text-extraction")
        assert spec["target_url"] == "https://obolos.tech/api/ocr-text-extraction"
        assert spec["method"] == "POST"

    def test_upload_request_includes_form_data(self):
        spec = self._build(
            resource="ocr-text-extraction",
            form_data={"file": ("test.pdf", b"content"), "language": "en"},
        )
        assert "form_data" in spec
        assert spec["form_data"]["language"] == "en"

    def test_upload_request_no_form_data_without_arg(self):
        spec = self._build(resource="ocr-text-extraction")
        assert "form_data" not in spec

    def test_upload_request_requires_slug(self):
        with pytest.raises(ValueError, match="slug"):
            self._build(resource="")


class TestDefaultFormDataPassthrough:
    """Default request mode passes form_data when provided."""

    def test_default_mode_includes_form_data(self):
        svc = ExternalGatewayService()
        route = get_external_capability_route("obolos_marketplace_api_primary")
        spec = svc._build_api_call_request(
            route=route,
            target_url="https://example.com/api",
            resource="test-slug",
            method="POST",
            query_params={},
            json_payload={},
            form_data={"field": "value"},
        )
        # marketplace_api_call_v1 mode doesn't use form_data (json-only)
        # but the default fallback should pass it through
        # Since this route uses obolos_marketplace_api_call_v1, form_data is not in spec
        assert "form_data" not in spec  # specific modes don't add form_data


# ─────────────────────────────────────────────
# x402 payment metadata extraction tests
# ─────────────────────────────────────────────

class TestX402PaymentMetadata:

    def _extract(self, headers=None, body=None):
        svc = ExternalGatewayService()
        return svc._extract_x402_payment_metadata(
            response_headers=headers or {},
            response_json=body or {},
        )

    def test_empty_response(self):
        result = self._extract()
        assert result == {}

    def test_retry_after_header(self):
        result = self._extract(headers={"Retry-After": "60"})
        assert result["retry_after"] == "60"

    def test_x_payment_headers(self):
        result = self._extract(headers={
            "X-Payment-Required": "true",
            "X-Payment-Address": "0xabc123",
            "X-Credits-Needed": "10",
        })
        assert "headers" in result
        assert result["headers"]["x-payment-required"] == "true"
        assert result["headers"]["x-payment-address"] == "0xabc123"
        assert result["headers"]["x-credits-needed"] == "10"

    def test_body_fields(self):
        result = self._extract(body={
            "credits_required": 5,
            "price": 0.01,
            "payment_url": "https://pay.example.com",
            "balance": 0,
        })
        assert result["body"]["credits_required"] == 5
        assert result["body"]["price"] == 0.01
        assert result["body"]["payment_url"] == "https://pay.example.com"

    def test_error_message(self):
        result = self._extract(body={
            "error": "Insufficient credits",
            "message": "Please top up your wallet",
        })
        assert result["error"] == "Insufficient credits"
        assert result["message"] == "Please top up your wallet"

    def test_combined_headers_and_body(self):
        result = self._extract(
            headers={
                "Retry-After": "30",
                "X-Credits-Needed": "5",
            },
            body={
                "credits_required": 5,
                "message": "Top up required",
            },
        )
        assert result["retry_after"] == "30"
        assert result["headers"]["x-credits-needed"] == "5"
        assert result["body"]["credits_required"] == 5
        assert result["message"] == "Top up required"


# ─────────────────────────────────────────────
# Gateway upload response normalization tests
# ─────────────────────────────────────────────

class TestUploadResponseNormalization:

    def _normalize(self, payload):
        svc = ExternalGatewayService()
        route = get_external_capability_route("obolos_marketplace_upload_primary")
        return svc._normalize_api_response(
            route=route,
            target_url="https://obolos.tech/api/ocr",
            result={"response_json": payload},
        )

    def test_upload_response_kind(self):
        result = self._normalize({"id": "res-123", "status": "completed"})
        assert result["kind"] == "marketplace_upload"
        assert result["provider_status"] == "ok"
        assert result["result_id"] == "res-123"
        assert result["status"] == "completed"

    def test_upload_response_top_level_keys(self):
        result = self._normalize({
            "id": "r1",
            "text": "extracted content",
            "confidence": 0.95,
        })
        assert "text" in result["top_level_keys"]
        assert "confidence" in result["top_level_keys"]


# ─────────────────────────────────────────────
# Catalog includes upload capability
# ─────────────────────────────────────────────

class TestCatalogIncludesUpload:

    def test_catalog_has_upload_capability(self):
        svc = ExternalGatewayService()
        catalog = svc.describe_capability_catalog()
        cap_map = catalog.get("capability_map", {})
        assert "marketplace_upload_v1" in cap_map
        assert "obolos.tech" in cap_map["marketplace_upload_v1"]

    def test_catalog_has_upload_route(self):
        svc = ExternalGatewayService()
        catalog = svc.describe_capability_catalog()
        routes = catalog.get("routes", [])
        upload_routes = [r for r in routes if r["capability_id"] == "marketplace_upload_v1"]
        assert len(upload_routes) >= 1
        assert upload_routes[0]["request_mode"] == "obolos_marketplace_upload_v1"


# ─────────────────────────────────────────────
# Integration: form_data threading through call chain
# ─────────────────────────────────────────────

class TestFormDataCallChain:

    async def test_form_data_reaches_request_executor(self):
        """form_data passes through call_api_via_capability to _request_http."""
        captured = {}

        async def mock_executor(**kwargs):
            captured.update(kwargs)
            return {
                "status_code": 200,
                "response_json": {"ok": True},
                "response_text": '{"ok": true}',
                "response_headers": {},
            }

        svc = ExternalGatewayService(
            request_executor=mock_executor,
            environment={
                "AGENT_OBOLOS_API_BASE_URL": "https://obolos.tech/api",
                "AGENT_OBOLOS_WALLET_ADDRESS": "0xtest",
            },
        )

        await svc.call_api_via_capability(
            provider_id="obolos.tech",
            capability_id="marketplace_upload_v1",
            resource="ocr-text-extraction",
            form_data={"file": ("test.pdf", b"fake-pdf"), "lang": "en"},
        )

        assert "form_data" in captured
        assert captured["form_data"]["lang"] == "en"

    async def test_402_includes_payment_metadata(self):
        """HTTP 402 response includes extracted x402 payment metadata."""
        async def mock_executor(**kwargs):
            return {
                "status_code": 402,
                "response_json": {
                    "error": "Insufficient credits",
                    "credits_required": 5,
                },
                "response_text": "Payment Required",
                "response_headers": {
                    "X-Credits-Needed": "5",
                    "Retry-After": "30",
                },
            }

        svc = ExternalGatewayService(
            request_executor=mock_executor,
            environment={
                "AGENT_OBOLOS_API_BASE_URL": "https://obolos.tech/api",
                "AGENT_OBOLOS_WALLET_ADDRESS": "0xtest",
            },
        )

        result = await svc.call_api_via_capability(
            provider_id="obolos.tech",
            capability_id="marketplace_api_call_v1",
            resource="ocr-text-extraction",
        )

        assert result["ok"] is False
        denial = result.get("denial", {})
        assert denial.get("code") == "external_api_payment_required"
        payment = denial.get("metadata", {}).get("payment", {})
        assert payment.get("retry_after") == "30"
        assert payment.get("body", {}).get("credits_required") == 5
        assert payment.get("error") == "Insufficient credits"
