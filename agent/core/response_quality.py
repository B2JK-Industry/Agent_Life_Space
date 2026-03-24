"""
Agent Life Space — Response Quality & Post-Routing

Hodnotí kvalitu LLM odpovede a rozhoduje o eskalácii.

Pattern (z RouteLLM research):
    1. Pošli na lacný model (Haiku)
    2. Vyhodnoť odpoveď — je dostatočná?
    3. Ak nie → eskaluj na silnejší model (Sonnet)

Signály kvality:
    - Dĺžka odpovede vs komplexita otázky
    - Prítomnosť "neviem" / "nerozumiem" / refusal
    - Prázdna alebo generická odpoveď
    - Chybové hlášky v odpovedi
    - Odpoveď je len echo otázky

Toto NIE JE keyword matching na vstupe.
Toto JE hodnotenie VÝSTUPU po tom čo model odpovedal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class QualityAssessment:
    """Výsledok hodnotenia kvality odpovede."""

    score: float  # 0.0 (veľmi zlá) — 1.0 (výborná)
    should_escalate: bool
    reason: str
    signals: list[str]


# Signály nízkej kvality — ak sa nájdu v odpovedi
_REFUSAL_SIGNALS = [
    "neviem", "nepoznám", "nemám informácie", "nerozumiem",
    "nemôžem", "nedokážem", "nie som schopný",
    "i don't know", "i can't", "i'm not sure",
    "nemám prístup", "nemám k dispozícii",
]

_GENERIC_SIGNALS = [
    "skús to inak", "zopakuj otázku", "upresni",
    "môžeš mi dať viac detailov",
    "nedostal som výsledok",
]

_ERROR_SIGNALS = [
    "chyba:", "error:", "exception:", "traceback",
    "nepodarilo sa", "zlyhalo",
]


def assess_quality(
    question: str,
    answer: str,
    model_used: str = "",
) -> QualityAssessment:
    """
    Vyhodnoť kvalitu odpovede. Deterministické — žiadne LLM volanie.

    Returns QualityAssessment s score a rozhodnutím o eskalácii.
    """
    signals: list[str] = []
    score = 1.0  # Začni s max, odpočítavaj za problémy

    answer_lower = answer.lower()
    question_lower = question.lower()
    question_words = len(question.split())
    answer_words = len(answer.split())

    # --- Signal 0: Prázdna odpoveď ---
    if answer_words == 0:
        score -= 0.6
        signals.append("empty")

    # --- Signal 1: Prázdna alebo veľmi krátka odpoveď na komplexnú otázku ---
    if 0 < answer_words < 5 and question_words > 5:
        score -= 0.4
        signals.append("too_short")

    # --- Signal 2: Refusal / "neviem" ---
    refusal_count = sum(1 for s in _REFUSAL_SIGNALS if s in answer_lower)
    if refusal_count > 0:
        score -= 0.3 * min(refusal_count, 2)
        signals.append(f"refusal({refusal_count})")

    # --- Signal 3: Generická "skús to inak" odpoveď ---
    generic_count = sum(1 for s in _GENERIC_SIGNALS if s in answer_lower)
    if generic_count > 0:
        score -= 0.3 * min(generic_count, 2)
        signals.append("generic_response")

    # --- Signal 4: Error v odpovedi ---
    error_count = sum(1 for s in _ERROR_SIGNALS if s in answer_lower)
    if error_count > 0:
        score -= 0.2
        signals.append(f"error_in_response({error_count})")

    # --- Signal 5: Odpoveď je echo otázky ---
    # Ak >50% slov z otázky sa opakuje v odpovedi a odpoveď je krátka
    if question_words > 3 and answer_words < question_words * 3:
        q_words = set(w for w in question_lower.split() if len(w) > 2)
        a_words = set(w for w in answer_lower.split() if len(w) > 2)
        if q_words:
            overlap = len(q_words & a_words) / len(q_words)
            if overlap > 0.5:
                score -= 0.3
                signals.append("echo_question")

    # --- Signal 6: Odpoveď len odkazuje inam bez obsahu ---
    redirect_patterns = [
        r"pozri\s+sa\s+na", r"odporúčam\s+pozrieť",
        r"nájdeš\s+na", r"viac\s+info\s+na",
    ]
    if any(re.search(p, answer_lower) for p in redirect_patterns):
        if answer_words < 30:
            score -= 0.15
            signals.append("redirect_only")

    # Clamp score
    score = max(0.0, min(1.0, score))

    # Rozhodnutie o eskalácii
    # Eskaluj ak: score < 0.5 AND model je Haiku (lacný)
    should_escalate = False
    reason = ""

    if score < 0.5:
        if "haiku" in model_used.lower():
            should_escalate = True
            reason = f"Haiku odpoveď nízka kvalita ({score:.1f}): {', '.join(signals)}. Eskalujem na Sonnet."
        elif "sonnet" in model_used.lower():
            # Sonnet tiež zlyhal — ale neeskalujeme na Opus cez API
            # (Daniel nechce API, Opus je len cez CLI pre programming)
            should_escalate = False
            reason = f"Sonnet odpoveď nízka kvalita ({score:.1f}): {', '.join(signals)}. Nemožno eskalovať ďalej."

    if should_escalate:
        logger.info(
            "quality_escalation",
            score=round(score, 2),
            signals=signals,
            from_model=model_used,
        )

    return QualityAssessment(
        score=round(score, 2),
        should_escalate=should_escalate,
        reason=reason,
        signals=signals,
    )
