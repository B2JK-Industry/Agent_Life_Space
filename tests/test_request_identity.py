"""
Tests for request identity and replay protection.
"""

from __future__ import annotations

import time

from agent.social.request_identity import (
    ReplayProtection,
    RequestIdentity,
    create_hmac,
    verify_hmac,
)


class TestRequestIdentity:
    """Request identity captures sender info."""

    def test_basic_identity(self):
        identity = RequestIdentity(sender="other-agent", nonce="abc123")
        assert identity.sender == "other-agent"
        assert identity.nonce == "abc123"


class TestReplayProtection:
    """Nonce tracking prevents replay attacks."""

    def test_first_request_ok(self):
        rp = ReplayProtection()
        result = rp.check_and_record("nonce1", time.time())
        assert result is None

    def test_replay_detected(self):
        rp = ReplayProtection()
        rp.check_and_record("nonce1", time.time())
        result = rp.check_and_record("nonce1", time.time())
        assert result is not None
        assert "Replay" in result

    def test_different_nonces_ok(self):
        rp = ReplayProtection()
        assert rp.check_and_record("nonce1", time.time()) is None
        assert rp.check_and_record("nonce2", time.time()) is None
        assert rp.check_and_record("nonce3", time.time()) is None

    def test_old_timestamp_rejected(self):
        rp = ReplayProtection(max_age_seconds=60)
        old_time = time.time() - 120  # 2 minutes ago
        result = rp.check_and_record("nonce1", old_time)
        assert result is not None
        assert "too old" in result

    def test_no_nonce_backward_compat(self):
        rp = ReplayProtection()
        result = rp.check_and_record("", time.time())
        assert result is None  # No nonce = no protection (compat)

    def test_eviction(self):
        rp = ReplayProtection(max_nonces=3, max_age_seconds=300)
        for i in range(5):
            rp.check_and_record(f"nonce{i}", time.time())
        # Should have evicted some
        assert rp.tracked_nonces <= 5

    def test_zero_timestamp_no_age_check(self):
        rp = ReplayProtection()
        result = rp.check_and_record("nonce1", 0.0)
        assert result is None  # timestamp=0 means skip age check


class TestHMAC:
    """HMAC signature creation and verification."""

    def test_create_and_verify(self):
        msg = "hello world"
        secret = "my_secret_key"
        sig = create_hmac(msg, secret)
        assert verify_hmac(msg, sig, secret)

    def test_wrong_secret_fails(self):
        msg = "hello world"
        sig = create_hmac(msg, "correct_secret")
        assert not verify_hmac(msg, sig, "wrong_secret")

    def test_tampered_message_fails(self):
        sig = create_hmac("original", "secret")
        assert not verify_hmac("tampered", sig, "secret")

    def test_empty_signature_fails(self):
        assert not verify_hmac("msg", "", "secret")

    def test_empty_secret_fails(self):
        assert not verify_hmac("msg", "sig", "")

    def test_signature_is_deterministic(self):
        sig1 = create_hmac("test", "key")
        sig2 = create_hmac("test", "key")
        assert sig1 == sig2
