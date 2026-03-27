"""
Agent Life Space — Control-Plane State Storage

Durable persistence for planner handoff records, planning traces,
and delivery lifecycle state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import orjson
import structlog

from agent.core.paths import get_project_root

logger = structlog.get_logger(__name__)


class ControlPlaneStorage:
    """SQLite-backed persistence for shared control-plane state."""

    def __init__(self, db_path: str = "") -> None:
        if not db_path:
            db_path = str(Path(get_project_root()) / "agent" / "control" / "control.db")
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
            CREATE TABLE IF NOT EXISTS job_plan_records (
                plan_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                status TEXT NOT NULL,
                resolved_work_type TEXT DEFAULT '',
                linked_job_id TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_trace_records (
                trace_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                trace_kind TEXT NOT NULL,
                plan_id TEXT DEFAULT '',
                job_id TEXT DEFAULT '',
                workspace_id TEXT DEFAULT '',
                bundle_id TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_records (
                bundle_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                status TEXT NOT NULL,
                job_id TEXT NOT NULL,
                workspace_id TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._db.commit()
        self._initialized = True
        logger.info("control_plane_storage_initialized", db_path=self._db_path)

    def save_plan_record(self, record: Any) -> None:
        if self._db is None:
            return
        payload = orjson.dumps(record.to_dict()).decode()
        self._db.execute(
            """
            INSERT OR REPLACE INTO job_plan_records
            (plan_id, data, status, resolved_work_type, linked_job_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.plan_id,
                payload,
                record.status.value,
                record.resolved_work_type,
                record.linked_job_id,
                record.created_at,
                record.updated_at,
            ),
        )
        self._db.commit()

    def load_plan_record(self, plan_id: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT data FROM job_plan_records WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        return orjson.loads(row[0])

    def list_plan_records(
        self,
        *,
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        if status:
            rows = self._db.execute(
                """
                SELECT data FROM job_plan_records
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                """
                SELECT data FROM job_plan_records
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [orjson.loads(row[0]) for row in rows]

    def save_trace_record(self, record: Any) -> None:
        if self._db is None:
            return
        payload = orjson.dumps(record.to_dict()).decode()
        self._db.execute(
            """
            INSERT OR REPLACE INTO execution_trace_records
            (trace_id, data, trace_kind, plan_id, job_id, workspace_id, bundle_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.trace_id,
                payload,
                record.trace_kind.value,
                record.plan_id,
                record.job_id,
                record.workspace_id,
                record.bundle_id,
                record.created_at,
            ),
        )
        self._db.commit()

    def list_trace_records(
        self,
        *,
        trace_kind: str = "",
        plan_id: str = "",
        job_id: str = "",
        workspace_id: str = "",
        bundle_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        query = """
            SELECT data FROM execution_trace_records
            WHERE 1 = 1
        """
        params: list[Any] = []
        if trace_kind:
            query += " AND trace_kind = ?"
            params.append(trace_kind)
        if plan_id:
            query += " AND plan_id = ?"
            params.append(plan_id)
        if job_id:
            query += " AND job_id = ?"
            params.append(job_id)
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        if bundle_id:
            query += " AND bundle_id = ?"
            params.append(bundle_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(query, tuple(params)).fetchall()
        return [orjson.loads(row[0]) for row in rows]

    def save_delivery_record(self, record: Any) -> None:
        if self._db is None:
            return
        payload = orjson.dumps(record.to_dict()).decode()
        self._db.execute(
            """
            INSERT OR REPLACE INTO delivery_records
            (bundle_id, data, status, job_id, workspace_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.bundle_id,
                payload,
                record.status.value,
                record.job_id,
                record.workspace_id,
                record.created_at,
                record.updated_at,
            ),
        )
        self._db.commit()

    def load_delivery_record(self, bundle_id: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT data FROM delivery_records WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
        if row is None:
            return None
        return orjson.loads(row[0])

    def list_delivery_records(
        self,
        *,
        status: str = "",
        job_id: str = "",
        workspace_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        query = """
            SELECT data FROM delivery_records
            WHERE 1 = 1
        """
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if job_id:
            query += " AND job_id = ?"
            params.append(job_id)
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(query, tuple(params)).fetchall()
        return [orjson.loads(row[0]) for row in rows]

    def get_stats(self) -> dict[str, Any]:
        if self._db is None:
            return {
                "plans": 0,
                "traces": 0,
                "deliveries": 0,
            }
        plans = self._db.execute(
            "SELECT COUNT(*) FROM job_plan_records"
        ).fetchone()[0]
        traces = self._db.execute(
            "SELECT COUNT(*) FROM execution_trace_records"
        ).fetchone()[0]
        deliveries = self._db.execute(
            "SELECT COUNT(*) FROM delivery_records"
        ).fetchone()[0]
        return {
            "plans": plans,
            "traces": traces,
            "deliveries": deliveries,
        }

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        self._initialized = False
