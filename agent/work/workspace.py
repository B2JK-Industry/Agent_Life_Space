"""
Agent Life Space — Workspace Manager

Workspace = izolované miesto kde John robí prácu.

Problém: John teraz pracuje priamo v ~/agent-life-space — to je nebezpečné.
Riešenie: Každá práca má vlastný workspace (adresár) s lifecycle.

Workspace lifecycle:
    CREATED → ACTIVE → COMPLETED | FAILED → CLEANED

Čo workspace obsahuje:
    - Vlastný adresár (~/agent-life-space/workspaces/<id>/)
    - Git repo (ak treba)
    - Výstupné súbory
    - Log čo sa robilo

Bezpečnosť:
    - Workspace je mimo hlavného kódu agenta
    - Docker sandbox pre spúšťanie cudzieho kódu
    - Automatický cleanup po dokončení
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Workspaces root — mimo hlavného kódu
_WORKSPACES_ROOT = Path.home() / "agent-life-space" / "workspaces"


class WorkspaceStatus(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CLEANED = "cleaned"


@dataclass
class Workspace:
    """Izolovaný pracovný priestor pre jednu úlohu."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    project_id: str = ""  # Referencia na Project
    task_id: str = ""     # Referencia na Task
    status: WorkspaceStatus = WorkspaceStatus.CREATED
    path: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    started_at: str | None = None
    completed_at: str | None = None
    # Čo sa robilo
    commands_run: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    output: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "path": self.path,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "commands_run": self.commands_run,
            "files_created": self.files_created,
            "output": self.output,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workspace:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            project_id=data.get("project_id", ""),
            task_id=data.get("task_id", ""),
            status=WorkspaceStatus(data.get("status", "created")),
            path=data.get("path", ""),
            created_at=data.get("created_at", ""),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            commands_run=data.get("commands_run", []),
            files_created=data.get("files_created", []),
            output=data.get("output", ""),
            error=data.get("error", ""),
        )


class WorkspaceManager:
    """Spravuje izolované pracovné priestory."""

    def __init__(self, root: str | None = None) -> None:
        self._root = Path(root) if root else _WORKSPACES_ROOT
        self._workspaces: dict[str, Workspace] = {}

    def initialize(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info("workspace_manager_initialized", root=str(self._root))

    def create(
        self,
        name: str,
        project_id: str = "",
        task_id: str = "",
    ) -> Workspace:
        """Vytvor nový workspace s vlastným adresárom."""
        ws = Workspace(
            name=name,
            project_id=project_id,
            task_id=task_id,
        )
        ws_path = self._root / ws.id
        ws_path.mkdir(parents=True, exist_ok=True)
        ws.path = str(ws_path)
        self._workspaces[ws.id] = ws
        logger.info("workspace_created", id=ws.id, name=name, path=str(ws_path))
        return ws

    def get(self, workspace_id: str) -> Workspace | None:
        return self._workspaces.get(workspace_id)

    def activate(self, workspace_id: str) -> Workspace | None:
        ws = self._workspaces.get(workspace_id)
        if not ws:
            return None
        ws.status = WorkspaceStatus.ACTIVE
        ws.started_at = datetime.now(UTC).isoformat()
        logger.info("workspace_activated", id=workspace_id)
        return ws

    def complete(self, workspace_id: str, output: str = "") -> Workspace | None:
        ws = self._workspaces.get(workspace_id)
        if not ws:
            return None
        ws.status = WorkspaceStatus.COMPLETED
        ws.completed_at = datetime.now(UTC).isoformat()
        ws.output = output
        logger.info("workspace_completed", id=workspace_id)
        return ws

    def fail(self, workspace_id: str, error: str = "") -> Workspace | None:
        ws = self._workspaces.get(workspace_id)
        if not ws:
            return None
        ws.status = WorkspaceStatus.FAILED
        ws.completed_at = datetime.now(UTC).isoformat()
        ws.error = error
        logger.info("workspace_failed", id=workspace_id, error=error[:100])
        return ws

    def record_command(self, workspace_id: str, command: str) -> None:
        """Zaznamenaj spustený príkaz."""
        ws = self._workspaces.get(workspace_id)
        if ws:
            ws.commands_run.append(command)

    def record_file(self, workspace_id: str, filepath: str) -> None:
        """Zaznamenaj vytvorený súbor."""
        ws = self._workspaces.get(workspace_id)
        if ws:
            ws.files_created.append(filepath)

    def cleanup(self, workspace_id: str) -> bool:
        """Vymaž workspace adresár. Volať len po complete/fail."""
        ws = self._workspaces.get(workspace_id)
        if not ws:
            return False
        if ws.status not in (WorkspaceStatus.COMPLETED, WorkspaceStatus.FAILED):
            logger.warning("workspace_cleanup_refused", id=workspace_id,
                           reason="not completed or failed")
            return False
        try:
            ws_path = Path(ws.path)
            if ws_path.exists() and str(ws_path).startswith(str(self._root)):
                shutil.rmtree(ws_path)
            ws.status = WorkspaceStatus.CLEANED
            logger.info("workspace_cleaned", id=workspace_id)
            return True
        except Exception as e:
            logger.error("workspace_cleanup_error", id=workspace_id, error=str(e))
            return False

    def list_workspaces(
        self,
        status: WorkspaceStatus | None = None,
        project_id: str = "",
    ) -> list[Workspace]:
        workspaces = list(self._workspaces.values())
        if status:
            workspaces = [w for w in workspaces if w.status == status]
        if project_id:
            workspaces = [w for w in workspaces if w.project_id == project_id]
        return workspaces

    def get_active(self) -> Workspace | None:
        """Vráť aktívny workspace (max 1)."""
        active = [w for w in self._workspaces.values()
                  if w.status == WorkspaceStatus.ACTIVE]
        return active[0] if active else None

    def get_stats(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for ws in self._workspaces.values():
            by_status[ws.status.value] = by_status.get(ws.status.value, 0) + 1
        return {
            "total": len(self._workspaces),
            "by_status": by_status,
            "active": self.get_active().name if self.get_active() else None,
            "root": str(self._root),
        }
