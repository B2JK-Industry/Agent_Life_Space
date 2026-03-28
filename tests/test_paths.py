from __future__ import annotations

from pathlib import Path

from agent.core.paths import get_project_root


def test_get_project_root_prefers_checked_out_repo(monkeypatch):
    monkeypatch.delenv("AGENT_PROJECT_ROOT", raising=False)

    root = Path(get_project_root())

    assert (root / "pyproject.toml").exists()
    assert root.name == "Agent_Life_Space"
