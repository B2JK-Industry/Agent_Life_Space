"""
Agent Life Space — Project Manager

Projekt = dlhodobá iniciatíva s viacerými taskami.

Hierarchia:
    Project (cieľ)
      └── Tasks (kroky — cez TaskManager)

Lifecycle:
    IDEA → PLANNING → ACTIVE → PAUSED → COMPLETED | ABANDONED

Príklady projektov:
    - "Moltbook integrácia" (claim, heartbeat, posting)
    - "Earning modul" (nájsť prácu, urobiť, fakturovať)
    - "Token optimalizácia" (presun .md, API migration)

Každý projekt má:
    - Zoznam task IDs (referencie na TaskManager)
    - Stav a progress (% completion)
    - Deadline a priority
    - Zdroje (aké skills treba, koľko to stojí)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import aiosqlite
import orjson
import structlog

logger = structlog.get_logger(__name__)


class ProjectStatus(str, Enum):
    IDEA = "idea"              # Nápad, ešte neschválený
    PLANNING = "planning"      # Definujú sa tasky
    ACTIVE = "active"          # Prebieha práca
    PAUSED = "paused"          # Pozastavený
    COMPLETED = "completed"    # Hotový
    ABANDONED = "abandoned"    # Zrušený


@dataclass
class Project:
    """Dlhodobá iniciatíva s viacerými taskami."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    description: str = ""
    status: ProjectStatus = ProjectStatus.IDEA
    task_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    priority: float = 0.5
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    started_at: str | None = None
    completed_at: str | None = None
    deadline: str | None = None
    # Zdroje a odhady
    required_skills: list[str] = field(default_factory=list)
    estimated_hours: float = 0.0
    estimated_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    # Výstup
    result: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "task_ids": self.task_ids,
            "tags": self.tags,
            "priority": self.priority,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "deadline": self.deadline,
            "required_skills": self.required_skills,
            "estimated_hours": self.estimated_hours,
            "estimated_cost_usd": self.estimated_cost_usd,
            "actual_cost_usd": self.actual_cost_usd,
            "result": self.result,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            name=data.get("name", ""),
            description=data.get("description", ""),
            status=ProjectStatus(data.get("status", "idea")),
            task_ids=data.get("task_ids", []),
            tags=data.get("tags", []),
            priority=data.get("priority", 0.5),
            created_at=data.get("created_at", ""),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            deadline=data.get("deadline"),
            required_skills=data.get("required_skills", []),
            estimated_hours=data.get("estimated_hours", 0.0),
            estimated_cost_usd=data.get("estimated_cost_usd", 0.0),
            actual_cost_usd=data.get("actual_cost_usd", 0.0),
            result=data.get("result", ""),
            notes=data.get("notes", ""),
        )


class ProjectManager:
    """
    Spravuje projekty. Každý projekt groupuje tasky z TaskManager.
    """

    def __init__(self, db_path: str = "agent/projects/projects.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await self._db.commit()
        count = await self._count()
        logger.info("project_manager_initialized", count=count)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def _count(self) -> int:
        assert self._db
        async with self._db.execute("SELECT COUNT(*) FROM projects") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def create(
        self,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        priority: float = 0.5,
        required_skills: list[str] | None = None,
        deadline: str | None = None,
    ) -> Project:
        """Vytvor nový projekt."""
        project = Project(
            name=name,
            description=description,
            tags=tags or [],
            priority=priority,
            required_skills=required_skills or [],
            deadline=deadline,
        )
        assert self._db
        await self._db.execute(
            "INSERT INTO projects (id, data, created_at) VALUES (?, ?, ?)",
            (project.id, orjson.dumps(project.to_dict()).decode(), project.created_at),
        )
        await self._db.commit()
        logger.info("project_created", id=project.id, name=name)
        return project

    async def get(self, project_id: str) -> Project | None:
        assert self._db
        async with self._db.execute(
            "SELECT data FROM projects WHERE id = ?", (project_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return Project.from_dict(orjson.loads(row[0]))
        return None

    async def update(self, project: Project) -> None:
        assert self._db
        await self._db.execute(
            "UPDATE projects SET data = ? WHERE id = ?",
            (orjson.dumps(project.to_dict()).decode(), project.id),
        )
        await self._db.commit()

    async def add_task(self, project_id: str, task_id: str) -> Project | None:
        """Pridaj task do projektu."""
        project = await self.get(project_id)
        if not project:
            return None
        if task_id not in project.task_ids:
            project.task_ids.append(task_id)
            await self.update(project)
            logger.info("project_task_added", project=project_id, task=task_id)
        return project

    async def start(self, project_id: str) -> Project | None:
        """Presuň projekt do ACTIVE."""
        project = await self.get(project_id)
        if not project:
            return None
        project.status = ProjectStatus.ACTIVE
        project.started_at = datetime.now(UTC).isoformat()
        await self.update(project)
        logger.info("project_started", id=project_id, name=project.name)
        return project

    async def complete(self, project_id: str, result: str = "") -> Project | None:
        """Označ projekt ako hotový."""
        project = await self.get(project_id)
        if not project:
            return None
        project.status = ProjectStatus.COMPLETED
        project.completed_at = datetime.now(UTC).isoformat()
        project.result = result
        await self.update(project)
        logger.info("project_completed", id=project_id, name=project.name)
        return project

    async def pause(self, project_id: str) -> Project | None:
        project = await self.get(project_id)
        if not project:
            return None
        project.status = ProjectStatus.PAUSED
        await self.update(project)
        return project

    async def abandon(self, project_id: str, reason: str = "") -> Project | None:
        project = await self.get(project_id)
        if not project:
            return None
        project.status = ProjectStatus.ABANDONED
        project.notes = reason
        await self.update(project)
        logger.info("project_abandoned", id=project_id, reason=reason)
        return project

    async def list_projects(
        self,
        status: ProjectStatus | None = None,
    ) -> list[Project]:
        assert self._db
        async with self._db.execute("SELECT data FROM projects") as cur:
            rows = await cur.fetchall()
        projects = [Project.from_dict(orjson.loads(r[0])) for r in rows]
        if status:
            projects = [p for p in projects if p.status == status]
        return sorted(projects, key=lambda p: p.priority, reverse=True)

    async def get_progress(self, project_id: str, task_manager: Any = None) -> dict[str, Any]:
        """Vypočítaj progress projektu na základe taskov."""
        project = await self.get(project_id)
        if not project:
            return {"error": "Project not found"}

        if not project.task_ids or not task_manager:
            return {
                "project": project.name,
                "total_tasks": len(project.task_ids),
                "progress": 0.0,
            }

        completed = 0
        failed = 0
        for tid in project.task_ids:
            task = task_manager.get_task(tid)
            if task and task.status.value == "completed":
                completed += 1
            elif task and task.status.value == "failed":
                failed += 1

        total = len(project.task_ids)
        progress = completed / total if total > 0 else 0.0

        return {
            "project": project.name,
            "total_tasks": total,
            "completed": completed,
            "failed": failed,
            "remaining": total - completed - failed,
            "progress": round(progress, 2),
        }

    async def get_stats(self) -> dict[str, Any]:
        projects = await self.list_projects()
        by_status: dict[str, int] = {}
        for p in projects:
            by_status[p.status.value] = by_status.get(p.status.value, 0) + 1
        return {
            "total_projects": len(projects),
            "by_status": by_status,
            "active": [p.name for p in projects if p.status == ProjectStatus.ACTIVE],
        }
