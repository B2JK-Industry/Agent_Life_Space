"""
Agent Life Space — Build Storage

SQLite persistence for build jobs and artifacts.
Same pattern as agent.review.storage.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import structlog

from agent.build.models import BuildArtifact, BuildJob
from agent.core.paths import get_project_root

logger = structlog.get_logger(__name__)

_MAX_ARTIFACT_BYTES = 5 * 1024 * 1024  # 5MB


class BuildStorage:
    """SQLite-backed persistence for build jobs and artifacts."""

    def __init__(self, db_path: str = "") -> None:
        if not db_path:
            root = get_project_root()
            db_path = str(root / "agent" / "build" / "builds.db")
        self._db_path = db_path
        self._db: sqlite3.Connection | None = None
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        self._db = sqlite3.connect(self._db_path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS build_jobs (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                status TEXT,
                build_type TEXT,
                created_at TEXT
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS build_artifacts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                artifact_kind TEXT,
                content TEXT,
                content_json TEXT,
                created_at TEXT
            )
        """)
        self._db.commit()
        self._initialized = True
        logger.debug("build_storage_initialized", db_path=self._db_path)

    def save_job(self, job: BuildJob) -> None:
        assert self._db is not None
        data = json.dumps(job.to_dict())
        self._db.execute(
            "INSERT OR REPLACE INTO build_jobs (id, data, status, build_type, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job.id, data, job.status.value, job.build_type.value,
             job.timing.created_at),
        )
        self._db.commit()

    def load_job(self, job_id: str) -> dict[str, Any] | None:
        assert self._db is not None
        row = self._db.execute(
            "SELECT data FROM build_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def list_jobs(
        self, status: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        assert self._db is not None
        if status:
            rows = self._db.execute(
                "SELECT data FROM build_jobs WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT data FROM build_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_artifact(self, artifact: BuildArtifact) -> None:
        assert self._db is not None
        content = artifact.content
        if len(content) > _MAX_ARTIFACT_BYTES:
            content = content[:_MAX_ARTIFACT_BYTES]
            logger.warning(
                "build_artifact_truncated",
                artifact_id=artifact.id,
                original_size=len(artifact.content),
            )
        content_json_str = ""
        if artifact.content_json:
            content_json_str = json.dumps(artifact.content_json)
        self._db.execute(
            "INSERT OR REPLACE INTO build_artifacts "
            "(id, job_id, artifact_kind, content, content_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (artifact.id, artifact.job_id, artifact.artifact_kind.value,
             content, content_json_str, artifact.created_at),
        )
        self._db.commit()

    def get_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        assert self._db is not None
        rows = self._db.execute(
            "SELECT id, artifact_kind, content, content_json, created_at "
            "FROM build_artifacts WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        results = []
        for r in rows:
            d: dict[str, Any] = {
                "id": r[0],
                "artifact_kind": r[1],
                "content": r[2],
                "content_json": {},
                "created_at": r[4],
            }
            if r[3]:
                d["content_json"] = json.loads(r[3])
            results.append(d)
        return results

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
