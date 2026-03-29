"""
Tests for approval queue — structured approval workflow for risk-sensitive actions.
"""

from __future__ import annotations

import tempfile

import pytest

from agent.core.approval import (
    ApprovalCategory,
    ApprovalQueue,
    ApprovalRequest,
    ApprovalStatus,
)
from agent.core.approval_storage import ApprovalStorage


class TestApprovalRequest:
    """ApprovalRequest captures action metadata."""

    def test_defaults(self):
        req = ApprovalRequest(description="test")
        assert req.status == ApprovalStatus.PENDING
        assert req.id
        assert req.created_at > 0

    def test_to_dict(self):
        req = ApprovalRequest(
            category=ApprovalCategory.FINANCE,
            description="Send 0.1 ETH",
            risk_level="high",
        )
        d = req.to_dict()
        assert d["category"] == "finance"
        assert d["description"] == "Send 0.1 ETH"

    def test_expiry(self):
        req = ApprovalRequest(ttl_seconds=0)
        # Immediately expired
        assert req.is_expired

    def test_not_expired_when_fresh(self):
        req = ApprovalRequest(ttl_seconds=3600)
        assert not req.is_expired


class TestApprovalQueue:
    """Queue manages the propose → approve/deny → execute lifecycle."""

    @pytest.fixture
    def queue(self):
        return ApprovalQueue()

    def test_propose(self, queue):
        req = queue.propose(
            category=ApprovalCategory.FINANCE,
            description="Pay $10 for API",
            risk_level="high",
            reason="Budget allocation needed",
        )
        assert req.status == ApprovalStatus.PENDING
        pending = queue.get_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == req.id

    def test_approve(self, queue):
        req = queue.propose(ApprovalCategory.TOOL, "run dangerous code")
        result = queue.approve(req.id, decided_by="owner")
        assert result.status == ApprovalStatus.APPROVED
        assert result.decided_by == "owner"
        assert len(queue.get_pending()) == 0

    def test_deny(self, queue):
        req = queue.propose(ApprovalCategory.EXTERNAL, "post to Twitter")
        result = queue.deny(req.id, reason="not now", decided_by="owner")
        assert result.status == ApprovalStatus.DENIED
        assert result.denial_reason == "not now"

    def test_approve_nonexistent_returns_none(self, queue):
        assert queue.approve("fake_id") is None

    def test_deny_nonexistent_returns_none(self, queue):
        assert queue.deny("fake_id") is None

    def test_mark_executed(self, queue):
        req = queue.propose(ApprovalCategory.FINANCE, "transfer")
        queue.approve(req.id)
        assert queue.mark_executed(req.id)

        history = queue.get_history()
        executed = [h for h in history if h["status"] == "executed"]
        assert len(executed) == 1

    def test_cannot_execute_unapproved(self, queue):
        req = queue.propose(ApprovalCategory.TOOL, "something")
        queue.deny(req.id)
        assert not queue.mark_executed(req.id)

    def test_expire_stale(self, queue):
        queue.propose(
            ApprovalCategory.TOOL, "old action", ttl_seconds=0
        )
        expired = queue.expire_stale()
        assert expired == 1
        assert len(queue.get_pending()) == 0

        history = queue.get_history()
        assert history[0]["status"] == "expired"

    def test_approve_expired_returns_expired(self, queue):
        req = queue.propose(ApprovalCategory.TOOL, "old", ttl_seconds=0)
        result = queue.approve(req.id)
        assert result.status == ApprovalStatus.EXPIRED

    def test_category_filter(self, queue):
        queue.propose(ApprovalCategory.FINANCE, "money thing")
        queue.propose(ApprovalCategory.TOOL, "code thing")
        queue.propose(ApprovalCategory.FINANCE, "another money thing")

        finance = queue.get_by_category(ApprovalCategory.FINANCE)
        assert len(finance) == 2

    def test_stats(self, queue):
        queue.propose(ApprovalCategory.TOOL, "a")
        req2 = queue.propose(ApprovalCategory.TOOL, "b")
        queue.approve(req2.id)

        stats = queue.get_stats()
        assert stats["pending"] == 1
        assert stats["history_total"] == 1

    def test_history_ring_buffer(self):
        queue = ApprovalQueue(max_history=3)
        for i in range(5):
            req = queue.propose(ApprovalCategory.TOOL, f"action {i}")
            queue.approve(req.id)

        assert len(queue.get_history()) == 3

    def test_multiple_categories(self, queue):
        queue.propose(ApprovalCategory.FINANCE, "pay")
        queue.propose(ApprovalCategory.HOST, "edit file")
        queue.propose(ApprovalCategory.EXTERNAL, "api call")

        pending = queue.get_pending()
        categories = {p["category"] for p in pending}
        assert categories == {"finance", "host", "external"}

    def test_list_requests_filters_by_job_and_artifact(self, queue):
        req = queue.propose(
            ApprovalCategory.EXTERNAL,
            "deliver build report",
            context={
                "job_id": "build-123",
                "artifact_ids": ["artifact-1", "artifact-2"],
            },
        )
        queue.approve(req.id)

        by_job = queue.list_requests(job_id="build-123")
        by_artifact = queue.list_requests(artifact_id="artifact-2")

        assert len(by_job) == 1
        assert by_job[0]["id"] == req.id
        assert len(by_artifact) == 1
        assert by_artifact[0]["id"] == req.id

    def test_list_requests_filters_by_workspace_and_bundle(self, queue):
        req = queue.propose(
            ApprovalCategory.EXTERNAL,
            "deliver build bundle",
            context={
                "job_id": "build-123",
                "workspace_id": "ws-123",
                "bundle_id": "build-delivery-build-123",
                "artifact_ids": ["artifact-1"],
            },
        )
        queue.approve(req.id)

        by_workspace = queue.list_requests(workspace_id="ws-123")
        by_bundle = queue.list_requests(bundle_id="build-delivery-build-123")

        assert len(by_workspace) == 1
        assert by_workspace[0]["id"] == req.id
        assert len(by_bundle) == 1
        assert by_bundle[0]["id"] == req.id

    def test_persistent_storage_recovers_pending_and_history(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            storage = ApprovalStorage(db_path=f.name)
            queue = ApprovalQueue(storage=storage)
            pending_req = queue.propose(
                ApprovalCategory.TOOL,
                "dangerous tool",
                context={"job_id": "job-pending"},
            )
            approved_req = queue.propose(
                ApprovalCategory.EXTERNAL,
                "deliver artifact",
                context={
                    "job_id": "job-approved",
                    "artifact_ids": ["artifact-1"],
                },
            )
            queue.approve(approved_req.id)

        recovered = ApprovalQueue(storage=ApprovalStorage(db_path=f.name))

        assert recovered.get_request(pending_req.id) is not None
        assert recovered.get_request(approved_req.id) is not None
        assert recovered.list_requests(job_id="job-approved")[0]["id"] == approved_req.id
        assert recovered.list_requests(artifact_id="artifact-1")[0]["id"] == approved_req.id
