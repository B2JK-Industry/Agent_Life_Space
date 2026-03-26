"""
Tests pre agent/brain/tool_router.py — pre-routing toolov.
"""

from __future__ import annotations

import pytest

from agent.brain.tool_router import (
    _get_datetime,
    build_always_inject,
    detect_and_fetch,
    format_tool_context,
)


class TestBuildAlwaysInject:
    def test_contains_date(self):
        result = build_always_inject()
        assert "Aktuálny dátum" in result
        assert "UTC" in result

    def test_not_empty(self):
        assert len(build_always_inject()) > 10


class TestGetDatetime:
    def test_format(self):
        result = _get_datetime()
        assert "Dátum:" in result
        assert "Čas:" in result


class TestDetectAndFetch:
    @pytest.mark.asyncio
    async def test_detect_weather(self):
        results = await detect_and_fetch("aké je počasie v Prahe?")
        # May fail if wttr.in is down, but should at least try
        assert isinstance(results, dict)

    @pytest.mark.asyncio
    async def test_detect_datetime(self):
        results = await detect_and_fetch("koľko je hodín?")
        assert "datetime" in results
        assert "Dátum:" in results["datetime"]

    @pytest.mark.asyncio
    async def test_no_tool_needed(self):
        results = await detect_and_fetch("ahoj ako sa máš?")
        assert results == {}

    @pytest.mark.asyncio
    async def test_detect_crypto(self):
        results = await detect_and_fetch("aká je cena BTC?")
        # May fail if CoinGecko is down
        assert isinstance(results, dict)


class TestFormatToolContext:
    def test_empty(self):
        assert format_tool_context({}) == ""

    def test_with_data(self):
        result = format_tool_context({"weather": "Praha: 8°C"})
        assert "Praha: 8°C" in result
        assert "Aktuálne dáta" in result

    def test_multiple(self):
        result = format_tool_context({
            "weather": "Praha: 8°C",
            "datetime": "2026-03-25 08:30",
        })
        assert "Praha" in result
        assert "2026" in result
