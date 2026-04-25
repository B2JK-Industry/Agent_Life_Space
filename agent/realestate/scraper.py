"""
Agent Life Space — Real Estate Watcher Scraper

Async sreality.cz API client with retry, rate limiting, pagination,
and client-side price filtering.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
import structlog

from agent.realestate.models import Estate, SearchConfig, build_url
from agent.realestate.store import RealEstateStore

logger = structlog.get_logger(__name__)

_API_BASE = "https://www.sreality.cz/api/cs/v2/estates"
_PER_PAGE = 60
_MAX_PAGES = 3
_RATE_LIMIT_DELAY = 2.0  # seconds between requests
_RETRY_DELAYS = (1.0, 3.0, 9.0)  # exponential backoff

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/120 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _parse_area(items: list[dict[str, Any]]) -> float:
    """Extract area in m² from the items list. Returns 0.0 if not found."""
    for item in items:
        name = item.get("name", "") or ""
        if "plocha" in name.lower():
            value_str = str(item.get("value", "") or "")
            match = re.search(r"[\d,\.]+", value_str)
            if match:
                try:
                    return float(match.group().replace(",", "."))
                except ValueError:
                    pass
    return 0.0


def _parse_estate(raw: dict[str, Any]) -> Estate | None:
    """Parse a single sreality API estate dict into an Estate dataclass."""
    hash_id = raw.get("hash_id")
    if not hash_id:
        return None

    hash_id = int(hash_id)
    title: str = raw.get("name", "") or ""
    price_obj = raw.get("price_czk") or {}
    price: int = int(price_obj.get("value_raw") or raw.get("price") or 0)
    locality: str = raw.get("locality", "") or ""
    has_floor_plan: bool = bool(raw.get("has_floor_plan", 0))
    labels: list[str] = raw.get("labels", []) or []

    seo: dict[str, Any] = raw.get("seo") or {}
    category_type: int = int(seo.get("category_type_cb") or 1)
    category_main: int = int(seo.get("category_main_cb") or 1)
    category_sub: int = int(seo.get("category_sub_cb") or 4)

    items: list[dict[str, Any]] = raw.get("items") or []
    area_m2 = _parse_area(items)
    price_per_m2 = round(price / area_m2, 2) if area_m2 > 0 else 0.0

    # image URL
    links = raw.get("_links") or {}
    images = links.get("images") or []
    image_url: str = images[0].get("href", "") if images else ""

    estate = Estate(
        hash_id=hash_id,
        title=title,
        price=price,
        area_m2=area_m2,
        price_per_m2=price_per_m2,
        url="",  # filled in below via build_url
        category_type=category_type,
        category_main=category_main,
        category_sub=category_sub,
        has_floor_plan=has_floor_plan,
        labels=labels,
        locality=locality,
        image_url=image_url,
    )
    estate.url = build_url(estate)
    return estate


class RealtyScraper:
    """Async sreality.cz API client."""

    def __init__(self, store: RealEstateStore, http_client: httpx.AsyncClient) -> None:
        self._store = store
        self._http = http_client

    # ── Internal helpers ─────────────────────────────────────────────────────────

    async def _get_with_retry(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET with up to 3 attempts and exponential backoff. Returns parsed JSON."""
        last_exc: Exception | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
            try:
                resp = await self._http.get(url, params=params, headers=_HEADERS, timeout=15.0)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "realestate.scraper.fetch_error",
                    attempt=attempt,
                    url=url,
                    error=str(exc),
                )
                if delay is not None:
                    await asyncio.sleep(delay)
        raise RuntimeError(f"sreality fetch failed after {len(_RETRY_DELAYS) + 1} attempts") from last_exc

    # ── Public API ─────────────────────────────────────────────────────────────────

    async def fetch_listings(self, search: SearchConfig) -> list[Estate]:
        """
        Fetch all matching listings from sreality (up to MAX_PAGES pages).

        Applies client-side price_max filter if present in search.params_json,
        because the sreality API silently ignores the price_max query param.
        """
        base_params: dict[str, Any] = {
            **search.params_json,
            "per_page": _PER_PAGE,
        }
        # Remove price_max from API params — we filter client-side
        base_params.pop("price_max", None)

        price_max: int | None = search.price_max
        results: list[Estate] = []

        for page in range(1, _MAX_PAGES + 1):
            params = {**base_params, "page": page}
            try:
                data = await self._get_with_retry(_API_BASE, params)
            except RuntimeError as exc:
                logger.error(
                    "realestate.scraper.page_failed",
                    search=search.name,
                    page=page,
                    error=str(exc),
                )
                break

            estates_raw: list[dict[str, Any]] = (
                data.get("_embedded", {}).get("estates", []) or []
            )
            logger.debug(
                "realestate.scraper.page_fetched",
                search=search.name,
                page=page,
                count=len(estates_raw),
            )

            for raw in estates_raw:
                estate = _parse_estate(raw)
                if estate is None:
                    continue
                # Client-side price cap
                if price_max is not None and estate.price > price_max:
                    continue
                results.append(estate)

            # Stop paginating if we got fewer items than a full page
            if len(estates_raw) < _PER_PAGE:
                break

            if page < _MAX_PAGES:
                await asyncio.sleep(_RATE_LIMIT_DELAY)

        logger.info(
            "realestate.scraper.fetch_done",
            search=search.name,
            total=len(results),
        )
        return results

    async def scrape_search(
        self,
        search: SearchConfig,
        *,
        silent: bool = False,
    ) -> list[Estate]:
        """
        Fetch listings and persist price history.

        - silent=True (warm-up run): store prices but return empty list
        - silent=False: return all fetched estates (new + unchanged + changed)

        upsert_price returns None on first snapshot (new listing) or a
        float pct_change on subsequent ones.
        """
        estates = await self.fetch_listings(search)

        for estate in estates:
            await self._store.upsert_price(
                estate.hash_id,
                search.name,
                estate.price,
            )

        if silent:
            logger.info(
                "realestate.scraper.silent_warmup",
                search=search.name,
                stored=len(estates),
            )
            return []

        logger.info(
            "realestate.scraper.scrape_done",
            search=search.name,
            total=len(estates),
        )
        return estates

    async def check_url_alive(self, url: str) -> bool:
        """
        HEAD-check a listing URL. Returns True if HTTP status < 400.
        Timeout: 10 seconds. Does NOT retry.
        """
        try:
            resp = await self._http.head(
                url, headers=_HEADERS, timeout=10.0, follow_redirects=True
            )
            alive = resp.status_code < 400
            if not alive:
                logger.debug(
                    "realestate.scraper.url_dead",
                    url=url,
                    status=resp.status_code,
                )
            return alive
        except Exception as exc:
            logger.debug("realestate.scraper.url_check_error", url=url, error=str(exc))
            return False
