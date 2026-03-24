"""
Agent Life Space — Semantic Router

Intent detection cez embeddings namiesto keyword matching.
Používa sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
— podporuje slovenčinu, 470MB RAM, beží lokálne.

Flow:
    1. Pri štarte sa zakódujú intent vektory (jednorazovo)
    2. Správa od Daniela sa zakóduje (1ms)
    3. Cosine similarity → najlepší intent
    4. Ak confidence > threshold → dispatch interne
    5. Ak nie → posielaj na LLM

Intenty:
    - status: stav agenta
    - health: zdravie servera
    - tasks: úlohy
    - skills: schopnosti
    - budget: rozpočet
    - identity: kto som
    - greeting: pozdrav
    - programming: kódenie
    - question: všeobecná otázka → LLM
"""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Intent definitions: name → example phrases (used to build intent vectors)
_INTENTS: dict[str, list[str]] = {
    "status": [
        "aký je tvoj stav", "ako sa máš", "bežíš", "status",
        "aký je stav agenta", "si v poriadku", "funguje všetko",
    ],
    "health": [
        "zdravie servera", "koľko CPU", "koľko RAM", "disk",
        "zdravie", "health check", "systémové zdravie",
    ],
    "tasks": [
        "aké máš úlohy", "čo robíš", "fronta úloh", "tasks",
        "čo máš v rade", "pracovná fronta",
    ],
    "skills": [
        "aké skills máš", "čo vieš robiť", "schopnosti",
        "aké skills ovládaš", "čo si sa naučil",
    ],
    "budget": [
        "rozpočet", "koľko peňazí", "financie", "budget",
        "výdavky a príjmy", "finančný stav",
    ],
    "identity": [
        "kto si", "povedz o sebe", "identita",
        "kto si ty", "aký si agent",
    ],
    "greeting": [
        "ahoj", "čau", "hello", "hi", "dobré ráno",
        "dobrý deň", "nazdar",
    ],
    "programming": [
        "naprogramuj", "implementuj", "napíš kód",
        "oprav bug", "refaktoruj", "vytvor modul",
        "pridaj funkciu", "debug", "napíš test",
    ],
}

# Threshold for confident match
_CONFIDENCE_THRESHOLD = 0.55

# Singleton model instance (loaded lazily)
_model = None
_intent_embeddings: dict[str, Any] = {}


def _load_model() -> Any:
    """Lazy-load the embedding model."""
    global _model
    if _model is not None:
        return _model

    try:
        from sentence_transformers import SentenceTransformer
        model_name = "paraphrase-multilingual-MiniLM-L12-v2"

        # Check if model is cached locally
        cache_dir = os.path.expanduser("~/.cache/sentence-transformers")
        _model = SentenceTransformer(model_name, cache_folder=cache_dir)
        logger.info("semantic_model_loaded", model=model_name)
        return _model
    except ImportError:
        logger.warning("sentence_transformers_not_installed")
        return None
    except Exception as e:
        logger.error("semantic_model_error", error=str(e))
        return None


def _get_intent_embeddings() -> dict[str, Any]:
    """Compute intent embeddings (once, then cached)."""
    global _intent_embeddings
    if _intent_embeddings:
        return _intent_embeddings

    model = _load_model()
    if model is None:
        return {}

    import numpy as np

    for intent_name, phrases in _INTENTS.items():
        # Average embedding of all example phrases
        embeddings = model.encode(phrases, convert_to_numpy=True)
        _intent_embeddings[intent_name] = np.mean(embeddings, axis=0)

    logger.info("intent_embeddings_computed", count=len(_intent_embeddings))
    return _intent_embeddings


def classify_intent(text: str) -> tuple[str, float]:
    """
    Classify user text to an intent using semantic similarity.
    Returns (intent_name, confidence).
    Falls back to ("unknown", 0.0) if model not available.
    """
    model = _load_model()
    if model is None:
        return ("unknown", 0.0)

    intent_embs = _get_intent_embeddings()
    if not intent_embs:
        return ("unknown", 0.0)

    import numpy as np

    # Encode user text
    text_emb = model.encode([text], convert_to_numpy=True)[0]

    # Cosine similarity with each intent
    best_intent = "unknown"
    best_score = 0.0

    for intent_name, intent_emb in intent_embs.items():
        # Cosine similarity
        dot = np.dot(text_emb, intent_emb)
        norm = np.linalg.norm(text_emb) * np.linalg.norm(intent_emb)
        if norm > 0:
            similarity = float(dot / norm)
            if similarity > best_score:
                best_score = similarity
                best_intent = intent_name

    logger.info(
        "semantic_classify",
        text=text[:50],
        intent=best_intent,
        confidence=round(best_score, 3),
    )
    return (best_intent, best_score)


def is_available() -> bool:
    """Check if semantic router is available (model installed)."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False
