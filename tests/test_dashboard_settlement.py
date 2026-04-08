"""
Tests for Operator Dashboard and Payment Settlement Service.

Validates:
1. Dashboard HTML renders correctly
2. Settlement service parses 402 denials
3. Settlement workflow: parse → check → create → approve → execute
4. Settlement edge cases and denial handling
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ─────────────────────────────────────────────
# Dashboard tests
# ─────────────────────────────────────────────

class TestDashboardRendering:

    def test_renders_html(self):
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html()
        assert "<!DOCTYPE html>" in html
        assert "Operator Dashboard" in html
        assert "/api/operator/" in html

    def test_contains_api_calls(self):
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html()
        # The api() helper function prefixes /api/operator/ to these paths
        assert "/api/operator/" in html
        assert "api('report')" in html
        assert "api('jobs" in html
        assert "api('telemetry')" in html
        assert "api('retention')" in html
        assert "api('margin')" in html
        assert "api('llm')" in html

    def test_contains_auth_form(self):
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html()
        assert 'id="apikey"' in html
        assert "authenticate()" in html

    def test_contains_metrics_section(self):
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html()
        assert 'id="metrics"' in html
        assert 'id="jobs-table"' in html
        assert 'id="llm-card"' in html

    def test_contains_llm_runtime_controls(self):
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html()
        assert "updateLlmRuntime" in html
        assert "Attach CLI" in html
        assert "Follow .env" in html

    def test_dashboard_version_exists(self):
        from agent.social.dashboard import _DASHBOARD_VERSION
        assert _DASHBOARD_VERSION

    def test_dashboard_has_html_escape_helper(self):
        """The rendered JS must define an esc() helper that escapes HTML
        before interpolating untrusted values into innerHTML."""
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html()
        assert "function esc(" in html
        # Helper must use a text-node detour, not a naive regex.
        assert "document.createTextNode" in html

    def test_dashboard_llm_panel_escapes_user_fields(self):
        """The LLM runtime panel must wrap data.note, data.updated_by and
        warnings in esc() before pushing them into innerHTML, otherwise an
        operator can store JS in the note field and trigger stored XSS."""
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html()
        # Locate the renderLlm() function block.
        start = html.index("function renderLlm(")
        end = html.index("function renderRetention(")
        block = html[start:end]
        # Free-text user fields must be escaped.
        assert "esc(data.note)" in block
        assert "esc(data.updated_by)" in block
        assert "esc(data.effective_backend)" in block
        assert "esc(data.effective_provider)" in block
        # Warnings come from a list[str] but still go through innerHTML.
        assert "esc(warnings.join" in block
        # Sanity: there must be NO raw ${data.note} or ${data.updated_by}
        # interpolation in the panel.
        assert "${data.note}" not in block
        assert "${data.updated_by}" not in block


# ─────────────────────────────────────────────
# Settlement model tests
# ─────────────────────────────────────────────

class TestSettlementModels:

    def test_payment_required_to_dict(self):
        from agent.control.settlement import PaymentRequired
        pr = PaymentRequired(
            provider_id="obolos.tech",
            capability_id="test_v1",
            amount_required=5.0,
        )
        d = pr.to_dict()
        assert d["provider_id"] == "obolos.tech"
        assert d["amount_required"] == 5.0
        assert d["request_id"]

    def test_settlement_request_to_dict(self):
        from agent.control.settlement import PaymentRequired, SettlementRequest
        sr = SettlementRequest(
            payment=PaymentRequired(amount_required=10.0),
            wallet_balance=3.0,
            sufficient_balance=False,
            topup_amount=7.0,
        )
        d = sr.to_dict()
        assert d["wallet_balance"] == 3.0
        assert d["sufficient_balance"] is False
        assert d["topup_amount"] == 7.0
        assert d["status"] == "pending"


# ─────────────────────────────────────────────
# Settlement service tests
# ─────────────────────────────────────────────

class TestPaymentSettlementService:

    def test_parse_402_denial(self):
        from agent.control.settlement import PaymentSettlementService
        svc = PaymentSettlementService()
        denial = {
            "code": "external_api_payment_required",
            "metadata": {
                "provider_id": "obolos.tech",
                "capability_id": "catalog_v1",
                "target_url": "https://api.obolos.tech/catalog",
                "payment_metadata": {
                    "credits_required": "50",
                    "payment_url": "https://pay.obolos.tech/topup",
                    "retry_after": "30",
                },
            },
        }
        payment = svc.parse_402_denial(denial)
        assert payment is not None
        assert payment.provider_id == "obolos.tech"
        assert payment.amount_required == 50.0
        assert payment.payment_url == "https://pay.obolos.tech/topup"
        assert payment.retry_after_seconds == 30

    def test_parse_non_402_returns_none(self):
        from agent.control.settlement import PaymentSettlementService
        svc = PaymentSettlementService()
        assert svc.parse_402_denial({"code": "gateway_delivery_blocked"}) is None

    def test_parse_402_with_price_field(self):
        from agent.control.settlement import PaymentSettlementService
        svc = PaymentSettlementService()
        denial = {
            "code": "external_api_payment_required",
            "metadata": {
                "payment_metadata": {"price": "9.99"},
            },
        }
        payment = svc.parse_402_denial(denial)
        assert payment is not None
        assert payment.amount_required == 9.99

    def test_create_settlement_sufficient_balance(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        svc = PaymentSettlementService()
        payment = PaymentRequired(amount_required=10.0, provider_id="test")
        sr = svc.create_settlement_request(payment, wallet_balance=20.0)
        assert sr.sufficient_balance is True
        assert sr.topup_amount == 0.0
        assert sr.status == "pending"

    def test_create_settlement_insufficient_balance(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        svc = PaymentSettlementService()
        payment = PaymentRequired(amount_required=100.0, provider_id="test")
        sr = svc.create_settlement_request(payment, wallet_balance=30.0)
        assert sr.sufficient_balance is False
        assert sr.topup_amount == 70.0
        assert sr.status == "pending"

    def test_approve_settlement(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        svc = PaymentSettlementService()
        payment = PaymentRequired(amount_required=5.0)
        sr = svc.create_settlement_request(payment)
        result = svc.approve_settlement(sr.settlement_id, note="OK to topup")
        assert result is not None
        assert result.status == "approved"
        assert result.operator_note == "OK to topup"
        assert result.resolved_at

    def test_deny_settlement(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        svc = PaymentSettlementService()
        payment = PaymentRequired(amount_required=5.0)
        sr = svc.create_settlement_request(payment)
        result = svc.deny_settlement(sr.settlement_id, note="Too expensive")
        assert result is not None
        assert result.status == "denied"

    def test_approve_nonexistent_returns_none(self):
        from agent.control.settlement import PaymentSettlementService
        svc = PaymentSettlementService()
        assert svc.approve_settlement("nonexistent") is None

    def test_double_approve_returns_none(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        svc = PaymentSettlementService()
        payment = PaymentRequired(amount_required=5.0)
        sr = svc.create_settlement_request(payment)
        svc.approve_settlement(sr.settlement_id)
        # Second approve should fail (status no longer "pending")
        assert svc.approve_settlement(sr.settlement_id) is None

    def test_get_pending_settlements(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        svc = PaymentSettlementService()
        p1 = PaymentRequired(amount_required=5.0)
        p2 = PaymentRequired(amount_required=10.0)
        svc.create_settlement_request(p1)
        sr2 = svc.create_settlement_request(p2)
        svc.deny_settlement(sr2.settlement_id)
        pending = svc.get_pending_settlements()
        assert len(pending) == 1
        assert pending[0].payment.amount_required == 5.0

    @pytest.mark.asyncio
    async def test_check_wallet_no_gateway(self):
        from agent.control.settlement import PaymentSettlementService
        svc = PaymentSettlementService(gateway=None)
        result = await svc.check_wallet_balance()
        assert result["ok"] is False
        assert "No gateway" in result["error"]

    @pytest.mark.asyncio
    async def test_check_wallet_success(self):
        from agent.control.settlement import PaymentSettlementService
        gateway = MagicMock()
        gateway.call_api_via_capability = AsyncMock(return_value={
            "ok": True,
            "normalized_response": {"credits": 42.5, "currency": "credits"},
        })
        svc = PaymentSettlementService(gateway=gateway)
        result = await svc.check_wallet_balance()
        assert result["ok"] is True
        assert result["balance"] == 42.5
        assert result["currency"] == "credits"
        gateway.call_api_via_capability.assert_awaited_once_with(
            provider_id="obolos.tech",
            capability_id="wallet_balance_v1",
            requester="settlement_service",
        )

    @pytest.mark.asyncio
    async def test_execute_topup_requires_approval(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        gateway = MagicMock()
        svc = PaymentSettlementService(gateway=gateway)
        payment = PaymentRequired(amount_required=10.0)
        sr = svc.create_settlement_request(payment)
        # Try to execute without approval
        result = await svc.execute_topup(sr.settlement_id)
        assert result["ok"] is False
        assert "not 'approved'" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_topup_after_approval(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        gateway = MagicMock()
        gateway.call_api_via_capability = AsyncMock(return_value={
            "ok": True,
            "normalized_response": {"new_balance": 50.0},
        })
        svc = PaymentSettlementService(gateway=gateway)
        payment = PaymentRequired(amount_required=10.0)
        sr = svc.create_settlement_request(payment, wallet_balance=0.0)
        svc.approve_settlement(sr.settlement_id)
        result = await svc.execute_topup(sr.settlement_id)
        assert result["ok"] is True
        assert result["amount"] == 10.0
        assert sr.status == "executed"
        gateway.call_api_via_capability.assert_awaited_once_with(
            provider_id="obolos.tech",
            capability_id="wallet_topup_v1",
            json_payload={"amount": 10.0},
            requester="settlement_service",
        )

    @pytest.mark.asyncio
    async def test_execute_topup_failure(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        gateway = MagicMock()
        gateway.call_api_via_capability = AsyncMock(return_value={
            "ok": False,
            "error": "Provider unavailable",
        })
        svc = PaymentSettlementService(gateway=gateway)
        payment = PaymentRequired(amount_required=10.0)
        sr = svc.create_settlement_request(payment)
        svc.approve_settlement(sr.settlement_id)
        result = await svc.execute_topup(sr.settlement_id)
        assert result["ok"] is False
        assert sr.status == "failed"


# ─────────────────────────────────────────────
# v1.29.0 — Persistence and workflow tests
# ─────────────────────────────────────────────

class TestSettlementPersistence:

    def test_from_dict_roundtrip(self):
        from agent.control.settlement import PaymentRequired, SettlementRequest
        orig = SettlementRequest(
            payment=PaymentRequired(provider_id="obolos", amount_required=25.0),
            wallet_balance=10.0,
            topup_amount=15.0,
            original_request={"provider_id": "obolos", "capability_id": "test"},
        )
        data = orig.to_dict()
        restored = SettlementRequest.from_dict(data)
        assert restored.settlement_id == orig.settlement_id
        assert restored.payment.provider_id == "obolos"
        assert restored.payment.amount_required == 25.0
        assert restored.topup_amount == 15.0
        assert restored.original_request["capability_id"] == "test"

    def test_list_settlements_by_status(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        svc = PaymentSettlementService()
        p1 = PaymentRequired(amount_required=5.0)
        p2 = PaymentRequired(amount_required=10.0)
        svc.create_settlement_request(p1)
        sr2 = svc.create_settlement_request(p2)
        svc.deny_settlement(sr2.settlement_id)
        assert len(svc.list_settlements()) == 2
        assert len(svc.list_settlements(status="pending")) == 1
        assert len(svc.list_settlements(status="denied")) == 1

    def test_original_request_stored(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        svc = PaymentSettlementService()
        payment = PaymentRequired(amount_required=5.0)
        sr = svc.create_settlement_request(
            payment,
            original_request={"provider_id": "obolos", "capability_id": "catalog_v1"},
        )
        assert sr.original_request["capability_id"] == "catalog_v1"

    @pytest.mark.asyncio
    async def test_execute_topup_includes_retry(self):
        from agent.control.settlement import PaymentRequired, PaymentSettlementService
        gateway = MagicMock()
        # First call: topup succeeds. Second call: retry succeeds.
        gateway.call_api_via_capability = AsyncMock(side_effect=[
            {"ok": True, "normalized_response": {"new_balance": 50.0}},
            {"ok": True, "response_json": {"data": "catalog_result"}},
        ])
        svc = PaymentSettlementService(gateway=gateway)
        payment = PaymentRequired(amount_required=10.0, provider_id="obolos")
        sr = svc.create_settlement_request(
            payment,
            original_request={
                "provider_id": "obolos",
                "capability_id": "catalog_v1",
                "resource": "catalog",
                "method": "POST",
                "query_params": {"page": 1},
                "json_payload": {"mode": "fast"},
            },
        )
        svc.approve_settlement(sr.settlement_id)
        result = await svc.execute_topup(sr.settlement_id)
        assert result["ok"] is True
        assert result["retry"]["retried"] is True
        assert result["retry"]["ok"] is True
        topup_call = gateway.call_api_via_capability.await_args_list[0].kwargs
        retry_call = gateway.call_api_via_capability.await_args_list[1].kwargs
        assert topup_call["json_payload"] == {"amount": 10.0}
        assert retry_call["resource"] == "catalog"
        assert retry_call["method"] == "POST"
        assert retry_call["query_params"] == {"page": 1}
        assert retry_call["json_payload"] == {"mode": "fast"}

    def test_dashboard_contains_settlements_section(self):
        from agent.social.dashboard import render_dashboard_html
        html = render_dashboard_html()
        assert "settlements-card" in html
        assert "settlementAction" in html
        assert "Settlements" in html
