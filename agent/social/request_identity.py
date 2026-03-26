"""
Agent Life Space — Request Identity & Replay Protection

Every API request has:
    - sender identity (who)
    - request nonce (unique, prevents replay)
    - timestamp (freshness check)
    - optional HMAC signature (integrity)

Replay protection:
    - Nonces are tracked in a bounded set
    - Requests older than max_age_seconds are rejected
    - Same nonce cannot be used twice
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class RequestIdentity:
    """Identity of an API request."""

    sender: str          # Who sent this
    nonce: str = ""      # Unique request ID (prevents replay)
    timestamp: float = 0.0  # When it was sent
    signature: str = ""  # Optional HMAC signature


class ReplayProtection:
    """
    Tracks nonces to prevent replay attacks.
    Bounded set — old nonces are evicted.
    """

    def __init__(
        self,
        max_nonces: int = 10000,
        max_age_seconds: int = 300,  # 5 minutes
    ) -> None:
        self._seen_nonces: dict[str, float] = {}
        self._max_nonces = max_nonces
        self._max_age = max_age_seconds

    def check_and_record(self, nonce: str, timestamp: float = 0.0) -> str | None:
        """
        Check if request is valid. Returns None if OK, error string if not.
        """
        now = time.time()

        # Reject old timestamps
        if timestamp > 0 and abs(now - timestamp) > self._max_age:
            return f"Request too old ({int(abs(now - timestamp))}s > {self._max_age}s max)"

        # Check nonce uniqueness
        if not nonce:
            return None  # No nonce = no replay protection (backward compat)

        if nonce in self._seen_nonces:
            logger.warning("replay_detected", nonce=nonce[:16])
            return "Replay detected — nonce already used"

        # Record nonce
        self._seen_nonces[nonce] = now

        # Evict old nonces
        if len(self._seen_nonces) > self._max_nonces:
            cutoff = now - self._max_age
            self._seen_nonces = {
                n: t for n, t in self._seen_nonces.items()
                if t > cutoff
            }

        return None

    @property
    def tracked_nonces(self) -> int:
        return len(self._seen_nonces)


def verify_hmac(
    message: str,
    signature: str,
    secret: str,
) -> bool:
    """Verify HMAC-SHA256 signature."""
    if not signature or not secret:
        return False
    expected = hmac.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def create_hmac(message: str, secret: str) -> str:
    """Create HMAC-SHA256 signature."""
    return hmac.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
