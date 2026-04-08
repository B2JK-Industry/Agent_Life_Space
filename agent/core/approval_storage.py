"""
Agent Life Space — Approval Storage

Persistent storage for approval requests and their lifecycle transitions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

import orjson
import structlog

from agent.core.paths import get_project_root

logger = structlog.get_logger(__name__)


class ApprovalStorage:
    """SQLite-backed persistence for approval requests."""

    def __init__(self, db_path: str = "") -> None:
        if not db_path:
            db_path = str(Path(get_project_root()) / "agent" / "approval" / "approvals.db")
        self._db_path = db_path
        self._db: sqlite3.Connection | None = None
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._db_path)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                status TEXT NOT NULL,
                category TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._db.commit()
        self._initialized = True
        logger.info("approval_storage_initialized", db_path=self._db_path)

    def save_request(self, request: Any) -> None:
        if not self._db:
            return
        data = orjson.dumps(request.to_dict()).decode()
        self._db.execute(
            """
            INSERT OR REPLACE INTO approval_requests (id, data, status, category, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                request.id,
                data,
                request.status.value,
                request.category.value,
                request.created_at,
            ),
        )
        self._db.commit()

    def load_request(self, request_id: str) -> dict[str, Any] | None:
        if not self._db:
            return None
        row = self._db.execute(
            "SELECT data FROM approval_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        return cast("dict[str, Any]", orjson.loads(row[0]))

    def list_requests(
        self,
        status: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not self._db:
            return []
        if status:
            rows = self._db.execute(
                """
                SELECT data FROM approval_requests
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                """
                SELECT data FROM approval_requests
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [orjson.loads(row[0]) for row in rows]

    def get_stats(self) -> dict[str, Any]:
        if not self._db:
            return {
                "total_requests": 0,
                "by_status": {},
            }
        total_requests = self._db.execute(
            "SELECT COUNT(*) FROM approval_requests"
        ).fetchone()[0]
        by_status_rows = self._db.execute(
            "SELECT status, COUNT(*) FROM approval_requests GROUP BY status"
        ).fetchall()
        return {
            "total_requests": total_requests,
            "by_status": {row[0]: row[1] for row in by_status_rows},
        }

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
        self._initialized = False
