"""
Agent Life Space — Self-RAG (Retrieval Augmented Generation)

Pred LLM volaním hľadaj v lokálnych dátach:
    1. Knowledge base (.md súbory) — zakódované cez embeddingy
    2. Semantic memory (SQLite) — fakty a vzory
    3. Procedural memory — postupy

Ak nájdeme dostatočne relevantný match:
    - HIGH (>0.85): vráť priamo, žiadne LLM
    - MEDIUM (0.60-0.85): pridaj ako kontext k LLM (menší prompt)
    - LOW (<0.60): LLM bez kontextu

Index sa buduje pri štarte a aktualizuje pri zmenách v KB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_HIGH_THRESHOLD = 0.85
_MEDIUM_THRESHOLD = 0.60


class RAGIndex:
    """
    Embedding-based index over knowledge base and memory.
    """

    def __init__(self, knowledge_dir: str = "") -> None:
        self._knowledge_dir = Path(knowledge_dir) if knowledge_dir else Path.home() / "agent-life-space" / "agent" / "brain" / "knowledge"
        self._model = None
        self._index: list[dict[str, Any]] = []  # [{text, embedding, source, category}]
        self._built = False

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from agent.brain.semantic_router import _load_model
            self._model = _load_model()
            return self._model
        except Exception:
            return None

    def build_index(self) -> int:
        """
        Build embedding index from knowledge base files.
        Returns number of indexed documents.
        """
        model = self._get_model()
        if model is None:
            return 0

        import numpy as np

        self._index.clear()
        count = 0

        # Index all .md files in knowledge base
        if self._knowledge_dir.exists():
            for md_file in self._knowledge_dir.rglob("*.md"):
                try:
                    content = md_file.read_text(encoding="utf-8")
                    if len(content) < 10:
                        continue

                    # Split long docs into chunks (~500 chars each)
                    chunks = self._chunk_text(content, max_chars=500)
                    for chunk in chunks:
                        embedding = model.encode([chunk], convert_to_numpy=True)[0]
                        self._index.append({
                            "text": chunk,
                            "embedding": embedding,
                            "source": str(md_file.relative_to(self._knowledge_dir)),
                            "category": md_file.parent.name,
                        })
                        count += 1
                except Exception as e:
                    logger.error("rag_index_error", file=str(md_file), error=str(e))

        self._built = True
        logger.info("rag_index_built", documents=count)
        return count

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 500) -> list[str]:
        """Split text into chunks at paragraph boundaries."""
        paragraphs = text.split("\n\n")
        chunks = []
        current = ""

        for para in paragraphs:
            if len(current) + len(para) > max_chars and current:
                chunks.append(current.strip())
                current = para
            else:
                current += "\n\n" + para if current else para

        if current.strip():
            chunks.append(current.strip())

        return chunks if chunks else [text[:max_chars]]

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """
        Search knowledge base by semantic similarity.
        Returns list of {text, source, score, category}.
        """
        if not self._built:
            self.build_index()

        model = self._get_model()
        if model is None or not self._index:
            return []

        import numpy as np

        query_emb = model.encode([query], convert_to_numpy=True)[0]

        results = []
        for doc in self._index:
            dot = np.dot(query_emb, doc["embedding"])
            norm = np.linalg.norm(query_emb) * np.linalg.norm(doc["embedding"])
            score = float(dot / norm) if norm > 0 else 0.0
            results.append({
                "text": doc["text"],
                "source": doc["source"],
                "category": doc["category"],
                "score": score,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def retrieve_for_llm(self, query: str) -> dict[str, Any]:
        """
        Self-RAG decision: what to do with the query?
        Returns {action: "direct"|"augment"|"llm_only", context: str, source: str}
        """
        results = self.search(query, top_k=3)

        if not results:
            return {"action": "llm_only", "context": "", "source": ""}

        best = results[0]

        if best["score"] >= _HIGH_THRESHOLD:
            # High confidence — answer directly from KB
            logger.info("rag_direct", source=best["source"], score=round(best["score"], 3))
            return {
                "action": "direct",
                "context": best["text"],
                "source": best["source"],
                "score": best["score"],
            }
        elif best["score"] >= _MEDIUM_THRESHOLD:
            # Medium — augment LLM with context
            context_parts = [r["text"] for r in results if r["score"] >= _MEDIUM_THRESHOLD]
            context = "\n---\n".join(context_parts)[:1000]  # Cap context at 1000 chars
            logger.info("rag_augment", source=best["source"], score=round(best["score"], 3))
            return {
                "action": "augment",
                "context": context,
                "source": best["source"],
                "score": best["score"],
            }
        else:
            # Low — LLM without context
            return {"action": "llm_only", "context": "", "source": ""}

    def get_stats(self) -> dict[str, Any]:
        return {
            "indexed_documents": len(self._index),
            "built": self._built,
            "knowledge_dir": str(self._knowledge_dir),
        }
