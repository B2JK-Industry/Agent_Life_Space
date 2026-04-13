"""
Agent Life Space — Web Monitor Extraction

Generic list-item extraction from server-rendered HTML pages.
Heuristic-based: looks for repeated structural patterns (list items,
table rows, card-like divs) and extracts title/url/price/location.

Not designed for JS-heavy SPAs. Works best on:
- Server-rendered listing pages
- JSON API responses
- Simple HTML tables / lists / card grids
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

from agent.web_monitor.models import MonitorItem


class _LinkExtractor(HTMLParser):
    """Minimal HTML parser that extracts <a> tags with href + text."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for name, val in attrs:
                if name == "href" and val:
                    self._current_href = val
                    self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href is not None:
            text = " ".join(t for t in self._current_text if t)
            if text and len(text) > 3:
                self.links.append({"href": self._current_href, "text": text})
            self._current_href = None
            self._current_text = []


# Price extraction patterns
_PRICE_RE = re.compile(
    r"(?:(?:CZK|EUR|USD|\$|€|Kč)\s*)?(\d[\d\s,.]*\d)\s*"
    r"(?:CZK|EUR|USD|Kč|€|\$)?",
    re.IGNORECASE,
)


def _parse_price(text: str) -> float | None:
    """Try to extract a numeric price from text."""
    m = _PRICE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _detect_currency(text: str) -> str:
    """Best-effort currency detection."""
    upper = text.upper()
    if "CZK" in upper or "KČ" in upper or "Kč" in text:
        return "CZK"
    if "EUR" in upper or "€" in text:
        return "EUR"
    if "USD" in upper or "$" in text:
        return "USD"
    return ""


def extract_items_from_html(html: str, base_url: str = "") -> list[MonitorItem]:
    """Extract list-like items from server-rendered HTML.

    Heuristic approach:
    1. Extract all <a> links with meaningful text
    2. Filter to likely listing items (length, uniqueness)
    3. Try to extract price from surrounding context
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        return []

    items: list[MonitorItem] = []
    seen_titles: set[str] = set()

    for link in parser.links:
        title = link["text"][:200]
        href = link["href"]

        # Skip navigation / utility links
        if len(title) < 5:
            continue
        if title.lower() in seen_titles:
            continue
        if any(skip in href.lower() for skip in (
            "login", "signup", "register", "javascript:", "#",
            "mailto:", "tel:", "/search", "/filter",
        )):
            continue

        seen_titles.add(title.lower())

        # Resolve relative URLs
        url = href
        if base_url and not href.startswith(("http://", "https://")):
            url = base_url.rstrip("/") + "/" + href.lstrip("/")

        items.append(MonitorItem(
            title=title,
            url=url,
            summary=title[:100],
        ))

    return items


def extract_items_from_json(data: Any, base_url: str = "") -> list[MonitorItem]:
    """Extract items from a JSON API response.

    Expects either:
    - A list of objects at top level
    - A dict with a list under a common key (items, results, data, estates, etc.)
    """
    item_list: list[dict[str, Any]] = []

    if isinstance(data, list):
        item_list = data
    elif isinstance(data, dict):
        # Search for a list-valued key
        for key in ("items", "results", "data", "estates", "listings",
                     "records", "entries", "_embedded"):
            val = data.get(key)
            if isinstance(val, list):
                item_list = val
                break
            if isinstance(val, dict):
                # Nested: _embedded.estates
                for subkey in ("estates", "items", "results"):
                    subval = val.get(subkey)
                    if isinstance(subval, list):
                        item_list = subval
                        break
                if item_list:
                    break

    items: list[MonitorItem] = []
    for obj in item_list[:100]:  # cap at 100 items
        if not isinstance(obj, dict):
            continue
        title = str(
            obj.get("name") or obj.get("title") or obj.get("label") or ""
        )[:200]
        url = str(obj.get("url") or obj.get("link") or obj.get("href") or "")
        if not url and obj.get("_links", {}).get("self", {}).get("href"):
            url = str(obj["_links"]["self"]["href"])
        if base_url and url and not url.startswith("http"):
            url = base_url.rstrip("/") + url

        price = None
        price_currency = ""
        price_raw = obj.get("price") or obj.get("price_czk", {}).get("value_raw")
        if isinstance(price_raw, (int, float)):
            price = float(price_raw)
        elif isinstance(price_raw, str):
            price = _parse_price(price_raw)
        if price is not None:
            price_currency = _detect_currency(str(obj.get("price_czk", {}).get("unit", "")))

        location = str(obj.get("locality") or obj.get("location") or obj.get("address") or "")

        items.append(MonitorItem(
            title=title or f"Item #{len(items) + 1}",
            url=url,
            summary=title[:100],
            price=price,
            price_currency=price_currency,
            location=location,
            raw_fields={k: v for k, v in obj.items() if k not in ("_links", "_embedded")},
        ))

    return items


def extract_items(content: str, content_type: str = "", base_url: str = "") -> list[MonitorItem]:
    """Auto-detect content type and extract items."""
    # Try JSON first
    if content_type and "json" in content_type.lower():
        try:
            data = json.loads(content)
            return extract_items_from_json(data, base_url)
        except (json.JSONDecodeError, ValueError):
            pass

    # Try JSON even without content-type hint
    stripped = content.strip()
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(stripped)
            return extract_items_from_json(data, base_url)
        except (json.JSONDecodeError, ValueError):
            pass

    # Fall back to HTML extraction
    return extract_items_from_html(content, base_url)
