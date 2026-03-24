"""
Agent Life Space — Model Router

Ktorý model na čo. Jedno miesto, ľahko rozšíriteľné.

Cascade: dispatcher (lokálne) → Haiku (lacný) → Sonnet (reasoning) → Opus (kód)

Cost odhad per request (s Max sub = $0, bez sub):
    Haiku:  ~$0.001 per odpoveď
    Sonnet: ~$0.01  per odpoveď
    Opus:   ~$0.05-0.20 per programovacia úloha
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
    """
    Classify user message → task type for model selection.

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

    # URL v texte → agent musí niečo prečítať/spracovať
    if "http://" in text_lower or "https://" in text_lower or "github.com" in text_lower:
        complexity_score += 2

    # Action verbs — agent má UROBIŤ niečo, nie len odpovedať
    _ACTION_VERBS = [
        "registruj", "zaregistruj", "prihlás", "vytvor účet",
        "nájdi", "vyhľadaj", "porovnaj", "analyzuj",
        "stiahni", "nainštaluj", "nastav", "nakonfiguruj",
        "preskúmaj", "prečítaj", "zisti", "over",
        "spusti", "otestuj", "skontroluj",
    ]
    if any(v in text_lower for v in _ACTION_VERBS):
        complexity_score += 2

    # Multi-step request (viacero viet alebo čiarky)
    if text.count(".") >= 2 or text.count(",") >= 2:
        complexity_score += 1

    # Dlhší text = pravdepodobne komplexnejšia úloha
    if len(words) > 15:
        complexity_score += 1

    # Otázka o schopnostiach ("vieš...?", "dokážeš...?")
    _CAPABILITY_VERBS = ["vieš", "dokážeš", "môžeš", "zvládneš", "umíš"]
    if any(v in text_lower for v in _CAPABILITY_VERBS):
        complexity_score += 1

    if complexity_score >= 3:
        return "programming"  # Opus — komplexné úlohy
    if complexity_score >= 1:
        return "analysis"  # Sonnet — stredná komplexita

    # === FACTUAL: krátka jednoduchá otázka ===
    if len(text_lower) < 30 and text_lower.endswith("?") and complexity_score == 0:
        return "factual"

    # Default: Sonnet
    return "chat"


def get_model(task_type: str) -> ModelConfig:
    """Vráť model config pre daný typ úlohy."""
    return _TASK_MODEL.get(task_type, SONNET)


def list_models() -> dict[str, str]:
    """Pre /models príkaz — prehľad."""
    return {task: cfg.model_id for task, cfg in _TASK_MODEL.items()}
