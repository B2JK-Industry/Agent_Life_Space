"""
Agent Life Space — Model Router

Ktorý model na čo. Jedno miesto, ľahko rozšíriteľné.

Cascade: dispatcher (0 tokens) → Haiku (lacný) → Sonnet (reasoning) → Opus (kód)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    model_id: str
    max_turns: int
    timeout: int  # seconds


# --- Model definitions ---

HAIKU = ModelConfig(
    model_id="claude-haiku-4-5-20251001",
    max_turns=1,
    timeout=60,
)

SONNET = ModelConfig(
    model_id="claude-sonnet-4-6",
    max_turns=3,
    timeout=180,
)

OPUS = ModelConfig(
    model_id="claude-opus-4-6",
    max_turns=15,
    timeout=300,
)


# --- Task → Model mapping ---

_TASK_MODEL: dict[str, ModelConfig] = {
    # Zero tokens (handled by dispatcher)
    # "status", "health", "tasks", "skills", "budget", "identity"

    # Haiku — jednoduché otázky, krátke odpovede, klasifikácia
    "simple": HAIKU,
    "greeting": HAIKU,
    "factual": HAIKU,

    # Sonnet — konverzácia, reasoning, analýza
    "chat": SONNET,
    "analysis": SONNET,
    "work_queue": SONNET,

    # Opus — len programovanie (čítanie/písanie súborov, testy, git)
    "programming": OPUS,
}

# Classify text → task type
_PROGRAMMING_KEYWORDS = frozenset([
    "naprogramuj", "implementuj", "napíš kód", "pridaj", "oprav bug",
    "vytvor modul", "refaktoruj", "fix", "uprav kód", "pridaj príkaz",
    "napíš test", "debug", "commitni", "pushni",
])

_SIMPLE_KEYWORDS = frozenset([
    "ahoj", "čau", "hello", "hi", "ďakujem", "díky", "thanks",
    "áno", "nie", "ok", "dobre", "jasné", "super",
])


def classify_task(text: str) -> str:
    """Classify user message → task type for model selection."""
    text_lower = text.lower().strip()

    # Programming?
    if any(kw in text_lower for kw in _PROGRAMMING_KEYWORDS):
        return "programming"

    # Simple greeting or short response? (exact match for short inputs)
    words = text_lower.split()
    if len(words) <= 3 and any(kw in text_lower for kw in _SIMPLE_KEYWORDS):
        return "simple"

    # Short factual question?
    if len(text_lower) < 40 and text_lower.endswith("?"):
        return "factual"

    # Default: Sonnet for general conversation
    return "chat"


def get_model(task_type: str) -> ModelConfig:
    """Vráť model config pre daný typ úlohy."""
    return _TASK_MODEL.get(task_type, SONNET)


def list_models() -> dict[str, str]:
    """Pre /models príkaz — prehľad."""
    return {task: cfg.model_id for task, cfg in _TASK_MODEL.items()}
