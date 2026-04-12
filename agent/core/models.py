"""
Agent Life Space — Model Router

Ktorý model na čo. Provider-agnostic, ľahko rozšíriteľné.

Cascade: dispatcher (lokálne) -> Haiku (lacný) -> Sonnet (reasoning) -> Opus (kód)

Model tiers namiesto hardcoded IDs:
    FAST     = cheap, quick (Haiku, GPT-4o-mini, llama3:8b)
    BALANCED = good quality (Sonnet, GPT-4o, llama3:70b)
    POWERFUL = best quality (Opus, o3, llama3:70b)

Classification:
    Multi-signal scoring with explicit weights.
    Each signal contributes independently — no keyword-only heuristics.
    Scoring config is externalized for testability.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from agent.control.llm_runtime import resolve_llm_runtime_state


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
    ModelTier.BALANCED: (5, 180),
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
    runtime = resolve_llm_runtime_state(environ=os.environ)
    provider = runtime["effective_provider"]

    models = PROVIDER_MODELS.get(provider, PROVIDER_MODELS["anthropic"])
    return models[tier]


_MODEL_ALIAS_TO_TIER: dict[str, ModelTier] = {
    model_id: tier
    for provider_models in PROVIDER_MODELS.values()
    for tier, model_id in provider_models.items()
}


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
    max_turns=5,
    timeout=180,
    tier=ModelTier.BALANCED,
)

OPUS = ModelConfig(
    model_id=PROVIDER_MODELS["anthropic"][ModelTier.POWERFUL],
    max_turns=15,
    timeout=300,
    tier=ModelTier.POWERFUL,
)


# ─────────────────────────────────────────────
# Classification — multi-signal scoring
# ─────────────────────────────────────────────

# Scoring config: externalized for testability and tuning.
# Each signal returns a score that is summed.
# Thresholds map total score → task type.

_PROGRAMMING_KEYWORDS = frozenset([
    # Unambiguously about code — safe to trigger programming alone (SK + EN)
    "naprogramuj", "implementuj", "napíš kód", "oprav bug",
    "vytvor modul", "refaktoruj", "fix bug", "uprav kód", "pridaj príkaz",
    "napíš test", "debug", "commitni", "pushni",
    "write code", "refactor", "fix bug", "add feature", "write test",
    "add command", "create module", "edit code", "code review",
])

# Technical terms — need 2+ matches OR combo with intent verb to trigger programming
_TECHNICAL_TERMS = frozenset([
    # Frameworks & protocols (language-neutral)
    "api", "rest", "fastapi", "flask", "django", "express", "endpoint",
    "microservice", "backend", "frontend", "databáza", "server",
    "database", "sqlite", "postgres", "redis", "mongodb", "docker",
    "pytest", "coverage", "middleware", "kubernetes", "nginx",
    "websocket", "graphql", "crud", "orm", "migration",
    "rate limit", "jwt", "oauth", "webhook", "cli",
    "typescript", "python", "javascript", "rust", "golang",
])

# General intent verbs — only boost score when combined with technical terms (SK + EN)
_IMPLEMENTATION_INTENTS = frozenset([
    # SK
    "potrebujem", "postav", "vytvor", "nasaď", "pridaj", "sprav",
    # EN
    "build", "implement", "deploy", "make", "create", "set up",
    "develop", "design", "write", "scaffold", "generate",
    "i need", "i want",
])

_SIMPLE_KEYWORDS = frozenset([
    # SK
    "ahoj", "čau", "ďakujem", "díky",
    "áno", "nie", "ok", "dobre", "jasné", "super",
    # EN
    "hello", "hi", "hey", "thanks", "thank you",
    "yes", "no", "ok", "sure", "got it", "great", "cool",
])

_ACTION_VERBS = [
    # SK — implementation/mutation verbs only
    "registruj", "zaregistruj", "registrovať", "prihlás", "prihlásiť", "vytvor účet",
    "nainštaluj", "nastav", "nakonfiguruj",
    # EN — implementation/mutation verbs only
    "register", "sign up", "create account",
    "download", "install", "configure", "set up",
    "run", "test",
]

# Analytical / inspection verbs — these should NOT push toward "programming".
# Questions like "over to ešte raz", "analyzuj výsledok", "skontroluj finding"
# are follow-ups, not code-generation requests.
_ANALYTICAL_VERBS = frozenset([
    # SK
    "over", "skontroluj", "preskúmaj", "analyzuj", "porovnaj",
    "prečítaj", "zisti", "vysvetli", "objasni", "zhrň", "sumarizuj",
    "nájdi", "vyhľadaj",
    # EN
    "find", "search", "compare", "analyze", "explore", "read",
    "check", "verify", "inspect", "scan", "explain", "summarize",
])

_CAPABILITY_VERBS = [
    # SK
    "vieš", "dokážeš", "môžeš", "zvládneš", "umíš",
    # EN
    "can you", "could you", "are you able", "do you know",
]

# Score thresholds
_THRESHOLD_PROGRAMMING = 5  # score >= 5 → programming
_THRESHOLD_ANALYSIS = 2     # score >= 2 → analysis


@dataclass
class ClassificationResult:
    """Explainable classification result."""

    task_type: str
    score: int
    signals: dict[str, int]  # signal_name → contribution


def classify_task_detailed(text: str) -> ClassificationResult:
    """
    Classify user message → task type with full explainability.
    Returns scores and signal breakdown for debugging/eval.
    """
    text_lower = text.lower().strip()
    words = text_lower.split()
    word_set = set(words)  # for O(1) whole-word matching
    signals: dict[str, int] = {}

    # === Code content early check (before simple) ===
    has_code = "```" in text or "def " in text or "import " in text or "class " in text

    # === SIMPLE: krátke jednoduché správy ===
    # Only if no code content and no programming keywords present
    if (
        len(words) <= 3
        and not has_code
        and any(kw in text_lower for kw in _SIMPLE_KEYWORDS)
        and not any(kw in text_lower for kw in _PROGRAMMING_KEYWORDS)
    ):
        return ClassificationResult(task_type="simple", score=0, signals={"simple_match": 1})

    # === Signal scoring ===
    total = 0

    # Programming keywords (strong signal — single match is enough)
    prog_matches = sum(1 for kw in _PROGRAMMING_KEYWORDS if kw in text_lower)
    if prog_matches:
        prog_score = max(5, prog_matches * 3)  # Ensure single keyword crosses threshold
        signals["programming_keywords"] = prog_score
        total += prog_score

    # URL presence
    if "http://" in text_lower or "https://" in text_lower or "github.com" in text_lower:
        signals["url_present"] = 2
        total += 2

    # Action verbs (implementation/mutation only)
    # Use word_set for single words, text_lower for multi-word phrases.
    action_matches = sum(
        1 for v in _ACTION_VERBS
        if (v in word_set if " " not in v else v in text_lower)
    )
    if action_matches:
        signals["action_verbs"] = action_matches * 2
        total += action_matches * 2

    # Analytical verbs — contribute to analysis threshold but capped
    # by the analytical guard from crossing into programming.
    # Word-boundary matching prevents "read" in "thread", "find" in "finding".
    analytical_matches = sum(1 for v in _ANALYTICAL_VERBS if v in word_set)
    if analytical_matches:
        signals["analytical_verbs"] = analytical_matches * 2
        total += analytical_matches * 2

    # Structural complexity
    punctuation_score = 0
    if text.count(".") >= 2 or text.count(",") >= 2:
        punctuation_score = 1
    if len(words) > 15:
        punctuation_score += 1
    if punctuation_score:
        signals["structural_complexity"] = punctuation_score
        total += punctuation_score

    # Capability questions (multi-word phrases → text_lower match)
    cap_matches = sum(1 for v in _CAPABILITY_VERBS if v in text_lower)
    if cap_matches:
        signals["capability_question"] = cap_matches
        total += cap_matches

    # Analytical verb guard check (used in threshold decision below)
    has_analytical = analytical_matches > 0

    # Code-like content (backticks, indentation, function calls)
    if has_code:
        signals["code_content"] = 5
        total += 5

    # Technical terms (API, framework, database, etc.)
    tech_matches = sum(1 for t in _TECHNICAL_TERMS if t in text_lower)
    has_intent = any(v in text_lower for v in _IMPLEMENTATION_INTENTS)

    if tech_matches >= 2:
        tech_score = min(tech_matches * 2, 6)
        signals["technical_terms"] = tech_score
        total += tech_score

    # Intent verb + technical term combo (e.g. "potrebujem API" → programming)
    if has_intent and tech_matches >= 1:
        signals["intent_plus_tech"] = 5
        total += 5

    # Analytical verb guard (has_analytical computed above from word_set)
    has_unambiguous_prog = bool(signals.get("programming_keywords"))

    # === Thresholds ===
    if total >= _THRESHOLD_PROGRAMMING and not (has_analytical and not has_unambiguous_prog):
        task_type = "programming"
    elif total >= _THRESHOLD_ANALYSIS:
        task_type = "analysis"
    elif len(text_lower) < 30 and text_lower.endswith("?") and total == 0:
        task_type = "factual"
    else:
        task_type = "chat"

    return ClassificationResult(task_type=task_type, score=total, signals=signals)


def classify_task(text: str) -> str:
    """Classify user message → task type for model selection."""
    return classify_task_detailed(text).task_type


def get_model(task_type: str) -> ModelConfig:
    """Get model config for task type. Resolves through provider-agnostic tier."""
    tier = _TASK_TIER.get(task_type, ModelTier.BALANCED)
    return get_model_for_tier(tier)


def get_model_for_tier(tier: ModelTier) -> ModelConfig:
    """Resolve a provider-agnostic tier into the current runtime model."""
    model_id = _resolve_model_id(tier)
    max_turns, timeout = _TIER_DEFAULTS[tier]
    return ModelConfig(model_id=model_id, max_turns=max_turns, timeout=timeout, tier=tier)


def resolve_runtime_model_alias(model_id: str) -> ModelConfig | None:
    """Map a canonical model id onto the equivalent tier for the current provider."""
    tier = _MODEL_ALIAS_TO_TIER.get(str(model_id or "").strip())
    if tier is None:
        return None
    return get_model_for_tier(tier)


def list_models() -> dict[str, str]:
    """Pre /models príkaz — prehľad."""
    return {task: get_model(task).model_id for task in _TASK_TIER}
