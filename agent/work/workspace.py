"""
Agent Life Space — Workspace Manager

Workspace = izolované miesto kde John robí prácu.

Problém: John teraz pracuje priamo v agent project root — to je nebezpečné.
Riešenie: Každá práca má vlastný workspace (adresár) s lifecycle.

Workspace lifecycle:
    CREATED → ACTIVE → COMPLETED | FAILED → CLEANED

Persistence:
    - Workspace metadata sú uložené v SQLite
    - Audit trail: commands, files, outputs, failures
    - Recovery po reštarte — aktívne workspaces sa obnovia
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Workspaces root — mimo hlavného kódu
_WORKSPACES_ROOT = Path(
    os.environ.get("AGENT_PROJECT_ROOT", str(Path.home() / "agent-life-space"))
) / "workspaces"


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
    """Spravuje izolované pracovné priestory s SQLite persistence."""

    DEFAULT_MAX_ACTIVE = 3
    DEFAULT_TTL_HOURS = 24  # Completed/failed workspaces older than this get auto-cleaned

    def __init__(
        self,
        root: str | None = None,
        db_path: str | None = None,
        max_active: int = DEFAULT_MAX_ACTIVE,
        ttl_hours: int = DEFAULT_TTL_HOURS,
    ) -> None:
        self._root = Path(root) if root else _WORKSPACES_ROOT
        self._workspaces: dict[str, Workspace] = {}
        self._db_path = db_path or str(self._root / "workspaces.db")
        self._db: sqlite3.Connection | None = None
        self._max_active = max_active
        self._ttl_hours = ttl_hours

    def initialize(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._db_path)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                project_id TEXT DEFAULT '',
                task_id TEXT DEFAULT '',
                status TEXT NOT NULL,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                output TEXT DEFAULT '',
                error TEXT DEFAULT ''
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS workspace_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
            )
        """)
        self._db.commit()
        self._load_from_db()
        logger.info("workspace_manager_initialized", root=str(self._root),
                     recovered=len(self._workspaces))

    def _load_from_db(self) -> None:
        """Load workspaces from DB on startup — recovery."""
        if not self._db:
            return
        cursor = self._db.execute(
            "SELECT id, name, project_id, task_id, status, path, "
            "created_at, started_at, completed_at, output, error FROM workspaces"
        )
        for row in cursor:
            ws = Workspace(
                id=row[0], name=row[1], project_id=row[2], task_id=row[3],
                status=WorkspaceStatus(row[4]), path=row[5],
                created_at=row[6], started_at=row[7], completed_at=row[8],
                output=row[9] or "", error=row[10] or "",
            )
            # Load audit trail for commands/files
            audit_cursor = self._db.execute(
                "SELECT event_type, detail FROM workspace_audit "
                "WHERE workspace_id = ? ORDER BY id",
                (ws.id,),
            )
            for event_type, detail in audit_cursor:
                if event_type == "command":
                    ws.commands_run.append(detail)
                elif event_type == "file":
                    ws.files_created.append(detail)
            self._workspaces[ws.id] = ws

    def _persist(self, ws: Workspace) -> None:
        """Save workspace state to DB."""
        if not self._db:
            return
        self._db.execute(
            """INSERT OR REPLACE INTO workspaces
            (id, name, project_id, task_id, status, path,
             created_at, started_at, completed_at, output, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ws.id, ws.name, ws.project_id, ws.task_id,
             ws.status.value, ws.path, ws.created_at,
             ws.started_at, ws.completed_at, ws.output, ws.error),
        )
        self._db.commit()

    def _audit(self, workspace_id: str, event_type: str, detail: str = "") -> None:
        """Record audit event."""
        if not self._db:
            return
        self._db.execute(
            "INSERT INTO workspace_audit (workspace_id, event_type, detail, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (workspace_id, event_type, detail, datetime.now(UTC).isoformat()),
        )
        self._db.commit()

    def create(
        self,
        name: str,
        project_id: str = "",
        task_id: str = "",
    ) -> Workspace:
        """Vytvor nový workspace s vlastným adresárom. Enforces max_active limit."""
        active_count = sum(
            1 for w in self._workspaces.values()
            if w.status == WorkspaceStatus.ACTIVE
        )
        if active_count >= self._max_active:
            msg = (
                f"Cannot create workspace: {active_count} active workspaces "
                f"(max {self._max_active}). Complete or fail existing ones first."
            )
            raise RuntimeError(msg)

        ws = Workspace(
            name=name,
            project_id=project_id,
            task_id=task_id,
        )
        ws_path = self._root / ws.id
        ws_path.mkdir(parents=True, exist_ok=True)
        ws.path = str(ws_path)
        self._workspaces[ws.id] = ws
        self._persist(ws)
        self._audit(ws.id, "lifecycle", "created")
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
        self._persist(ws)
        self._audit(workspace_id, "lifecycle", "activated")
        logger.info("workspace_activated", id=workspace_id)
        return ws

    def complete(self, workspace_id: str, output: str = "") -> Workspace | None:
        ws = self._workspaces.get(workspace_id)
        if not ws:
            return None
        ws.status = WorkspaceStatus.COMPLETED
        ws.completed_at = datetime.now(UTC).isoformat()
        ws.output = output
        self._persist(ws)
        self._audit(workspace_id, "lifecycle", "completed")
        logger.info("workspace_completed", id=workspace_id)
        return ws

    def fail(self, workspace_id: str, error: str = "") -> Workspace | None:
        ws = self._workspaces.get(workspace_id)
        if not ws:
            return None
        ws.status = WorkspaceStatus.FAILED
        ws.completed_at = datetime.now(UTC).isoformat()
        ws.error = error
        self._persist(ws)
        self._audit(workspace_id, "lifecycle", f"failed: {error[:200]}")
        logger.info("workspace_failed", id=workspace_id, error=error[:100])
        return ws

    def record_command(self, workspace_id: str, command: str) -> None:
        """Zaznamenaj spustený príkaz."""
        ws = self._workspaces.get(workspace_id)
        if ws:
            ws.commands_run.append(command)
            self._audit(workspace_id, "command", command)

    def record_file(self, workspace_id: str, filepath: str) -> None:
        """Zaznamenaj vytvorený súbor."""
        ws = self._workspaces.get(workspace_id)
        if ws:
            ws.files_created.append(filepath)
            self._audit(workspace_id, "file", filepath)

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
            self._persist(ws)
            self._audit(workspace_id, "lifecycle", "cleaned")
            logger.info("workspace_cleaned", id=workspace_id)
            return True
        except Exception as e:
            logger.error("workspace_cleanup_error", id=workspace_id, error=str(e))
            return False

    def get_audit_trail(self, workspace_id: str) -> list[dict[str, str]]:
        """Return full audit trail for a workspace."""
        if not self._db:
            return []
        cursor = self._db.execute(
            "SELECT event_type, detail, timestamp FROM workspace_audit "
            "WHERE workspace_id = ? ORDER BY id",
            (workspace_id,),
        )
        return [
            {"event_type": row[0], "detail": row[1], "timestamp": row[2]}
            for row in cursor
        ]

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

    def cleanup_expired(self) -> int:
        """
        Auto-cleanup completed/failed workspaces older than TTL.
        Returns number of workspaces cleaned.
        """
        now = datetime.now(UTC)
        cleaned = 0
        for ws in list(self._workspaces.values()):
            if ws.status not in (WorkspaceStatus.COMPLETED, WorkspaceStatus.FAILED):
                continue
            if not ws.completed_at:
                continue
            try:
                completed = datetime.fromisoformat(ws.completed_at)
                age_hours = (now - completed).total_seconds() / 3600
                if age_hours > self._ttl_hours:
                    if self.cleanup(ws.id):
                        cleaned += 1
            except (ValueError, TypeError):
                continue

        if cleaned:
            logger.info("workspace_expired_cleanup", cleaned=cleaned)
        return cleaned

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
