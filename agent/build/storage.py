"""
Agent Life Space — Build Storage

SQLite persistence for build jobs and artifacts.
Same pattern as agent.review.storage.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
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
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
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
                format TEXT DEFAULT 'text',
                created_at TEXT
            )
        """)
        self._ensure_text_column("build_artifacts", "format", "text")
        self._db.commit()
        self._initialized = True
        logger.debug("build_storage_initialized", db_path=self._db_path)

    def _ensure_text_column(self, table: str, column: str, default: str) -> None:
        if self._db is None:
            return
        rows = self._db.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
        known = {row[1] for row in rows}
        if column in known:
            return
        self._db.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} TEXT DEFAULT '{default}'"  # noqa: S608
        )

    def save_job(self, job: BuildJob) -> None:
        if not self._db:
            return None  # type: ignore[return-value]
        data = json.dumps(job.to_dict())
        self._db.execute(
            "INSERT OR REPLACE INTO build_jobs (id, data, status, build_type, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job.id, data, job.status.value, job.build_type.value,
             job.timing.created_at),
        )
        self._db.commit()

    def load_job(self, job_id: str) -> dict[str, Any] | None:
        if not self._db:
            return None  # type: ignore[return-value]
        row = self._db.execute(
            "SELECT data FROM build_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def list_jobs(
        self, status: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        if not self._db:
            return None  # type: ignore[return-value]
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
        if not self._db:
            return None  # type: ignore[return-value]
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
            "(id, job_id, artifact_kind, content, content_json, format, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact.id, artifact.job_id, artifact.artifact_kind.value,
             content, content_json_str, artifact.format, artifact.created_at),
        )
        self._db.commit()

    def get_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        if not self._db:
            return None  # type: ignore[return-value]
        rows = self._db.execute(
            "SELECT id, artifact_kind, content, content_json, format, created_at "
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
                "format": r[4] or "text",
                "created_at": r[5],
            }
            if r[3]:
                d["content_json"] = json.loads(r[3])
            results.append(d)
        return results

    def list_artifacts(
        self,
        *,
        job_id: str = "",
        artifact_kind: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not self._db:
            return None  # type: ignore[return-value]
        if job_id and artifact_kind:
            rows = self._db.execute(
                "SELECT id, job_id, artifact_kind, content, content_json, format, created_at "
                "FROM build_artifacts WHERE job_id = ? AND artifact_kind = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (job_id, artifact_kind, limit),
            ).fetchall()
        elif job_id:
            rows = self._db.execute(
                "SELECT id, job_id, artifact_kind, content, content_json, format, created_at "
                "FROM build_artifacts WHERE job_id = ? ORDER BY created_at DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        elif artifact_kind:
            rows = self._db.execute(
                "SELECT id, job_id, artifact_kind, content, content_json, format, created_at "
                "FROM build_artifacts WHERE artifact_kind = ? ORDER BY created_at DESC LIMIT ?",
                (artifact_kind, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT id, job_id, artifact_kind, content, content_json, format, created_at "
                "FROM build_artifacts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        artifacts: list[dict[str, Any]] = []
        for row in rows:
            entry: dict[str, Any] = {
                "id": row[0],
                "job_id": row[1],
                "artifact_kind": row[2],
                "content": row[3],
                "content_json": {},
                "format": row[5] or "text",
                "created_at": row[6],
            }
            if row[4]:
                entry["content_json"] = json.loads(row[4])
            artifacts.append(entry)
        return artifacts

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        if not self._db:
            return None  # type: ignore[return-value]
        row = self._db.execute(
            "SELECT id, job_id, artifact_kind, content, content_json, format, created_at "
            "FROM build_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = {
            "id": row[0],
            "job_id": row[1],
            "artifact_kind": row[2],
            "content": row[3],
            "content_json": {},
            "format": row[5] or "text",
            "created_at": row[6],
        }
        if row[4]:
            result["content_json"] = json.loads(row[4])
        return result

    def get_stats(self) -> dict[str, Any]:
        if not self._db:
            return {
                "total_jobs": 0,
                "artifacts": 0,
                "by_status": {},
            }

        total_jobs = self._db.execute(
            "SELECT COUNT(*) FROM build_jobs"
        ).fetchone()[0]
        total_artifacts = self._db.execute(
            "SELECT COUNT(*) FROM build_artifacts"
        ).fetchone()[0]
        by_status_rows = self._db.execute(
            "SELECT status, COUNT(*) FROM build_jobs GROUP BY status"
        ).fetchall()
        return {
            "total_jobs": total_jobs,
            "artifacts": total_artifacts,
            "by_status": {row[0]: row[1] for row in by_status_rows},
        }

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
