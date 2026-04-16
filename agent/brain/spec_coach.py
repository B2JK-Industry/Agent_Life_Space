"""
Agent Life Space — Build Spec Coach

Helps users write better build specs. Two functions:

1. `score_spec_quality(description)` — fast heuristic (no LLM) that flags
   obviously vague descriptions. Used as a safety gate in /build.

2. `coach_spec(idea, language)` — LLM-powered spec generator that turns
   a vague idea into a structured spec the user can review and submit.
   Used by the explicit /spec command.

Why two layers:
- Heuristic is cheap, runs on every /build, catches "build api" disasters.
- LLM coach is opt-in via /spec, produces high-quality specs from ideas.

Design choices:
- Heuristic uses static signals only (length, nouns, verbs, fuzzy words).
  No semantic analysis — that's the LLM coach's job.
- Coach returns markdown the user reads, not JSON the build pipeline parses.
  Keeps it human-friendly; user can edit before submitting.
- Coach response includes a ready-to-paste /build command suggestion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────
# Heuristic spec quality scoring (no LLM)
# ─────────────────────────────────────────────


@dataclass
class SpecQuality:
    """Fast heuristic assessment of build spec quality."""

    score: float  # 0.0 (terrible) to 1.0 (great)
    issues: list[str]  # human-readable problems
    is_too_vague: bool  # True if score < 0.4 — recommend /spec coaching

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "issues": self.issues,
            "is_too_vague": self.is_too_vague,
        }


# Words that signal vagueness when they appear without specifics.
# "build a nice tool" → fuzzy. "tool that converts CSV to JSON" → specific.
_FUZZY_WORDS = frozenset({
    "nice", "good", "simple", "basic", "easy", "cool",
    "something", "stuff", "things", "etc",
    "perfect", "awesome", "great", "amazing",
})

# Concrete nouns that increase confidence (signals user knows what they want).
_CONCRETE_HINTS = frozenset({
    "function", "class", "endpoint", "api", "cli", "script",
    "input", "output", "argument", "parameter", "return",
    "test", "pytest", "unittest", "assertion",
    "json", "csv", "yaml", "xml", "sqlite", "database",
    "file", "directory", "stdin", "stdout",
    "http", "post", "get", "put", "delete",
    "regex", "parser", "validator",
})

# Acceptance/success signals — user thought about how to verify it works.
_ACCEPTANCE_SIGNALS = frozenset({
    "must", "should", "expects", "returns", "raises",
    "passes", "fails", "verify", "validate", "check",
    "test", "tests", "coverage", "edge case", "edge cases",
    "acceptance", "criteria", "success",
})


def score_spec_quality(description: str) -> SpecQuality:
    """Score how well-formed a build spec is, without calling an LLM.

    Heuristic signals:
    - Length: too short = bad
    - Concrete nouns: function/api/cli/etc → good
    - Acceptance signals: must/should/expects/etc → good
    - Fuzzy words without specifics: nice/cool/etc → bad
    - Examples or input/output: → good
    """
    text = (description or "").strip()
    issues: list[str] = []

    if not text:
        return SpecQuality(score=0.0, issues=["empty description"], is_too_vague=True)

    word_count = len(text.split())
    lower = text.lower()

    # Score components (each contributes 0.0-0.25)
    length_score = min(0.25, word_count / 80.0)  # 80 words = full credit
    if word_count < 8:
        issues.append("description is very short — explain what should it do")

    concrete_hits = sum(1 for w in _CONCRETE_HINTS if w in lower)
    concrete_score = min(0.25, concrete_hits * 0.05)
    if concrete_hits == 0:
        issues.append("no concrete tech terms (function, api, cli, json, ...) — be specific")

    acceptance_hits = sum(1 for w in _ACCEPTANCE_SIGNALS if w in lower)
    acceptance_score = min(0.25, acceptance_hits * 0.06)
    if acceptance_hits == 0:
        issues.append("no success criteria — how will we know it works?")

    # Bonus for examples / input-output ("input X → output Y", "given ..., return ...")
    has_io_example = bool(re.search(r"(→|->|=>|returns?|expects?|input.*output)", lower))
    io_score = 0.15 if has_io_example else 0.0
    if not has_io_example:
        issues.append("no input/output examples — describe what data flows through")

    # Penalty for fuzzy words
    fuzzy_hits = sum(1 for w in _FUZZY_WORDS if w in lower.split())
    fuzzy_penalty = min(0.20, fuzzy_hits * 0.05)
    if fuzzy_hits >= 2:
        issues.append(f"vague words ({fuzzy_hits}× nice/good/simple/...) — replace with specifics")

    # Bonus for tests being mentioned (10pts)
    has_tests = "test" in lower or "pytest" in lower
    test_bonus = 0.10 if has_tests else 0.0

    score = length_score + concrete_score + acceptance_score + io_score + test_bonus - fuzzy_penalty
    score = max(0.0, min(1.0, score))

    return SpecQuality(
        score=score,
        issues=issues,
        is_too_vague=score < 0.4,
    )


# ─────────────────────────────────────────────
# LLM-powered spec generation (/spec command)
# ─────────────────────────────────────────────


_COACH_SYSTEM_PROMPT_SK = """\
Si pomocník pre developera, ktorý chce spustiť automatický build (codegen + testy).
Tvoja úloha: vziať jeho VOĽNÚ predstavu o tom čo chce a premeniť ju na konkrétny spec.

Výstupný formát (markdown):

## {title v slovenčine}

**Funkčnosť:**
- konkrétny bod 1 (čo to robí, aké vstupy, aké výstupy)
- konkrétny bod 2
- ...

**Acceptance (ako overíme že to funguje):**
- konkrétny test 1
- konkrétny test 2
- ...

**Tech:**
- jazyk a knižnice
- žiadne externé dependencies pokiaľ to nie je nevyhnutné

**Pripravený príkaz:**
```
/build {cesta} --description "{jedno-vetový popis ktorý obsahuje všetko podstatné}"
```

Pravidlá:
- Buď CONCRETE. Žiadne "nice", "simple", "robust" — popíš čo to skutočne robí.
- Vždy navrhni edge cases (empty input, invalid input, big input).
- Ak je predstava priveľa široká, OBSAH sa zamerať na MVP (minimum viable product).
- Pripravený príkaz musí mať description ktorý dáva codegen LLM dosť informácií na napísanie kódu BEZ ďalších otázok.
- Output je číra markdown — žiadny JSON, žiadne markdown bloky okrem toho jedného s príkazom.
"""


_COACH_SYSTEM_PROMPT_EN = """\
You are an assistant helping a developer prepare a spec for an automated build
pipeline (codegen + tests). Your job: take a vague idea and turn it into a
concrete spec.

Output format (markdown):

## {Title}

**What it does:**
- concrete bullet 1 (what it does, what inputs, what outputs)
- concrete bullet 2
- ...

**Acceptance (how we verify it works):**
- concrete test 1
- concrete test 2
- ...

**Tech:**
- language and libraries
- avoid external dependencies unless strictly needed

**Ready-to-run command:**
```
/build {path} --description "{single-sentence description with everything essential}"
```

Rules:
- Be CONCRETE. No "nice", "simple", "robust" — describe what it actually does.
- Always propose edge cases (empty input, invalid input, big input).
- If idea is too broad, scope it down to MVP.
- The ready-to-run description must give codegen enough info to write code WITHOUT further questions.
- Output is plain markdown — no JSON, no markdown blocks except the command one.
"""


async def coach_spec(
    idea: str,
    *,
    target_path: str = ".",
    language: str = "sk",
    timeout: int = 60,
) -> dict[str, Any]:
    """Turn a vague idea into a structured build spec via LLM.

    Returns:
        {"ok": True, "spec_markdown": str, "cost_usd": float, "model": str}
        {"ok": False, "error": str}
    """
    from agent.core.llm_provider import GenerateRequest, get_provider
    from agent.core.models import get_model

    provider = get_provider()
    model = get_model("reasoning").model_id

    system_prompt = (
        _COACH_SYSTEM_PROMPT_SK if language.lower().startswith("sk")
        else _COACH_SYSTEM_PROMPT_EN
    )

    user_prompt = (
        f"Užívateľ chce postaviť projekt v ceste `{target_path}`.\n"
        f"Jeho voľný popis:\n\n{idea.strip()}\n\n"
        f"Vygeneruj structured spec podľa formátu vyššie."
        if language.lower().startswith("sk") else
        f"User wants to build a project in path `{target_path}`.\n"
        f"Their loose description:\n\n{idea.strip()}\n\n"
        f"Generate a structured spec following the format above."
    )

    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    logger.info("spec_coach_request", target_path=target_path, language=language,
                idea_length=len(idea), model=model)

    try:
        response = await provider.generate(GenerateRequest(
            messages=[{"role": "user", "content": full_prompt}],
            model=model,
            timeout=timeout,
            max_turns=1,
        ))
    except Exception as exc:
        logger.exception("spec_coach_llm_failed")
        return {"ok": False, "error": f"LLM call failed: {type(exc).__name__}: {exc}"[:200]}

    if not response.success:
        logger.warning("spec_coach_llm_unsuccessful", error=response.error[:200])
        return {"ok": False, "error": response.error[:200] or "unknown LLM error"}

    text = (response.text or "").strip()
    if not text:
        return {"ok": False, "error": "LLM returned empty spec"}

    logger.info("spec_coach_complete",
                model=model, cost_usd=round(response.cost_usd, 4),
                output_length=len(text))

    return {
        "ok": True,
        "spec_markdown": text,
        "cost_usd": round(response.cost_usd, 4),
        "model": model,
    }
