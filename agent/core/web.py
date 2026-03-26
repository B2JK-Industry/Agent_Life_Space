"""
Agent Life Space — Web Access Module

John vie čítať internet. Rozumne, bezpečne, s rate limitom.

Capabilities:
    - fetch_url: GET request, vráti text/HTML
    - fetch_json: GET/POST request, vráti parsovaný JSON
    - scrape_text: Extrahuje čistý text z HTML (bez tagov)
    - search_web: Jednoduchý search cez DuckDuckGo HTML

Rules:
    - Rate limit: max 10 requests per minute
    - Timeout: 15s per request
    - No auth to external services without Daniel's approval
    - Results stored in memory (episodic or semantic)
"""

from __future__ import annotations

import time
from html.parser import HTMLParser
from typing import Any

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

# Rate limiting
_request_times: list[float] = []
_MAX_REQUESTS_PER_MINUTE = 10
_REQUEST_TIMEOUT = 15


class _TextExtractor(HTMLParser):
    """Extract visible text from HTML, skip scripts/styles."""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._skip = False
        self._skip_tags = {"script", "style", "noscript", "svg", "head"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._skip_tags:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._skip_tags:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.strip()
            if text:
                self._text.append(text)

    def get_text(self) -> str:
        return "\n".join(self._text)


def _check_rate_limit() -> bool:
    """Check if we're within rate limit. Returns True if OK."""
    now = time.monotonic()
    # Remove entries older than 60s
    _request_times[:] = [t for t in _request_times if now - t < 60]
    if len(_request_times) >= _MAX_REQUESTS_PER_MINUTE:
        return False
    _request_times.append(now)
    return True


class WebAccess:
    """
    John's internet access. Fetch URLs, scrape text, call APIs.
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
                headers={"User-Agent": "John-AgentLifeSpace/0.1"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def fetch_url(self, url: str) -> dict[str, Any]:
        """GET a URL, return raw text content."""
        if not _check_rate_limit():
            return {"error": "Rate limit exceeded (10/min)", "url": url}

        logger.info("web_fetch", url=url)
        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                text = await resp.text()
                return {
                    "url": url,
                    "status": resp.status,
                    "content_type": resp.content_type,
                    "length": len(text),
                    "text": text[:10000],  # Cap at 10KB
                }
        except Exception as e:
            logger.error("web_fetch_error", url=url, error=str(e))
            return {"error": str(e), "url": url}

    async def fetch_json(
        self, url: str, method: str = "GET", json_data: dict | None = None,
    ) -> dict[str, Any]:
        """Fetch JSON from API endpoint."""
        if not _check_rate_limit():
            return {"error": "Rate limit exceeded (10/min)", "url": url}

        logger.info("web_fetch_json", url=url, method=method)
        try:
            session = await self._get_session()
            if method.upper() == "POST":
                async with session.post(url, json=json_data) as resp:
                    data = await resp.json()
                    return {"url": url, "status": resp.status, "data": data}
            else:
                async with session.get(url) as resp:
                    data = await resp.json()
                    return {"url": url, "status": resp.status, "data": data}
        except Exception as e:
            logger.error("web_fetch_json_error", url=url, error=str(e))
            return {"error": str(e), "url": url}

    async def scrape_text(self, url: str, max_chars: int = 5000) -> dict[str, Any]:
        """Fetch URL and extract clean text (no HTML tags)."""
        result = await self.fetch_url(url)
        if "error" in result:
            return result

        extractor = _TextExtractor()
        try:
            extractor.feed(result["text"])
        except Exception:
            pass

        clean_text = extractor.get_text()[:max_chars]

        return {
            "url": url,
            "status": result["status"],
            "text": clean_text,
            "length": len(clean_text),
        }

    async def search_web(self, query: str, max_results: int = 5) -> dict[str, Any]:
        """
        Search the web via DuckDuckGo HTML (no API key needed).
        Returns list of results with title, url, snippet.
        """
        if not _check_rate_limit():
            return {"error": "Rate limit exceeded (10/min)", "query": query}

        logger.info("web_search", query=query)
        search_url = f"https://html.duckduckgo.com/html/?q={query}"

        try:
            session = await self._get_session()
            async with session.get(search_url) as resp:
                html = await resp.text()

            # Parse results from DuckDuckGo HTML
            results = []
            # DuckDuckGo HTML results are in <a class="result__a"> tags
            import re
            # Find result blocks
            links = re.findall(
                r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.+?)</a>',
                html,
            )
            snippets = re.findall(
                r'<a class="result__snippet"[^>]*>(.+?)</a>',
                html, re.DOTALL,
            )

            for i, (url, title) in enumerate(links[:max_results]):
                # Clean HTML tags from title and snippet
                clean_title = re.sub(r'<[^>]+>', '', title).strip()
                clean_snippet = ""
                if i < len(snippets):
                    clean_snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()

                results.append({
                    "title": clean_title,
                    "url": url,
                    "snippet": clean_snippet[:200],
                })

            return {
                "query": query,
                "results": results,
                "count": len(results),
            }

        except Exception as e:
            logger.error("web_search_error", query=query, error=str(e))
            return {"error": str(e), "query": query}

    async def check_url(self, url: str) -> dict[str, Any]:
        """Quick check — is URL alive? Returns status code."""
        if not _check_rate_limit():
            return {"error": "Rate limit exceeded", "url": url}

        try:
            session = await self._get_session()
            async with session.head(url, allow_redirects=True) as resp:
                return {"url": url, "status": resp.status, "alive": resp.status < 400}
        except Exception as e:
            return {"url": url, "status": 0, "alive": False, "error": str(e)}
