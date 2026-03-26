"""
Tests for multi-step approval workflow.
"""

from __future__ import annotations

from agent.core.approval import ApprovalCategory, ApprovalQueue, ApprovalStatus


class TestMultiStepApproval:
    """High-risk actions can require multiple approvals."""

    def test_single_step_default(self):
        queue = ApprovalQueue()
        req = queue.propose(ApprovalCategory.TOOL, "simple action")
        assert req.required_approvals == 1
        result = queue.approve(req.id)
        assert result.status == ApprovalStatus.APPROVED

    def test_multi_step_partial(self):
        queue = ApprovalQueue()
        req = queue.propose(
            ApprovalCategory.FINANCE, "big expense",
            required_approvals=2,
        )
        result = queue.approve(req.id, decided_by="approver1")
        assert result.status == ApprovalStatus.PARTIALLY_APPROVED
        assert len(result.approvals_received) == 1

    def test_multi_step_full_approval(self):
        queue = ApprovalQueue()
        req = queue.propose(
            ApprovalCategory.FINANCE, "big expense",
            required_approvals=2,
        )
        queue.approve(req.id, decided_by="approver1")
        result = queue.approve(req.id, decided_by="approver2")
        assert result.status == ApprovalStatus.APPROVED
        assert len(result.approvals_received) == 2

    def test_same_person_cannot_double_approve(self):
        queue = ApprovalQueue()
        req = queue.propose(
            ApprovalCategory.FINANCE, "needs 2 approvals",
            required_approvals=2,
        )
        queue.approve(req.id, decided_by="same_person")
        result = queue.approve(req.id, decided_by="same_person")
        # Still partially approved — same person can't count twice
        assert result.status == ApprovalStatus.PARTIALLY_APPROVED
        assert len(result.approvals_received) == 1

    def test_three_step_approval(self):
        queue = ApprovalQueue()
        req = queue.propose(
            ApprovalCategory.HOST, "host access",
            required_approvals=3,
        )
        queue.approve(req.id, decided_by="a1")
        queue.approve(req.id, decided_by="a2")
        assert queue.get_pending()  # Still pending after 2/3

        result = queue.approve(req.id, decided_by="a3")
        assert result.status == ApprovalStatus.APPROVED
        assert len(queue.get_pending()) == 0

    def test_deny_cancels_multi_step(self):
        queue = ApprovalQueue()
        req = queue.propose(
            ApprovalCategory.FINANCE, "risky",
            required_approvals=2,
        )
        queue.approve(req.id, decided_by="a1")
        result = queue.deny(req.id, reason="too risky")
        assert result.status == ApprovalStatus.DENIED

    def test_to_dict_includes_approvals(self):
        queue = ApprovalQueue()
        req = queue.propose(
            ApprovalCategory.TOOL, "test",
            required_approvals=2,
        )
        queue.approve(req.id, decided_by="a1")
        pending = queue.get_pending()
        assert pending[0]["required_approvals"] == 2
        assert pending[0]["approvals_received"] == ["a1"]

    def test_partially_approved_can_expire(self):
        queue = ApprovalQueue()
        req = queue.propose(
            ApprovalCategory.TOOL, "expires",
            required_approvals=2, ttl_seconds=0,
        )
        # First approval detects expiry and returns EXPIRED
        result = queue.approve(req.id, decided_by="a1")
        assert result.status == ApprovalStatus.EXPIRED
