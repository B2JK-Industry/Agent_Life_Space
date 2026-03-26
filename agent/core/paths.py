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
    """Return project root path from AGENT_PROJECT_ROOT env var or home directory default."""
    return os.environ.get("AGENT_PROJECT_ROOT", str(Path.home() / _DEFAULT_PROJECT_DIR))
