"""
Tests for channel policy — per-channel capabilities and response classification.
"""

from __future__ import annotations

from agent.social.channel_policy import (
    CHANNEL_CAPABILITIES,
    ChannelTrustLevel,
    ResponseClass,
    can_send_response,
    classify_response,
    get_channel_capabilities,
)


class TestChannelCapabilities:
    """Different channels have different trust levels."""

    def test_telegram_owner_has_full_access(self):
        caps = get_channel_capabilities("telegram", is_owner=True, is_group=False)
        assert caps.trust_level == ChannelTrustLevel.FULL
        assert caps.can_execute_code
        assert caps.can_access_host
        assert caps.can_approve_actions

    def test_telegram_group_is_safe_mode(self):
        caps = get_channel_capabilities("telegram", is_owner=False, is_group=True)
        assert caps.trust_level == ChannelTrustLevel.SAFE_MODE
        assert not caps.can_execute_code
        assert not caps.can_access_host
        assert not caps.can_approve_actions

    def test_agent_api_is_restricted(self):
        caps = get_channel_capabilities("agent_api")
        assert caps.trust_level == ChannelTrustLevel.RESTRICTED
        assert not caps.can_execute_code
        assert not caps.can_access_host
        assert caps.can_web_fetch  # API can fetch

    def test_cli_has_full_access(self):
        caps = get_channel_capabilities("cli")
        assert caps.trust_level == ChannelTrustLevel.FULL
        assert caps.can_execute_code

    def test_unknown_channel_is_restricted(self):
        caps = get_channel_capabilities("unknown_channel")
        assert caps.trust_level == ChannelTrustLevel.RESTRICTED
        assert not caps.can_execute_code
        assert not caps.can_access_host

    def test_telegram_non_owner_private_is_safe_mode(self):
        """Non-owner in private chat is still safe mode."""
        caps = get_channel_capabilities("telegram", is_owner=False, is_group=False)
        assert caps.trust_level == ChannelTrustLevel.SAFE_MODE


class TestResponseClassification:
    """Response content determines sensitivity level."""

    def test_safe_response(self):
        assert classify_response("Ahoj, ako sa máš?") == ResponseClass.SAFE

    def test_private_response_wallet(self):
        assert classify_response("Wallet balance: 0.5 ETH") == ResponseClass.PRIVATE

    def test_private_response_budget(self):
        assert classify_response("Budget proposal: $100") == ResponseClass.PRIVATE

    def test_internal_response_api_key(self):
        assert classify_response("api_key=sk-abc123") == ResponseClass.INTERNAL

    def test_internal_response_path(self):
        assert classify_response("Error in /home/user/app.py") == ResponseClass.INTERNAL

    def test_internal_response_token(self):
        assert classify_response("Bearer eyJhb...") == ResponseClass.INTERNAL


class TestResponseSending:
    """Response class must match channel capability."""

    def test_safe_response_goes_anywhere(self):
        for caps in CHANNEL_CAPABILITIES.values():
            assert can_send_response(ResponseClass.SAFE, caps)

    def test_internal_response_allowed_for_full_trust_only(self):
        """INTERNAL responses allowed only on FULL trust channels (owner private chat)."""
        for name, caps in CHANNEL_CAPABILITIES.items():
            if caps.trust_level == ChannelTrustLevel.FULL:
                assert can_send_response(ResponseClass.INTERNAL, caps), (
                    f"INTERNAL should be allowed on FULL trust channel: {name}"
                )
            else:
                assert not can_send_response(ResponseClass.INTERNAL, caps), (
                    f"INTERNAL should be blocked on non-FULL trust channel: {name}"
                )

    def test_private_response_only_to_owner(self):
        owner_caps = get_channel_capabilities("telegram", is_owner=True)
        group_caps = get_channel_capabilities("telegram", is_group=True)

        assert can_send_response(ResponseClass.PRIVATE, owner_caps)
        assert not can_send_response(ResponseClass.PRIVATE, group_caps)

    def test_private_response_blocked_on_api(self):
        api_caps = get_channel_capabilities("agent_api")
        assert not can_send_response(ResponseClass.PRIVATE, api_caps)
