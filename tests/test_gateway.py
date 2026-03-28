from __future__ import annotations

import os
import tempfile

import pytest

from agent.control.gateway import ExternalGatewayService
from agent.control.models import JobKind
from agent.control.state import ControlPlaneStateService
from agent.control.storage import ControlPlaneStorage
from agent.core.approval import ApprovalCategory, ApprovalQueue


def _bundle(*, job_id: str = "build-1", bundle_id: str = "bundle-1") -> dict[str, object]:
    return {
        "job_id": job_id,
        "bundle_id": bundle_id,
        "package_type": "build_delivery",
        "artifact_count": 2,
        "artifact_ids": ["artifact-1", "artifact-2"],
        "workspace_id": "ws-1",
    }


@pytest.fixture()
def control_plane():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    state = ControlPlaneStateService(ControlPlaneStorage(db_path=db_path))
    state.initialize()
    try:
        yield state
    finally:
        os.unlink(db_path)


def _approved_request(queue: ApprovalQueue) -> str:
    request = queue.propose(
        ApprovalCategory.EXTERNAL,
        "Send bundle",
        reason="External handoff",
        context={"job_id": "build-1", "bundle_id": "bundle-1"},
    )
    queue.approve(request.id, decided_by="owner-1")
    return request.id


class TestExternalGatewayService:
    @pytest.mark.asyncio
    async def test_gateway_blocks_unapproved_delivery(self, control_plane):
        queue = ApprovalQueue()
        request = queue.propose(
            ApprovalCategory.EXTERNAL,
            "Send review bundle",
            reason="External handoff",
            context={"job_id": "review-1", "bundle_id": "review-bundle-1"},
        )
        service = ExternalGatewayService(
            control_plane_state=control_plane,
            approval_queue=queue,
        )

        result = await service.send_delivery(
            bundle=_bundle(job_id="review-1", bundle_id="review-bundle-1"),
            job_kind=JobKind.REVIEW,
            target_url="https://hooks.example.test/review",
            approval_request_id=request.id,
            auth_token="secret-token",
        )

        assert result["ok"] is False
        assert result["denial"]["code"] == "gateway_delivery_blocked"
        assert "approved external delivery request" in result["denial"]["detail"]
        traces = control_plane.list_traces(trace_kind="gateway", job_id="review-1", limit=10)
        assert len(traces) == 1
        assert traces[0].title == "Gateway delivery blocked"
        assert control_plane.list_cost_entries(job_id="review-1", limit=10) == []

    @pytest.mark.asyncio
    async def test_gateway_requires_auth_when_policy_demands_it(self, control_plane):
        queue = ApprovalQueue()
        request_id = _approved_request(queue)
        service = ExternalGatewayService(
            control_plane_state=control_plane,
            approval_queue=queue,
        )

        result = await service.send_delivery(
            bundle=_bundle(),
            job_kind=JobKind.BUILD,
            target_url="https://hooks.example.test/build",
            approval_request_id=request_id,
        )

        assert result["ok"] is False
        assert result["denial"]["code"] == "gateway_delivery_blocked"
        assert "authentication token" in result["denial"]["detail"]

    @pytest.mark.asyncio
    async def test_gateway_retries_then_records_success_trace_and_cost(
        self,
        control_plane,
        monkeypatch,
    ):
        queue = ApprovalQueue()
        request_id = _approved_request(queue)
        calls = {"count": 0}
        sleep_delays: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_delays.append(delay)

        async def executor(**_: object) -> dict[str, object]:
            calls["count"] += 1
            if calls["count"] == 1:
                return {"status_code": 503, "response_json": {}, "response_text": "retry"}
            return {
                "status_code": 202,
                "response_json": {"accepted": True},
                "response_text": "accepted",
            }

        monkeypatch.setattr("agent.control.gateway.asyncio.sleep", fake_sleep)
        service = ExternalGatewayService(
            control_plane_state=control_plane,
            approval_queue=queue,
            request_executor=executor,
        )

        result = await service.send_delivery(
            bundle=_bundle(),
            job_kind=JobKind.BUILD,
            target_url="https://hooks.example.test/build",
            approval_request_id=request_id,
            auth_token="secret-token",
            estimated_cost_usd=0.42,
        )

        assert result["ok"] is True
        assert result["attempts"] == 2
        assert sleep_delays == [0.5]
        traces = control_plane.list_traces(trace_kind="gateway", job_id="build-1", limit=10)
        assert {trace.title for trace in traces} >= {
            "Gateway delivery requested",
            "Gateway delivery succeeded",
        }
        entries = control_plane.list_cost_entries(job_id="build-1", limit=10)
        assert len(entries) == 1
        assert entries[0].entry_id.startswith("gateway-")
        assert entries[0].source_type == "external_gateway_call"
        assert entries[0].metadata["attempts"] == 2

    @pytest.mark.asyncio
    async def test_gateway_reports_actual_attempt_count_on_non_retryable_failure(
        self,
        control_plane,
    ):
        queue = ApprovalQueue()
        request_id = _approved_request(queue)

        async def executor(**_: object) -> dict[str, object]:
            return {"status_code": 400, "response_json": {}, "response_text": "bad request"}

        service = ExternalGatewayService(
            control_plane_state=control_plane,
            approval_queue=queue,
            request_executor=executor,
        )

        result = await service.send_delivery(
            bundle=_bundle(),
            job_kind=JobKind.BUILD,
            target_url="https://hooks.example.test/build",
            approval_request_id=request_id,
            auth_token="secret-token",
        )

        assert result["ok"] is False
        assert result["attempts"] == 1
        assert result["status_code"] == 400
        assert control_plane.list_cost_entries(job_id="build-1", limit=10) == []

    @pytest.mark.asyncio
    async def test_gateway_cost_entries_do_not_overwrite_same_job(self, control_plane):
        queue = ApprovalQueue()
        request_id = _approved_request(queue)

        async def executor(**_: object) -> dict[str, object]:
            return {"status_code": 200, "response_json": {"ok": True}, "response_text": "ok"}

        service = ExternalGatewayService(
            control_plane_state=control_plane,
            approval_queue=queue,
            request_executor=executor,
        )

        await service.send_delivery(
            bundle=_bundle(),
            job_kind=JobKind.BUILD,
            target_url="https://hooks.example.test/build",
            approval_request_id=request_id,
            auth_token="secret-token",
            estimated_cost_usd=0.11,
        )
        await service.send_delivery(
            bundle=_bundle(),
            job_kind=JobKind.BUILD,
            target_url="https://hooks-2.example.test/build",
            approval_request_id=request_id,
            auth_token="secret-token",
            estimated_cost_usd=0.12,
        )

        entries = control_plane.list_cost_entries(job_id="build-1", limit=10)
        assert len(entries) == 2
        assert len({entry.entry_id for entry in entries}) == 2
        assert {entry.metadata["target_url"] for entry in entries} == {
            "https://hooks.example.test/build",
            "https://hooks-2.example.test/build",
        }

    def test_gateway_catalog_reports_provider_route_readiness(self, control_plane):
        service = ExternalGatewayService(
            control_plane_state=control_plane,
            environment={
                "AGENT_OBOLOS_REVIEW_WEBHOOK_URL": "https://obolos.example.test/review",
            },
            secret_lookup=lambda name: "vault-token" if name == "obolos.tech.auth_token" else "",
        )

        catalog = service.describe_capability_catalog(
            provider_id="obolos.tech",
            capability_id="review_handoff_v1",
            job_kind=JobKind.REVIEW,
            export_mode="client_safe",
        )

        assert catalog["summary"]["total_routes"] == 2
        assert catalog["summary"]["configured_routes"] == 1
        primary = next(
            route
            for route in catalog["routes"]
            if route["route_id"] == "obolos_review_handoff_primary"
        )
        backup = next(
            route
            for route in catalog["routes"]
            if route["route_id"] == "obolos_review_handoff_backup"
        )
        assert primary["configured"] is True
        assert primary["target_source"] == "env"
        assert primary["auth_source"] == "vault"
        assert backup["configured"] is False
        assert "AGENT_OBOLOS_REVIEW_WEBHOOK_URL_BACKUP" in backup["missing"]

    @pytest.mark.asyncio
    async def test_gateway_provider_send_uses_configured_route_without_raw_target(
        self,
        control_plane,
    ):
        queue = ApprovalQueue()
        request_id = _approved_request(queue)

        async def executor(**_: object) -> dict[str, object]:
            return {
                "status_code": 202,
                "response_json": {"accepted": True},
                "response_text": "accepted",
            }

        service = ExternalGatewayService(
            control_plane_state=control_plane,
            approval_queue=queue,
            request_executor=executor,
            environment={
                "AGENT_OBOLOS_REVIEW_WEBHOOK_URL": "https://obolos.example.test/review",
                "AGENT_OBOLOS_AUTH_TOKEN": "env-token",
            },
        )

        result = await service.send_delivery_via_capability(
            bundle=_bundle(job_id="review-1", bundle_id="review-bundle-1"),
            job_kind=JobKind.REVIEW,
            provider_id="obolos.tech",
            capability_id="review_handoff_v1",
            approval_request_id=request_id,
            export_mode="client_safe",
        )

        assert result["ok"] is True
        assert result["provider_id"] == "obolos.tech"
        assert result["route_id"] == "obolos_review_handoff_primary"
        assert result["fallback_used"] is False
        assert result["target_url"] == "https://obolos.example.test/review"
        assert result["attempted_routes"][0]["status"] == "sent"
        traces = control_plane.list_traces(trace_kind="gateway", job_id="review-1", limit=10)
        entries = control_plane.list_cost_entries(job_id="review-1", limit=10)
        assert any(
            trace.metadata.get("provider_context", {}).get("route_id")
            == "obolos_review_handoff_primary"
            for trace in traces
        )
        assert entries[0].metadata["provider_context"]["provider_id"] == "obolos.tech"

    @pytest.mark.asyncio
    async def test_gateway_provider_send_falls_back_to_backup_route(
        self,
        control_plane,
    ):
        queue = ApprovalQueue()
        request_id = _approved_request(queue)

        async def executor(**kwargs: object) -> dict[str, object]:
            target_url = str(kwargs.get("target_url", ""))
            if target_url.endswith("/primary"):
                return {
                    "status_code": 503,
                    "response_json": {},
                    "response_text": "retry later",
                }
            return {
                "status_code": 202,
                "response_json": {"accepted": True},
                "response_text": "accepted",
            }

        service = ExternalGatewayService(
            control_plane_state=control_plane,
            approval_queue=queue,
            request_executor=executor,
            environment={
                "AGENT_OBOLOS_BUILD_WEBHOOK_URL": "https://obolos.example.test/primary",
                "AGENT_OBOLOS_BUILD_WEBHOOK_URL_BACKUP": "https://obolos.example.test/backup",
                "AGENT_OBOLOS_AUTH_TOKEN": "env-token",
            },
        )

        result = await service.send_delivery_via_capability(
            bundle=_bundle(),
            job_kind=JobKind.BUILD,
            provider_id="obolos.tech",
            capability_id="build_delivery_v1",
            approval_request_id=request_id,
            export_mode="internal",
        )

        assert result["ok"] is True
        assert result["route_id"] == "obolos_build_delivery_backup"
        assert result["fallback_used"] is True
        assert [attempt["status"] for attempt in result["attempted_routes"]] == [
            "failed",
            "sent",
        ]

    @pytest.mark.asyncio
    async def test_gateway_provider_send_reports_missing_route_config(
        self,
        control_plane,
    ):
        queue = ApprovalQueue()
        request_id = _approved_request(queue)
        service = ExternalGatewayService(
            control_plane_state=control_plane,
            approval_queue=queue,
            environment={},
        )

        result = await service.send_delivery_via_capability(
            bundle=_bundle(),
            job_kind=JobKind.BUILD,
            provider_id="obolos.tech",
            capability_id="build_delivery_v1",
            approval_request_id=request_id,
            export_mode="internal",
        )

        assert result["ok"] is False
        assert result["denial"]["code"] == "gateway_provider_not_configured"
        assert all(item["status"] == "unavailable" for item in result["attempted_routes"])
