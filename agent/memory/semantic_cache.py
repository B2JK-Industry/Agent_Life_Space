"""
Agent Life Space — Semantic Cache

Ak John už odpovedal na podobnú otázku, vráti cache.
Zero tokens, okamžitá odpoveď.

Používa rovnaký embedding model ako semantic_router.
Cosine similarity > threshold → cache hit.

Cache je in-memory (rýchla) s optional SQLite persistence.
TTL na záznamy (default 1h) — odpovede starnú.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_TTL = 3600  # 1 hour
_DEFAULT_THRESHOLD = 0.95  # cosine similarity for cache hit (raised from 0.90 to reduce false matches)
_MAX_CACHE_SIZE = 200
# Queries shorter than this are too ambiguous for semantic matching
_MIN_QUERY_LENGTH = 12
# Max length ratio between query and cached query (prevents "hi" matching "what is X")
_MAX_LENGTH_RATIO = 3.0


@dataclass
class CacheEntry:
    query_embedding: Any  # numpy array
    query_text: str
    response: str
    created_at: float = field(default_factory=time.monotonic)
    hit_count: int = 0


class SemanticCache:
    """
    Cache LLM responses indexed by semantic similarity.
    """

    def __init__(
        self,
        threshold: float = _DEFAULT_THRESHOLD,
        ttl: int = _DEFAULT_TTL,
        max_size: int = _MAX_CACHE_SIZE,
    ) -> None:
        self._threshold = threshold
        self._ttl = ttl
        self._max_size = max_size
        self._entries: list[CacheEntry] = []
        self._model = None
        self._hits = 0
        self._misses = 0

    def _get_model(self) -> Any:
        """Reuse the semantic router's model (already loaded)."""
        if self._model is not None:
            return self._model
        try:
            from agent.brain.semantic_router import _load_model
            self._model = _load_model()
            return self._model
        except Exception:
            return None

    def lookup(self, query: str) -> str | None:
        """
        Check if we have a cached response for a similar query.
        Returns cached response or None.
        """
        # Skip very short queries — too ambiguous for semantic matching
        if len(query.strip()) < _MIN_QUERY_LENGTH:
            self._misses += 1
            return None

        # Skip command-like queries (/, zapamätaj, remember, etc.)
        stripped = query.strip().lower()
        if stripped.startswith("/") or stripped.startswith("zapam") or stripped.startswith("remember"):
            self._misses += 1
            return None

        model = self._get_model()
        if model is None:
            return None

        import numpy as np

        # Encode query
        query_emb = model.encode([query], convert_to_numpy=True)[0]

        # Evict expired entries
        now = time.monotonic()
        self._entries = [e for e in self._entries if now - e.created_at < self._ttl]

        # Find best match
        best_score = 0.0
        best_entry: CacheEntry | None = None

        for entry in self._entries:
            # Length ratio guard: skip if queries are wildly different lengths
            q_len = max(len(query), 1)
            c_len = max(len(entry.query_text), 1)
            ratio = max(q_len, c_len) / min(q_len, c_len)
            if ratio > _MAX_LENGTH_RATIO:
                continue

            dot = np.dot(query_emb, entry.query_embedding)
            norm = np.linalg.norm(query_emb) * np.linalg.norm(entry.query_embedding)
            if norm > 0:
                similarity = float(dot / norm)
                if similarity > best_score:
                    best_score = similarity
                    best_entry = entry

        if best_entry and best_score >= self._threshold:
            best_entry.hit_count += 1
            self._hits += 1
            logger.info(
                "semantic_cache_hit",
                query=query[:50],
                cached_query=best_entry.query_text[:50],
                similarity=round(best_score, 3),
                hit_count=best_entry.hit_count,
            )
            return best_entry.response

        self._misses += 1
        return None

    def store(self, query: str, response: str) -> None:
        """Store a query-response pair in cache."""
        # Don't cache very short queries, commands, or error responses
        if len(query.strip()) < _MIN_QUERY_LENGTH:
            return
        if query.strip().startswith("/"):
            return
        if len(response) < 10 or "chyba" in response.lower():
            return

        model = self._get_model()
        if model is None:
            return

        query_emb = model.encode([query], convert_to_numpy=True)[0]

        # Evict if full (remove oldest)
        if len(self._entries) >= self._max_size:
            self._entries.sort(key=lambda e: e.created_at)
            self._entries = self._entries[self._max_size // 2:]

        self._entries.append(CacheEntry(
            query_embedding=query_emb,
            query_text=query,
            response=response,
        ))

        logger.info("semantic_cache_stored", query=query[:50], entries=len(self._entries))

    def get_stats(self) -> dict[str, float]:
        return {
            "entries": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1), 2),
        }

    def clear(self) -> None:
        self._entries.clear()
        logger.info("semantic_cache_cleared")
