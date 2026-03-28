from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_fetch_url_rate_limit_returns_structured_denial(monkeypatch):
    from agent.core.web import WebAccess

    monkeypatch.setattr("agent.core.web._check_rate_limit", lambda: False)

    result = await WebAccess().fetch_url("https://example.com")

    assert result["denial"]["code"] == "web_rate_limited"


@pytest.mark.asyncio
async def test_fetch_json_exception_returns_structured_denial(monkeypatch):
    from agent.core.web import WebAccess

    web = WebAccess()

    async def raise_error():
        raise RuntimeError("network down")

    monkeypatch.setattr(web, "_get_session", raise_error)

    result = await web.fetch_json("https://example.com/api")

    assert result["error"] == "network down"
    assert result["denial"]["code"] == "web_json_fetch_failed"


@pytest.mark.asyncio
async def test_check_url_exception_returns_structured_denial(monkeypatch):
    from agent.core.web import WebAccess

    web = WebAccess()

    async def raise_error():
        raise RuntimeError("dns failed")

    monkeypatch.setattr(web, "_get_session", raise_error)

    result = await web.check_url("https://example.com")

    assert result["alive"] is False
    assert result["denial"]["code"] == "web_head_check_failed"
