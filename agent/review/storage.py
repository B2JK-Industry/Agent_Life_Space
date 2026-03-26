"""
Agent Life Space — Review Storage

Persists review jobs, artifacts, and reports.
SQLite-backed, recoverable, auditable.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC
from pathlib import Path
from typing import Any

import orjson
import structlog

from agent.core.paths import get_project_root
from agent.review.models import ReviewArtifact, ReviewJob

logger = structlog.get_logger(__name__)


class ReviewStorage:
    """SQLite-backed storage for review jobs and artifacts."""

    def __init__(self, db_path: str = "") -> None:
        if not db_path:
            db_path = str(Path(get_project_root()) / "agent" / "review" / "reviews.db")
        self._db_path = db_path
        self._db: sqlite3.Connection | None = None

    def initialize(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._db_path)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS review_jobs (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                status TEXT NOT NULL,
                job_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS review_artifacts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                content TEXT NOT NULL,
                content_json TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES review_jobs(id)
            )
        """)
        self._db.commit()
        logger.info("review_storage_initialized", db=self._db_path)

    def save_job(self, job: ReviewJob) -> None:
        if not self._db:
            return
        data = orjson.dumps(job.to_dict()).decode()
        self._db.execute(
            "INSERT OR REPLACE INTO review_jobs (id, data, status, job_type, created_at) VALUES (?, ?, ?, ?, ?)",
            (job.id, data, job.status.value, job.job_type.value, job.created_at),
        )
        self._db.commit()

    def load_job(self, job_id: str) -> dict[str, Any] | None:
        if not self._db:
            return None
        cursor = self._db.execute("SELECT data FROM review_jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if row:
            return orjson.loads(row[0])
        return None

    def list_jobs(self, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        if not self._db:
            return []
        if status:
            cursor = self._db.execute(
                "SELECT data FROM review_jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = self._db.execute(
                "SELECT data FROM review_jobs ORDER BY created_at DESC LIMIT ?", (limit,),
            )
        return [orjson.loads(row[0]) for row in cursor]

    _MAX_ARTIFACT_SIZE = 5 * 1024 * 1024  # 5MB per artifact

    def save_artifact(self, artifact: ReviewArtifact) -> None:
        if not self._db:
            return
        content = artifact.content
        if len(content) > self._MAX_ARTIFACT_SIZE:
            content = content[:self._MAX_ARTIFACT_SIZE] + "\n\n[TRUNCATED — exceeded 5MB limit]"
        json_str = orjson.dumps(artifact.content_json).decode() if artifact.content_json else ""
        if len(json_str) > self._MAX_ARTIFACT_SIZE:
            json_str = ""  # Drop oversized JSON rather than corrupt it
        self._db.execute(
            "INSERT OR REPLACE INTO review_artifacts (id, job_id, artifact_type, content, content_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (artifact.id, artifact.job_id, artifact.artifact_type.value,
             content, json_str, artifact.created_at),
        )
        self._db.commit()

    def get_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        if not self._db:
            return []
        cursor = self._db.execute(
            "SELECT id, artifact_type, content, content_json, created_at FROM review_artifacts WHERE job_id = ?",
            (job_id,),
        )
        results = []
        for r in cursor:
            entry: dict[str, Any] = {
                "id": r[0], "artifact_type": r[1],
                "content": r[2], "created_at": r[4],
            }
            if r[3]:
                try:
                    entry["content_json"] = orjson.loads(r[3])
                except Exception:
                    entry["content_json"] = {}
            results.append(entry)
        return results

    def cleanup_old_jobs(self, max_age_days: int = 30) -> int:
        """Remove jobs older than max_age_days. Returns count of removed jobs."""
        if not self._db:
            return 0
        from datetime import datetime, timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()
        cursor = self._db.execute(
            "SELECT id FROM review_jobs WHERE created_at < ?", (cutoff,),
        )
        old_ids = [row[0] for row in cursor]
        if not old_ids:
            return 0
        placeholders = ",".join("?" for _ in old_ids)
        self._db.execute(f"DELETE FROM review_artifacts WHERE job_id IN ({placeholders})", old_ids)  # noqa: S608
        self._db.execute(f"DELETE FROM review_jobs WHERE id IN ({placeholders})", old_ids)  # noqa: S608
        self._db.commit()
        logger.info("review_storage_cleanup", removed=len(old_ids), max_age_days=max_age_days)
        return len(old_ids)

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
