"""
Agent Life Space — Payment Settlement Service

Handles HTTP 402 Payment Required responses from external providers.
Workflow: detect 402 → check wallet balance → request operator approval
→ execute topup → retry original API call.

All payment actions require explicit operator approval (human-in-the-loop).
No automatic spending without approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

@dataclass
class PaymentRequired:
    """Parsed 402 Payment Required response."""

    request_id: str = field(default_factory=lambda: uuid4().hex[:12])
    provider_id: str = ""
    capability_id: str = ""
    target_url: str = ""
    amount_required: float = 0.0
    currency: str = "USD"
    payment_url: str = ""
    payment_address: str = ""
    retry_after_seconds: int = 0
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "provider_id": self.provider_id,
            "capability_id": self.capability_id,
            "target_url": self.target_url,
            "amount_required": self.amount_required,
            "currency": self.currency,
            "payment_url": self.payment_url,
            "payment_address": self.payment_address,
            "retry_after_seconds": self.retry_after_seconds,
            "created_at": self.created_at,
        }


@dataclass
class SettlementRequest:
    """Operator-facing approval request for a payment."""

    settlement_id: str = field(default_factory=lambda: uuid4().hex[:12])
    payment: PaymentRequired = field(default_factory=PaymentRequired)
    wallet_balance: float = 0.0
    wallet_currency: str = "credits"
    sufficient_balance: bool = False
    topup_amount: float = 0.0
    status: str = "pending"  # pending, approved, denied, executed, failed
    operator_note: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    resolved_at: str = ""
    # Original request context for retry after successful topup
    original_request: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "payment": self.payment.to_dict(),
            "wallet_balance": self.wallet_balance,
            "wallet_currency": self.wallet_currency,
            "sufficient_balance": self.sufficient_balance,
            "topup_amount": self.topup_amount,
            "status": self.status,
            "operator_note": self.operator_note,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "original_request": self.original_request,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SettlementRequest:
        payment_data = data.get("payment", {})
        return cls(
            settlement_id=data.get("settlement_id", ""),
            payment=PaymentRequired(
                request_id=payment_data.get("request_id", ""),
                provider_id=payment_data.get("provider_id", ""),
                capability_id=payment_data.get("capability_id", ""),
                target_url=payment_data.get("target_url", ""),
                amount_required=float(payment_data.get("amount_required", 0.0)),
                currency=payment_data.get("currency", "USD"),
                payment_url=payment_data.get("payment_url", ""),
                payment_address=payment_data.get("payment_address", ""),
                retry_after_seconds=int(payment_data.get("retry_after_seconds", 0)),
                raw_metadata=dict(payment_data.get("raw_metadata", {})),
                created_at=payment_data.get("created_at", ""),
            ),
            wallet_balance=float(data.get("wallet_balance", 0.0)),
            wallet_currency=data.get("wallet_currency", "credits"),
            sufficient_balance=bool(data.get("sufficient_balance", False)),
            topup_amount=float(data.get("topup_amount", 0.0)),
            status=data.get("status", "pending"),
            operator_note=data.get("operator_note", ""),
            created_at=data.get("created_at", ""),
            resolved_at=data.get("resolved_at", ""),
            original_request=dict(data.get("original_request", {})),
        )


# ─────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────

class PaymentSettlementService:
    """Manages 402 Payment Required handling with operator approval.

    Workflow:
    1. parse_402() — extract payment metadata from gateway denial
    2. check_wallet() — query wallet balance via gateway capability
    3. create_settlement_request() — build approval request for operator
    4. approve/deny — operator decides
    5. execute_topup() — if approved, topup wallet via gateway capability
    6. retry_original_call() — re-execute the failed API call

    Every spending action requires operator approval.
    """

    def __init__(self, gateway: Any = None, control_plane: Any = None) -> None:
        self._gateway = gateway
        self._control_plane = control_plane
        self._requests: dict[str, SettlementRequest] = {}
        self._load_from_storage()

    def _load_from_storage(self) -> None:
        """Load persisted settlement requests from SQLite on startup."""
        if not self._control_plane:
            return
        try:
            rows = self._control_plane.list_settlement_requests(limit=200)
            for data in rows:
                sr = SettlementRequest.from_dict(data)
                self._requests[sr.settlement_id] = sr
            if rows:
                logger.info("settlements_loaded", count=len(rows))
        except Exception:
            logger.exception("settlements_load_error")

    def _persist(self, request: SettlementRequest) -> None:
        """Persist a single settlement request via control plane public API."""
        if not self._control_plane:
            return
        try:
            self._control_plane.save_settlement_request(request)
        except Exception:
            logger.exception("settlement_persist_error", settlement_id=request.settlement_id)

    def parse_402_denial(self, denial: dict[str, Any]) -> PaymentRequired | None:
        """Extract PaymentRequired from a gateway denial with code 'external_api_payment_required'."""
        if denial.get("code") != "external_api_payment_required":
            return None

        meta = denial.get("metadata", {})
        payment_meta = meta.get("payment_metadata") or meta.get("payment", {})

        # Extract amount from various provider formats
        amount = 0.0
        for key in ("price", "cost", "amount", "credits_required"):
            if key in payment_meta:
                try:
                    amount = float(payment_meta[key])
                    break
                except (ValueError, TypeError):
                    continue

        return PaymentRequired(
            provider_id=meta.get("provider_id", ""),
            capability_id=meta.get("capability_id", ""),
            target_url=meta.get("target_url", ""),
            amount_required=amount,
            payment_url=str(payment_meta.get("payment_url", "")),
            payment_address=str(payment_meta.get("payment_address", "")),
            retry_after_seconds=int(payment_meta.get("retry_after", 0)),
            raw_metadata=dict(payment_meta),
        )

    async def check_wallet_balance(
        self, provider_id: str = "obolos.tech",
    ) -> dict[str, Any]:
        """Check wallet balance via gateway capability.

        Returns {"balance": float, "currency": str, "ok": bool}.
        """
        if not self._gateway:
            return {"balance": 0.0, "currency": "unknown", "ok": False, "error": "No gateway"}

        try:
            result = await self._gateway.call_api_via_capability(
                provider_id=provider_id,
                capability_id="wallet_balance_v1",
                requester="settlement_service",
            )
            if result.get("ok"):
                resp = result.get("normalized_response") or result.get("response_json", {})
                return {
                    "balance": float(resp.get("credits", resp.get("balance", 0.0))),
                    "currency": str(resp.get("currency", "credits")),
                    "ok": True,
                }
            return {
                "balance": 0.0,
                "currency": "unknown",
                "ok": False,
                "error": result.get("error", "Unknown error"),
            }
        except Exception as e:
            logger.error("settlement_wallet_check_error", error=str(e))
            return {"balance": 0.0, "currency": "unknown", "ok": False, "error": str(e)}

    def create_settlement_request(
        self,
        payment: PaymentRequired,
        wallet_balance: float = 0.0,
        wallet_currency: str = "credits",
        original_request: dict[str, Any] | None = None,
    ) -> SettlementRequest:
        """Create a settlement request for operator approval."""
        sufficient = wallet_balance >= payment.amount_required
        topup_needed = max(0.0, payment.amount_required - wallet_balance)

        request = SettlementRequest(
            payment=payment,
            wallet_balance=wallet_balance,
            wallet_currency=wallet_currency,
            sufficient_balance=sufficient,
            topup_amount=topup_needed if not sufficient else 0.0,
            original_request=dict(original_request) if original_request else {},
        )
        self._requests[request.settlement_id] = request
        self._persist(request)

        # Record trace if control plane available
        if self._control_plane:
            try:
                from agent.control.models import TraceRecordKind
                self._control_plane.record_trace(
                    trace_kind=TraceRecordKind.GATEWAY,
                    title="Payment settlement requested",
                    detail=(
                        f"provider={payment.provider_id} "
                        f"amount={payment.amount_required} "
                        f"balance={wallet_balance} "
                        f"sufficient={sufficient}"
                    ),
                    metadata=request.to_dict(),
                )
            except Exception:
                pass

        logger.info(
            "settlement_request_created",
            settlement_id=request.settlement_id,
            provider=payment.provider_id,
            amount=payment.amount_required,
            balance=wallet_balance,
            sufficient=sufficient,
        )
        return request

    def get_pending_settlements(self) -> list[SettlementRequest]:
        """Get all pending settlement requests."""
        return [s for s in self._requests.values() if s.status == "pending"]

    def get_settlement(self, settlement_id: str) -> SettlementRequest | None:
        return self._requests.get(settlement_id)

    def list_settlements(self, *, status: str = "") -> list[SettlementRequest]:
        """List all settlement requests, optionally filtered by status."""
        requests = list(self._requests.values())
        if status:
            requests = [s for s in requests if s.status == status]
        return sorted(requests, key=lambda s: s.created_at, reverse=True)

    def approve_settlement(
        self, settlement_id: str, *, note: str = "",
    ) -> SettlementRequest | None:
        """Operator approves a settlement request."""
        request = self._requests.get(settlement_id)
        if not request or request.status != "pending":
            return None
        request.status = "approved"
        request.operator_note = note
        request.resolved_at = datetime.now(UTC).isoformat()
        self._persist(request)
        logger.info("settlement_approved", settlement_id=settlement_id)
        return request

    def deny_settlement(
        self, settlement_id: str, *, note: str = "",
    ) -> SettlementRequest | None:
        """Operator denies a settlement request."""
        request = self._requests.get(settlement_id)
        if not request or request.status != "pending":
            return None
        request.status = "denied"
        request.operator_note = note
        request.resolved_at = datetime.now(UTC).isoformat()
        self._persist(request)
        logger.info("settlement_denied", settlement_id=settlement_id)
        return request

    async def execute_topup(
        self,
        settlement_id: str,
        *,
        provider_id: str = "obolos.tech",
    ) -> dict[str, Any]:
        """Execute a wallet topup for an approved settlement.

        Only works if settlement status is 'approved'. Requires gateway.
        """
        request = self._requests.get(settlement_id)
        if not request:
            return {"ok": False, "error": "Settlement not found"}
        if request.status != "approved":
            return {"ok": False, "error": f"Settlement status is '{request.status}', not 'approved'"}
        if not self._gateway:
            return {"ok": False, "error": "No gateway configured"}

        amount = request.topup_amount or request.payment.amount_required
        try:
            result = await self._gateway.call_api_via_capability(
                provider_id=provider_id,
                capability_id="wallet_topup_v1",
                json_payload={"amount": amount},
                requester="settlement_service",
            )
            if result.get("ok"):
                request.status = "executed"
                self._persist(request)
                logger.info(
                    "settlement_topup_executed",
                    settlement_id=settlement_id,
                    amount=amount,
                )
                # Attempt retry of original request if stored
                retry_result = await self._retry_original(request, provider_id)
                return {
                    "ok": True,
                    "amount": amount,
                    "result": result.get("normalized_response") or result.get("response_json", {}),
                    "retry": retry_result,
                }
            else:
                request.status = "failed"
                self._persist(request)
                error = result.get("error", "Topup failed")
                logger.error("settlement_topup_failed", settlement_id=settlement_id, error=error)
                return {"ok": False, "error": error}
        except Exception as e:
            request.status = "failed"
            self._persist(request)
            logger.error("settlement_topup_error", settlement_id=settlement_id, error=str(e))
            return {"ok": False, "error": str(e)}

    async def _retry_original(
        self,
        request: SettlementRequest,
        provider_id: str,
    ) -> dict[str, Any]:
        """Retry the original API call that triggered the 402, after successful topup."""
        orig = request.original_request
        if not orig or not self._gateway:
            return {"retried": False, "reason": "No original request context or gateway"}

        try:
            result = await self._gateway.call_api_via_capability(
                provider_id=orig.get("provider_id", provider_id),
                capability_id=orig.get("capability_id", ""),
                resource=str(orig.get("resource", "")),
                method=str(orig.get("method", "")),
                query_params=dict(orig.get("query_params", {})),
                json_payload=dict(orig.get("json_payload", {})),
                form_data=(
                    dict(orig["form_data"])
                    if isinstance(orig.get("form_data"), dict)
                    else None
                ),
                requester=f"settlement_retry:{request.settlement_id}",
            )
            logger.info(
                "settlement_retry_completed",
                settlement_id=request.settlement_id,
                ok=result.get("ok", False),
            )
            return {"retried": True, "ok": result.get("ok", False), "result": result}
        except Exception as e:
            logger.error("settlement_retry_error", settlement_id=request.settlement_id, error=str(e))
            return {"retried": True, "ok": False, "error": str(e)}
