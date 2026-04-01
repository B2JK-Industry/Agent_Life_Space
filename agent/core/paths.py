"""
Agent Life Space — Centralized Project Root

Single source of truth for project root resolution.
All modules import get_project_root() instead of duplicating path logic.

Resolution order:
1. AGENT_PROJECT_ROOT env var (explicit, preferred for self-host)
2. Inferred from source tree (pyproject.toml marker)
3. Fail explicitly (no silent home directory fallback)
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_resolved_root: str = ""


def get_project_root() -> str:
    """Return project root from env or infer the checked-out repository root.

    Raises RuntimeError if no valid root can be determined.
    """
    global _resolved_root  # noqa: PLW0603
    if _resolved_root:
        return _resolved_root

    configured = os.environ.get("AGENT_PROJECT_ROOT", "")
    if configured:
        _resolved_root = configured
        return _resolved_root

    inferred_root = Path(__file__).resolve().parents[2]
    if (inferred_root / "pyproject.toml").exists():
        _resolved_root = str(inferred_root)
        os.environ.setdefault("AGENT_PROJECT_ROOT", _resolved_root)
        return _resolved_root

    # No silent fallback to ~/.agent-life-space — that hides deployment errors.
    # Self-host deployments must set AGENT_PROJECT_ROOT explicitly.
    raise RuntimeError(
        "Cannot determine project root. Set AGENT_PROJECT_ROOT environment variable "
        "or run from a directory containing pyproject.toml."
    )
