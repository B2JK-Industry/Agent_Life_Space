"""
Agent Life Space — Model Router

Ktorý model na čo. Provider-agnostic, ľahko rozšíriteľné.

Cascade: dispatcher (lokálne) -> Haiku (lacný) -> Sonnet (reasoning) -> Opus (kód)

Model tiers namiesto hardcoded IDs:
    FAST     = cheap, quick (Haiku, GPT-4o-mini, llama3:8b)
    BALANCED = good quality (Sonnet, GPT-4o, llama3:70b)
    POWERFUL = best quality (Opus, o3, llama3:70b)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class ModelTier(str, Enum):
    """Provider-agnostic model quality tier."""

    FAST = "fast"
    BALANCED = "balanced"
    POWERFUL = "powerful"


# Provider -> tier -> model ID mapping
PROVIDER_MODELS: dict[str, dict[ModelTier, str]] = {
    "anthropic": {
        ModelTier.FAST: "claude-haiku-4-5-20251001",
        ModelTier.BALANCED: "claude-sonnet-4-6",
        ModelTier.POWERFUL: "claude-opus-4-6",
    },
    "openai": {
        ModelTier.FAST: "gpt-4o-mini",
        ModelTier.BALANCED: "gpt-4o",
        ModelTier.POWERFUL: "o3",
    },
    "local": {
        ModelTier.FAST: "llama3:8b",
        ModelTier.BALANCED: "llama3:70b",
        ModelTier.POWERFUL: "llama3:70b",
    },
}

# Tier -> (max_turns, timeout) defaults
_TIER_DEFAULTS: dict[ModelTier, tuple[int, int]] = {
    ModelTier.FAST: (1, 60),
    ModelTier.BALANCED: (3, 180),
    ModelTier.POWERFUL: (15, 300),
}


@dataclass(frozen=True)
class ModelConfig:
    model_id: str
    max_turns: int
    timeout: int  # seconds
    tier: ModelTier = ModelTier.BALANCED


def _resolve_model_id(tier: ModelTier) -> str:
    """Resolve tier to model ID based on current provider config."""
    backend = os.environ.get("LLM_BACKEND", "cli")
    provider = os.environ.get("LLM_PROVIDER", "anthropic")

    # CLI backend always uses Anthropic models
    if backend == "cli":
        provider = "anthropic"

    models = PROVIDER_MODELS.get(provider, PROVIDER_MODELS["anthropic"])
    return models[tier]


# --- Task -> Tier mapping ---

_TASK_TIER: dict[str, ModelTier] = {
    # Haiku-tier: jednoduché otázky, krátke odpovede
    "simple": ModelTier.FAST,
    "greeting": ModelTier.FAST,
    "factual": ModelTier.FAST,

    # Sonnet-tier: konverzácia, reasoning, analýza
    "chat": ModelTier.BALANCED,
    "analysis": ModelTier.BALANCED,
    "work_queue": ModelTier.BALANCED,

    # Opus-tier: len programovanie (čítanie/písanie súborov, testy, git)
    "programming": ModelTier.POWERFUL,
}

# Backward-compatible constants (used in existing tests)
HAIKU = ModelConfig(
    model_id=PROVIDER_MODELS["anthropic"][ModelTier.FAST],
    max_turns=1,
    timeout=60,
    tier=ModelTier.FAST,
)

SONNET = ModelConfig(
    model_id=PROVIDER_MODELS["anthropic"][ModelTier.BALANCED],
    max_turns=3,
    timeout=180,
    tier=ModelTier.BALANCED,
)

OPUS = ModelConfig(
    model_id=PROVIDER_MODELS["anthropic"][ModelTier.POWERFUL],
    max_turns=15,
    timeout=300,
    tier=ModelTier.POWERFUL,
)


# Classify text -> task type
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
    """
    Classify user message -> task type for model selection.

    Multi-signal scoring — nie len keyword match.
    Signály: keywords, complexity, URL, action verbs, dĺžka.
    """
    text_lower = text.lower().strip()
    words = text_lower.split()

    # === SIMPLE: krátke jednoduché správy ===
    if len(words) <= 3 and any(kw in text_lower for kw in _SIMPLE_KEYWORDS):
        return "simple"

    # === PROGRAMMING: explicitné programovacie kľúčové slová ===
    if any(kw in text_lower for kw in _PROGRAMMING_KEYWORDS):
        return "programming"

    # === COMPLEX: signály že úloha vyžaduje hlbšie premýšľanie ===
    complexity_score = 0

    if "http://" in text_lower or "https://" in text_lower or "github.com" in text_lower:
        complexity_score += 2

    action_verbs = [
        "registruj", "zaregistruj", "prihlás", "vytvor účet",
        "nájdi", "vyhľadaj", "porovnaj", "analyzuj",
        "stiahni", "nainštaluj", "nastav", "nakonfiguruj",
        "preskúmaj", "prečítaj", "zisti", "over",
        "spusti", "otestuj", "skontroluj",
    ]
    if any(v in text_lower for v in action_verbs):
        complexity_score += 2

    if text.count(".") >= 2 or text.count(",") >= 2:
        complexity_score += 1

    if len(words) > 15:
        complexity_score += 1

    capability_verbs = ["vieš", "dokážeš", "môžeš", "zvládneš", "umíš"]
    if any(v in text_lower for v in capability_verbs):
        complexity_score += 1

    if complexity_score >= 3:
        return "programming"
    if complexity_score >= 1:
        return "analysis"

    # === FACTUAL: krátka jednoduchá otázka ===
    if len(text_lower) < 30 and text_lower.endswith("?") and complexity_score == 0:
        return "factual"

    return "chat"


def get_model(task_type: str) -> ModelConfig:
    """Get model config for task type. Resolves through provider-agnostic tier."""
    tier = _TASK_TIER.get(task_type, ModelTier.BALANCED)
    model_id = _resolve_model_id(tier)
    max_turns, timeout = _TIER_DEFAULTS[tier]
    return ModelConfig(model_id=model_id, max_turns=max_turns, timeout=timeout, tier=tier)


def list_models() -> dict[str, str]:
    """Pre /models príkaz — prehľad."""
    return {task: get_model(task).model_id for task in _TASK_TIER}
