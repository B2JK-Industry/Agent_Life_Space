"""Tests for the structured Agent API review endpoint."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.review.models import ReviewJob, ReviewJobStatus, ReviewPhase, ReviewReport


class TestAgentAPIReviewEndpoint:
    def _make_request(
        self,
        payload: dict[str, object],
        auth: str = "Bearer secret",
        *,
        json_side_effect: Exception | None = None,
    ):
        request = MagicMock()
        request.headers = {"Authorization": auth}
        request.remote = "127.0.0.1"
        request.json = AsyncMock(
            side_effect=json_side_effect,
            return_value=payload,
        )
        return request

    @pytest.mark.asyncio
    async def test_review_endpoint_routes_to_orchestrator(self):
        from agent.social.agent_api import AgentAPI

        job = ReviewJob(
            id="review-123",
            source="api",
            requester="svc-review",
        )
        job.status = ReviewJobStatus.COMPLETED
        job.phase = ReviewPhase.COMPLETED
        job.report = ReviewReport(executive_summary="Looks good.", verdict="pass")

        agent = MagicMock()
        agent.run_review_job = AsyncMock(return_value=job)
        api = AgentAPI(agent=agent, api_keys=["secret"])

        response = await api._handle_review(
            self._make_request(
                {
                    "repo_path": "/tmp/repo",
                    "sender": "svc-review",
                    "context": "API-triggered review",
                }
            )
        )

        assert response.status == 200
        payload = json.loads(response.text)
        assert payload["job_id"] == "review-123"
        assert payload["status"] == "completed"
        intake = agent.run_review_job.await_args.args[0]
        assert intake.source == "api"
        assert intake.requester == "svc-review"

    @pytest.mark.asyncio
    async def test_review_endpoint_rejects_invalid_payload(self):
        from agent.social.agent_api import AgentAPI

        api = AgentAPI(agent=MagicMock(), api_keys=["secret"])
        response = await api._handle_review(
            self._make_request({"sender": "svc-review"})
        )

        assert response.status == 400
        payload = json.loads(response.text)
        assert "repo_path" in payload["error"]
        assert payload["denial"]["code"] == "agent_api_review_validation_failed"

    @pytest.mark.asyncio
    async def test_review_endpoint_returns_job_denial_when_blocked(self):
        from agent.social.agent_api import AgentAPI

        job = ReviewJob(
            id="review-blocked",
            source="api",
            requester="svc-review",
        )
        job.status = ReviewJobStatus.BLOCKED
        job.phase = ReviewPhase.VERIFYING
        job.report = ReviewReport(executive_summary="Blocked", verdict="fail")
        job.error = "Policy blocked review"
        job.denial = {"code": "review_policy_blocked"}

        agent = MagicMock()
        agent.run_review_job = AsyncMock(return_value=job)
        api = AgentAPI(agent=agent, api_keys=["secret"])

        response = await api._handle_review(
            self._make_request({"repo_path": "/tmp/repo", "sender": "svc-review"})
        )

        assert response.status == 422
        payload = json.loads(response.text)
        assert payload["denial"]["code"] == "review_policy_blocked"

    @pytest.mark.asyncio
    async def test_review_endpoint_invalid_json_includes_structured_denial(self):
        from agent.social.agent_api import AgentAPI

        api = AgentAPI(agent=MagicMock(), api_keys=["secret"])
        response = await api._handle_review(
            self._make_request(
                {},
                json_side_effect=ValueError("bad json"),
            )
        )

        assert response.status == 400
        payload = json.loads(response.text)
        assert payload["denial"]["code"] == "agent_api_invalid_json"

    @pytest.mark.asyncio
    async def test_message_endpoint_auth_error_includes_structured_denial(self):
        from agent.social.agent_api import AgentAPI

        api = AgentAPI(api_keys=["secret"])
        response = await api._handle_message(
            self._make_request({"message": "hello"}, auth="Bearer wrong")
        )

        assert response.status == 401
        payload = json.loads(response.text)
        assert payload["denial"]["code"] == "agent_api_auth_failed"
