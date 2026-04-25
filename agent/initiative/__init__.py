"""
Agent Life Space — Initiative Engine

Z prirodzenej reči (NL goal) vytvorí persistentný multi-step plán,
exekvuje ho autonómne na pozadí cez restart, hlási progress vlastníkovi.

Stavba nad existujúcimi modulmi:
    - agent.projects.ProjectManager  (long-running iniciatíva)
    - agent.tasks.TaskManager        (jednotlivé kroky s deps)
    - agent.core.AgentLoop           (vykonávač jednotlivých prompt-driven krokov)
    - agent.brain knowledge/         (pattern library — RAG injekcia)

Public API:
    InitiativeEngine.start_initiative(goal_nl, chat_id) -> Initiative
    InitiativeEngine.list_active() -> list[Initiative]
    InitiativeEngine.get_status(initiative_id) -> dict
    InitiativeEngine.pause(id) / resume(id) / cancel(id)

Spustenie driver-loopu cez agent.core.AgentCron._initiative_driver_loop.
"""

from agent.initiative.engine import InitiativeEngine
from agent.initiative.schemas import (
    InitiativePlan,
    PatternRef,
    PlannedStep,
    StepKind,
)

__all__ = [
    "InitiativeEngine",
    "InitiativePlan",
    "PlannedStep",
    "StepKind",
    "PatternRef",
]
