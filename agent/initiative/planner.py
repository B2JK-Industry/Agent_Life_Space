"""InitiativePlanner — z NL goalu vytvorí štruktúrovaný InitiativePlan.

Volá LLM (cez existujúci `agent.core.llm_provider`), výstup parsuje a
validuje cez Pydantic. Pri chybe parsing-u urobí 1 retry s explicitnou
chybovou hláškou.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from agent.initiative.prompts import PLANNER_SYSTEM_PROMPT, PLANNER_USER_TEMPLATE
from agent.initiative.schemas import InitiativePlan

logger = structlog.get_logger(__name__)


_PATTERNS_DIR = Path(__file__).parent / "patterns"
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def load_pattern_index() -> str:
    """Vráti markdown blok so súpisom dostupných patternov pre prompt."""
    index_path = _PATTERNS_DIR / "INDEX.md"
    if not index_path.exists():
        return "(žiadne patterny — použij vlastný úsudok)"
    return index_path.read_text(encoding="utf-8")


def load_pattern_detail(pattern_id: str) -> str:
    """Vráti obsah konkrétneho pattern .md (pre RAG injekciu pri exekúcii)."""
    safe = re.sub(r"[^a-zA-Z0-9_]", "", pattern_id).upper()
    path = _PATTERNS_DIR / f"{safe}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _extract_json(text: str) -> dict[str, Any]:
    """Vytiahni JSON z LLM odpovede — ošetri ```json fences aj raw JSON."""
    text = text.strip()
    # Try fenced first
    fenced = _JSON_FENCE_RE.search(text)
    if fenced:
        return json.loads(fenced.group(1))
    # Try raw — find first { and matching last }
    if text.startswith("{"):
        return json.loads(text)
    # Find first { — last }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    msg = "No JSON object found in planner response"
    raise ValueError(msg)


class InitiativePlanner:
    """Plánovač — NL → InitiativePlan."""

    def __init__(
        self,
        provider: Any,
        agent_name: str,
        owner_name: str,
        project_root: str,
        data_root: str,
        planner_model_id: str = "claude-sonnet-4-6",
        planner_max_turns: int = 1,
        planner_timeout: int = 120,
    ) -> None:
        self._provider = provider
        self._agent_name = agent_name
        self._owner_name = owner_name
        self._project_root = project_root
        self._data_root = data_root
        self._model_id = planner_model_id
        self._max_turns = planner_max_turns
        self._timeout = planner_timeout

    def _select_model(self, goal_nl: str) -> tuple[str, int, int]:
        """UltraPlan-style tier: krátke ciele → Sonnet, dlhé/explicit → Opus.

        Vracia (model_id, max_turns, timeout_s).
        """
        text = goal_nl.lower()
        ultraplan_signals = (
            "ultraplan",
            "ultra plan",
            "veľký projekt",
            "velky projekt",
            "long running",
            "dlhodob",
        )
        is_ultraplan = (
            len(goal_nl) >= 400
            or any(s in text for s in ultraplan_signals)
        )
        if is_ultraplan:
            # Opus tier — extended thinking, viac turnov
            return ("claude-opus-4-6", 2, 300)
        return (self._model_id, self._max_turns, self._timeout)

    async def plan(self, goal_nl: str, chat_id: int) -> InitiativePlan:
        """Vyrob plán z NL goalu. Raises ValueError pri opakovanom zlyhaní."""
        patterns_block = load_pattern_index()
        model_id, max_turns, timeout = self._select_model(goal_nl)
        user_prompt = PLANNER_USER_TEMPLATE.format(
            owner_name=self._owner_name,
            goal_nl=goal_nl[:4000],  # safety cap
            chat_id=chat_id,
            patterns_block=patterns_block,
            project_root=self._project_root,
            data_root=self._data_root,
        )

        attempts = 0
        last_error: str = ""
        while attempts < 2:
            attempts += 1
            full_prompt = (
                PLANNER_SYSTEM_PROMPT
                + "\n\n---\n\n"
                + user_prompt
                + (
                    f"\n\nPredošlý pokus zlyhal: {last_error}\nVráť opravený JSON."
                    if last_error
                    else ""
                )
            )

            from agent.core.llm_provider import GenerateRequest

            response = await self._provider.generate(
                GenerateRequest(
                    messages=[{"role": "user", "content": full_prompt}],
                    model=model_id,
                    max_turns=max_turns,
                    timeout=timeout,
                    allow_file_access=False,
                    cwd=self._project_root,
                )
            )

            if not response.success:
                last_error = (response.error or "")[:300]
                logger.warning(
                    "initiative_planner_llm_error",
                    attempt=attempts,
                    error=last_error,
                )
                continue

            try:
                raw = _extract_json(response.text or "")
                plan = InitiativePlan.model_validate(raw)
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:400]
                logger.warning(
                    "initiative_planner_parse_error",
                    attempt=attempts,
                    error=last_error,
                )
                continue

            logger.info(
                "initiative_planned",
                attempt=attempts,
                pattern=plan.pattern.pattern_id,
                steps=len(plan.steps),
                long_running=plan.is_long_running,
                model=model_id,
            )
            return plan

        msg = f"Planner failed after {attempts} attempts: {last_error}"
        raise ValueError(msg)
