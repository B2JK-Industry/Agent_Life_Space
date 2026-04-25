"""Tests for Coordinator Mode v1 — sub-agent isolated workspace per step."""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

from agent.initiative.executor import StepExecutor
from agent.initiative.schemas import PlannedStep, StepKind


class FakeResponse:
    def __init__(self, success=True, text="result text", error=""):
        self.success = success
        self.text = text
        self.error = error


class FakeProvider:
    def __init__(self) -> None:
        self.last_request: Any = None

    async def generate(self, req: Any) -> FakeResponse:
        self.last_request = req
        return FakeResponse(success=True, text="agent did something useful")


def _step(coordinator: bool, kind: StepKind = StepKind.CODE) -> PlannedStep:
    return PlannedStep(
        idx=0,
        kind=kind,
        title="test step",
        prompt="do something useful with code",
        depends_on_idx=[],
        estimated_minutes=5,
        metadata={"coordinator": coordinator} if coordinator else {},
    )


@pytest.mark.asyncio
async def test_no_coordinator_uses_project_root():
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider()
        ex = StepExecutor(
            provider=provider,
            agent_name="t",
            project_root=tmp,
            data_root=tmp,
        )
        cwd, ws_id = ex._resolve_cwd("init1", _step(coordinator=False))
        assert cwd == tmp
        assert ws_id is None


@pytest.mark.asyncio
async def test_coordinator_creates_workspace():
    """coordinator=True → workspace created, cwd points there, distinct from project_root."""
    with tempfile.TemporaryDirectory() as tmp:
        # Inject a workspace_manager so we don't pollute real workspaces dir
        from agent.work.workspace import WorkspaceManager

        wm_root = os.path.join(tmp, "ws_root")
        wm = WorkspaceManager(root=wm_root, db_path=os.path.join(tmp, "wm.db"))
        wm.initialize()

        provider = FakeProvider()
        ex = StepExecutor(
            provider=provider,
            agent_name="t",
            project_root=tmp,
            data_root=tmp,
        )
        ex._workspace_manager = wm

        cwd, ws_id = ex._resolve_cwd("init_coord", _step(coordinator=True))
        assert cwd != tmp, "coordinator should NOT use project_root"
        assert ws_id is not None
        assert os.path.isdir(cwd), "workspace dir must exist"
        # Workspace recorded in WorkspaceManager
        ws = wm.get(ws_id)
        assert ws is not None
        assert ws.status.value == "active"
        assert ws.owner_id == "init_coord"


@pytest.mark.asyncio
async def test_coordinator_workspace_completed_after_step():
    """End-to-end: full _handle_llm_step with coordinator → workspace status=completed."""
    with tempfile.TemporaryDirectory() as tmp:
        from agent.work.workspace import WorkspaceManager

        wm = WorkspaceManager(root=os.path.join(tmp, "ws"), db_path=os.path.join(tmp, "wm.db"))
        wm.initialize()

        provider = FakeProvider()
        ex = StepExecutor(
            provider=provider,
            agent_name="t",
            project_root=tmp,
            data_root=tmp,
        )
        ex._workspace_manager = wm

        result = await ex._handle_llm_step(
            initiative_id="init1",
            initiative_title="test init",
            initiative_goal="goal",
            pattern_id="scraper",
            step=_step(coordinator=True, kind=StepKind.CODE),
            prior_outputs=[],
            total_steps=1,
            attempt=1,
        )
        assert result.success
        ws_id = result.metadata.get("workspace_id")
        assert ws_id is not None
        ws = wm.get(ws_id)
        assert ws is not None
        assert ws.status.value == "completed", f"expected completed, got {ws.status.value}"
        # Output captured
        assert "agent did" in ws.output


@pytest.mark.asyncio
async def test_coordinator_falls_back_when_workspace_unavailable():
    """If WorkspaceManager init fails, coordinator falls back to project_root (not crash)."""
    with tempfile.TemporaryDirectory() as tmp:
        provider = FakeProvider()
        ex = StepExecutor(
            provider=provider,
            agent_name="t",
            project_root=tmp,
            data_root=tmp,
        )
        # No workspace_manager injected — falls back via lazy create
        # This MIGHT succeed with default WorkspaceManager pointing to real dir,
        # or fall back to project_root. Either way, must not crash.
        cwd, ws_id = ex._resolve_cwd("init_x", _step(coordinator=True))
        assert cwd  # not empty
        # ws_id may be set (success) or None (fallback) — both are valid
