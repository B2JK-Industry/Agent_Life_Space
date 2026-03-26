"""
Agent Life Space — Tool Router

Automaticky detekuje kedy John potrebuje externé dáta a fetchne ich
PRED CLI callom. Výsledky sa injektujú do promptu.

3 vrstvy:
    1. Always inject — dátum, čas (zadarmo, vždy)
    2. Pre-route — regex → auto-fetch (počasie, ceny, čas)
    3. Fallback — CLI rozhodne (programming, analýza)

Prečo pre-route a nie LLM tool use:
    - Používame CLI, nie API s tools parametrom
    - Pre-route je rýchlejšie (regex < LLM call)
    - Isté patterny sú deterministické (počasie, čas)

Rozšíriteľné: pridaj nový pattern do TOOL_PATTERNS.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# --- Tool pattern definitions ---
# Každý pattern: regex → tool name → handler

TOOL_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "weather",
        "patterns": [
            r"(?:počasie|weather|teplota|temperature)\s+(?:v|in|for|pre|na)\s+(\w+)",
            r"(?:aké|aká|ako)\s+(?:je|bude)\s+(?:počasie|teplota)\s+(?:v|in)\s+(\w+)",
            r"(?:počasie|weather)\s+(\w+)",
        ],
        "description": "Aktuálne počasie pre lokalitu",
    },
    {
        "name": "datetime",
        "patterns": [
            r"(?:koľko|kolko)\s+(?:je|bolo)\s+(?:hodín|hodin)",
            r"(?:aký|aky|jaky)\s+(?:je|dnes)\s+(?:dátum|datum|deň|den)",
            r"(?:what)\s+(?:time|date)",
        ],
        "description": "Aktuálny dátum a čas",
    },
    {
        "name": "crypto_price",
        "patterns": [
            r"(?:cena|price|kurz)\s+(?:btc|bitcoin|eth|ethereum)",
            r"(?:koľko|kolko)\s+(?:stojí|stoji)\s+(?:btc|bitcoin|eth|ethereum)",
        ],
        "description": "Aktuálna cena kryptomien",
    },
]


async def detect_and_fetch(text: str) -> dict[str, str]:
    """
    Analyzuj text, detekuj aké dáta treba, fetchni ich.

    Vracia dict s context kľúčmi:
        {"weather": "Praha: 8°C, ...", "datetime": "2026-03-25 08:30"}

    Ak nič netreba → vráti prázdny dict.
    """
    results: dict[str, str] = {}
    text_lower = text.lower()

    for tool in TOOL_PATTERNS:
        for pattern in tool["patterns"]:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    tool_name = tool["name"]
                    if tool_name == "weather":
                        location = match.group(1).strip()
                        data = await _fetch_weather(location)
                        if data:
                            results["weather"] = data
                    elif tool_name == "datetime":
                        results["datetime"] = _get_datetime()
                    elif tool_name == "crypto_price":
                        data = await _fetch_crypto_price(text_lower)
                        if data:
                            results["crypto"] = data
                    logger.info("tool_pre_route", tool=tool_name, match=match.group())
                except Exception as e:
                    logger.error("tool_pre_route_error", tool=tool_name, error=str(e))
                break  # Len prvý match per tool

    return results


def build_always_inject() -> str:
    """Kontext ktorý sa VŽDY pridá do promptu (zadarmo)."""
    now = datetime.now(UTC)
    return (
        f"Aktuálny dátum a čas: {now.strftime('%Y-%m-%d %H:%M')} UTC\n"
    )


def format_tool_context(results: dict[str, str]) -> str:
    """Formátuj tool výsledky pre injekciu do promptu."""
    if not results:
        return ""

    lines = ["Aktuálne dáta (práve zistené):"]
    for _key, value in results.items():
        lines.append(f"  {value}")
    return "\n".join(lines) + "\n"


# --- Tool implementations ---


# Slovenské skloňované tvary → základný tvar pre wttr.in
_LOCATION_NORMALIZE: dict[str, str] = {
    "prahe": "Praha", "prahy": "Praha", "prahu": "Praha", "prahou": "Praha",
    "bratislave": "Bratislava", "bratislavy": "Bratislava", "bratislavu": "Bratislava",
    "košiciach": "Košice", "košíc": "Košice",
    "brne": "Brno", "brna": "Brno",
    "viedni": "Wien", "viedne": "Wien",
    "londýne": "London", "londýna": "London",
    "berlíne": "Berlin", "berlína": "Berlin",
    "paríži": "Paris", "paríža": "Paris",
}


async def _fetch_weather(location: str) -> str:
    """Fetch počasie z wttr.in (zadarmo, bez API key)."""
    try:
        import aiohttp

        # Normalizuj slovenské skloňovanie
        location = _LOCATION_NORMALIZE.get(location.lower(), location.capitalize())
        url = f"https://wttr.in/{location}?format=j1&lang=sk"
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("weather_http_error", status=resp.status, location=location)
                    return ""
                data = await resp.json(content_type=None)

        current = data.get("current_condition", [{}])[0]
        temp = current.get("temp_C", "?")
        feels = current.get("FeelsLikeC", "?")
        humidity = current.get("humidity", "?")
        wind = current.get("windspeedKmph", "?")
        desc = current.get("lang_sk", [{}])
        if desc:
            desc_text = desc[0].get("value", current.get("weatherDesc", [{}])[0].get("value", ""))
        else:
            desc_text = current.get("weatherDesc", [{}])[0].get("value", "")

        # Sunrise/sunset
        astronomy = data.get("weather", [{}])[0].get("astronomy", [{}])[0]
        sunrise = astronomy.get("sunrise", "?")
        sunset = astronomy.get("sunset", "?")

        return (
            f"Počasie {location.capitalize()}: {temp}°C (pocitovo {feels}°C), "
            f"{desc_text}, vlhkosť {humidity}%, vietor {wind} km/h, "
            f"východ {sunrise}, západ {sunset}"
        )
    except Exception as e:
        logger.error("weather_fetch_error", location=location, error=str(e))
        return ""


def _get_datetime() -> str:
    """Aktuálny dátum a čas."""
    now = datetime.now()
    return f"Dátum: {now.strftime('%Y-%m-%d')}, Čas: {now.strftime('%H:%M:%S')}"


async def _fetch_crypto_price(text: str) -> str:
    """Fetch cenu BTC/ETH z CoinGecko (zadarmo, bez API key)."""
    try:
        import aiohttp

        coins = []
        if "btc" in text or "bitcoin" in text:
            coins.append("bitcoin")
        if "eth" in text or "ethereum" in text:
            coins.append("ethereum")

        if not coins:
            return ""

        ids = ",".join(coins)
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd,eur"

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()

        parts = []
        if "bitcoin" in data:
            parts.append(f"BTC: ${data['bitcoin']['usd']:,.0f} (€{data['bitcoin']['eur']:,.0f})")
        if "ethereum" in data:
            parts.append(f"ETH: ${data['ethereum']['usd']:,.0f} (€{data['ethereum']['eur']:,.0f})")

        return "Krypto ceny: " + ", ".join(parts)
    except Exception as e:
        logger.error("crypto_fetch_error", error=str(e))
        return ""
