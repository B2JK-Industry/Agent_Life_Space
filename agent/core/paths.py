"""
Agent Life Space — Centralized Project Root

Single source of truth for project root resolution.
All modules import get_project_root() instead of duplicating
the env var lookup with a home directory fallback.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_PROJECT_DIR = "agent-life-space"


def get_project_root() -> str:
    """Return project root from env or infer the checked-out repository root."""
    configured = os.environ.get("AGENT_PROJECT_ROOT", "")
    if configured:
        return configured
    inferred_root = Path(__file__).resolve().parents[2]
    if (inferred_root / "pyproject.toml").exists():
        resolved = str(inferred_root)
        os.environ.setdefault("AGENT_PROJECT_ROOT", resolved)
        return resolved
    return str(Path.home() / _DEFAULT_PROJECT_DIR)
