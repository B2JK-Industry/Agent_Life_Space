"""Tests for the structured Agent API review endpoint."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.review.models import ReviewJob, ReviewJobStatus, ReviewPhase, ReviewReport


class TestAgentAPIReviewEndpoint:
    def _make_request(self, payload: dict[str, object], auth: str = "Bearer secret"):
        request = MagicMock()
        request.headers = {"Authorization": auth}
        request.remote = "127.0.0.1"
        request.json = AsyncMock(return_value=payload)
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
