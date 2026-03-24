"""
Agent Life Space — Model Router

Ktorý model na čo. Jedno miesto, ľahko rozšíriteľné.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    model_id: str
    max_turns: int
    timeout: int  # seconds


# --- Model definitions ---

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

HAIKU = ModelConfig(
    model_id="claude-haiku-4-5-20251001",
    max_turns=1,
    timeout=60,
)


# --- Task → Model mapping ---

_TASK_MODEL: dict[str, ModelConfig] = {
    "chat": SONNET,           # bežná konverzácia
    "programming": OPUS,      # kódenie, refaktoring, debugovanie
    "work_queue": SONNET,     # úlohy z fronty
    "analysis": OPUS,         # hlboká analýza, research
    "simple_question": HAIKU, # áno/nie, fakty, rýchle odpovede
}


def get_model(task_type: str) -> ModelConfig:
    """Vráť model config pre daný typ úlohy."""
    return _TASK_MODEL.get(task_type, SONNET)


def list_models() -> dict[str, str]:
    """Pre /models príkaz — prehľad."""
    return {task: cfg.model_id for task, cfg in _TASK_MODEL.items()}
