"""
Agent Life Space — User-facing error normalization.

The Claude Code CLI surfaces a handful of internal error shapes that
are confusing for end users:

  * ``errormaxturns`` / ``error_max_turns`` — tool-use loop hit the
    cap before producing a final answer.
  * ``tooluse`` / ``tool_use`` JSON blocks — the model wanted to call
    a tool but the call failed or was rejected.
  * Raw structured ``result`` blobs (``{"is_error": true, ...}``).
  * ``stop_reason`` / ``session_id`` / other CLI metadata.

Operators have to see these in logs (logging path stays untouched),
but Telegram users only need a short human sentence. This module is
the single normalization seam.

Usage::

    from agent.core.error_normalize import normalize_user_error

    text = normalize_user_error(raw_provider_text)

The function is intentionally pure-text: no LLM, no JSON parsing
beyond a defensive ``json.loads``, no regex magic. False positives
are acceptable — we'd rather drop a bit of structured data than
echo a 4 KB JSON blob into a chat window.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Stop-reason → short user-facing line.
_STOP_REASON_MAP: dict[str, str] = {
    "max_turns": "I hit my tool-use turn limit before reaching a final answer.",
    "errormaxturns": "I hit my tool-use turn limit before reaching a final answer.",
    "error_max_turns": "I hit my tool-use turn limit before reaching a final answer.",
    "tool_use_failed": "An internal tool call failed before I could finish.",
    "tooluse_failed": "An internal tool call failed before I could finish.",
    "permission_denied": "An internal tool call was denied by policy.",
    "tool_input_invalid": "I built a tool call with invalid input.",
}

# Substrings that indicate the payload is internal CLI noise even
# without an explicit stop_reason.
_INTERNAL_NOISE_SUBSTRINGS: tuple[str, ...] = (
    "errormaxturns",
    "error_max_turns",
    "max_turns",
    '"tool_use"',
    '"is_error"',
    '"stop_reason"',
    '"session_id"',
    '"tool_use_id"',
)

_TOKEN_COST_LINE = re.compile(r"_💰 .*?tokens_", re.DOTALL)

# Plain-text CLI / network timeout. We normalize a wide variety of
# wording so the user always sees a short friendly sentence and never
# sees "CLI timeout after 180s" or "asyncio.TimeoutError" in chat.
#
# Variants matched:
#   * ``CLI timeout after 180s``
#   * ``timeout after 60s`` / ``timed out after 120 seconds``
#   * ``deadline exceeded`` (gRPC / aiohttp style)
#   * ``request_timeout=60``
#   * ``asyncio.TimeoutError`` / ``TimeoutError``
#   * ``read timed out``
_CLI_TIMEOUT_RE = re.compile(
    r"\b(?:cli\s+)?(?:read\s+)?(?:timed?\s+out|timeout)"
    r"(?:\s+after\s+(?P<secs>\d+)\s*(?:s|sec|seconds?))?\b",
    re.IGNORECASE,
)
_DEADLINE_RE = re.compile(r"\bdeadline\s+exceeded\b", re.IGNORECASE)
_REQUEST_TIMEOUT_RE = re.compile(
    r"\brequest_timeout\s*[=:]\s*(?P<secs>\d+)\b",
    re.IGNORECASE,
)
_ASYNCIO_TIMEOUT_RE = re.compile(
    r"\b(?:asyncio\.)?TimeoutError\b",
)


def normalize_user_error(raw: str | None) -> str:
    """Return a short user-facing sentence for *raw*.

    If *raw* doesn't look like a structured CLI error, the input is
    returned unchanged (modulo whitespace trimming).
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""

    # Cheap path: if the text looks like a JSON object, try to parse
    # and pull a stop_reason / error message.
    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            short = _from_json_payload(obj)
            if short:
                return short

    lower = text.lower()

    # Plain CLI / network timeout family — must run BEFORE the
    # structured-noise check because "timeout" is also in the
    # internal-noise table for the *structured* form.
    req_timeout = _REQUEST_TIMEOUT_RE.search(text)
    if req_timeout:
        return _friendly_timeout(req_timeout.group("secs"))
    if _DEADLINE_RE.search(text) or _ASYNCIO_TIMEOUT_RE.search(text):
        return _friendly_timeout(None)
    timeout_match = _CLI_TIMEOUT_RE.search(text)
    if timeout_match:
        return _friendly_timeout(timeout_match.group("secs"))

    if any(token in lower for token in _INTERNAL_NOISE_SUBSTRINGS):
        # Try to find a known stop reason.
        for key, friendly in _STOP_REASON_MAP.items():
            if key in lower:
                return friendly
        return (
            "Something went wrong inside the LLM tool-use loop. "
            "I logged the details — try again or rephrase the request."
        )

    return text


def _friendly_timeout(secs: str | None) -> str:
    """Map a CLI timeout to a short user-facing sentence."""
    base = (
        "I took too long thinking and didn't finish the reply. "
        "Try shortening the question or asking it more directly."
    )
    if secs:
        return f"{base} (timeout after {secs}s)"
    return base


def _from_json_payload(obj: dict[str, Any]) -> str | None:
    """Map a JSON payload to a friendly line, or return ``None``."""
    stop_reason = str(obj.get("stop_reason") or obj.get("error_type") or "").lower()
    if stop_reason in _STOP_REASON_MAP:
        return _STOP_REASON_MAP[stop_reason]
    if obj.get("is_error"):
        msg = obj.get("result") or obj.get("error") or obj.get("message")
        if isinstance(msg, str) and msg.strip():
            return f"Internal error: {msg.strip()[:200]}"
        return "Internal error in the LLM provider — see logs for details."
    # Plain assistant text occasionally arrives wrapped in a result obj.
    result = obj.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    return None


def normalize_telegram_reply(text: str) -> str:
    """Strip internal cost/usage banner before sending to a user.

    The brain appends a ``_💰 $X | model | ...tokens_`` block to most
    replies. For replies that reach end-users via channels other than
    Telegram (where the operator wants the meter visible), the meter
    can leak proprietary cost data. This helper removes it.
    """
    if not text:
        return ""
    return _TOKEN_COST_LINE.sub("", text).rstrip()


__all__ = ["normalize_telegram_reply", "normalize_user_error"]
