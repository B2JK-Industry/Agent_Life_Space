"""Schemas for InitiativeEngine.

Štruktúrované typy pre plán + kroky. Plánovač (LLM) vracia JSON, ktorý
sa tu validuje cez Pydantic — zabráni halucinovaným kľúčom a bezpečne
serializuje.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class StepKind(str, Enum):
    """Kategórie krokov — driver podľa nich routuje executor."""

    ANALYZE = "analyze"        # LLM analýza/decomposition (read-only)
    DESIGN = "design"          # LLM návrh architektúry/spec (read-only)
    CODE = "code"              # LLM píše kód (file-access)
    TEST = "test"              # spustí pytest/integration testy
    VERIFY = "verify"          # auto-review výstupu predošlého kroku
    DEPLOY = "deploy"          # commit + push (po schválení)
    SCHEDULE = "schedule"      # zaregistruje cron/recurring task
    MONITOR = "monitor"        # periodické sledovanie (long-running)
    NOTIFY = "notify"          # pošle správu majiteľovi (Telegram)
    APPROVAL = "approval"      # vyžaduje schválenie majiteľa pred ďalším krokom


# Kroky ktoré dotýkajú filesystému / vyžadujú nástroje
FILE_TOUCHING_KINDS = frozenset({StepKind.CODE, StepKind.TEST, StepKind.DEPLOY})

# Kroky ktoré vyžadujú schválenie pred exekúciou
APPROVAL_REQUIRED_KINDS = frozenset({StepKind.DEPLOY, StepKind.APPROVAL})


class PlannedStep(BaseModel):
    """Jeden krok v iniciatíve."""

    idx: int = Field(ge=0, description="Poradie v rámci plánu")
    kind: StepKind
    title: str = Field(min_length=3, max_length=200)
    prompt: str = Field(
        min_length=10,
        max_length=8000,
        description=(
            "Kompletný prompt pre LLM. Musí obsahovať kontext a presné acceptance "
            "criteria. Pre SCHEDULE/NOTIFY/MONITOR má strukturovany payload v "
            "metadata."
        ),
    )
    depends_on_idx: list[int] = Field(default_factory=list)
    estimated_minutes: int = Field(default=5, ge=1, le=240)
    requires_approval: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("depends_on_idx")
    @classmethod
    def _no_self_dep(cls, v: list[int], info: Any) -> list[int]:
        idx = info.data.get("idx")
        if idx is not None and idx in v:
            msg = f"Step {idx} cannot depend on itself"
            raise ValueError(msg)
        return v


class PatternRef(BaseModel):
    """Odkaz na pattern z knowledge base."""

    pattern_id: str = Field(min_length=2, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=500)


class InitiativePlan(BaseModel):
    """Štruktúrovaný plán vrátený plánovačom (LLM)."""

    goal_summary: str = Field(min_length=5, max_length=400)
    pattern: PatternRef
    success_criteria: list[str] = Field(min_length=1, max_length=25)
    steps: list[PlannedStep] = Field(min_length=1, max_length=40)
    estimated_total_minutes: int = Field(ge=1, le=10000)
    risk_notes: list[str] = Field(default_factory=list, max_length=20)
    is_long_running: bool = Field(
        default=False,
        description=(
            "True ak po dokončení posledného kroku má iniciatíva zostať v "
            "MONITORING režime (napr. recurring scraper)."
        ),
    )

    @field_validator("steps")
    @classmethod
    def _validate_step_order(cls, steps: list[PlannedStep]) -> list[PlannedStep]:
        seen_idx = set()
        for step in steps:
            if step.idx in seen_idx:
                msg = f"Duplicate step idx: {step.idx}"
                raise ValueError(msg)
            seen_idx.add(step.idx)
            for dep in step.depends_on_idx:
                if dep >= step.idx:
                    msg = (
                        f"Step {step.idx} depends on idx {dep} which is not strictly "
                        f"earlier — only forward dependencies allowed."
                    )
                    raise ValueError(msg)
                if dep not in seen_idx:
                    msg = f"Step {step.idx} depends on unknown idx {dep}"
                    raise ValueError(msg)
        return steps


class StepExecutionResult(BaseModel):
    """Výsledok jedného exekvovaného kroku."""

    success: bool
    summary: str = Field(default="", max_length=4000)
    artifact_paths: list[str] = Field(default_factory=list)
    error: str = Field(default="", max_length=2000)
    next_step_hint: str = Field(default="", max_length=400)
    metadata: dict[str, Any] = Field(default_factory=dict)
