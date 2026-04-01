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
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS product_job_records (
                job_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                job_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                workspace_id TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_retention_records (
                record_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                job_id TEXT NOT NULL,
                job_kind TEXT NOT NULL,
                artifact_kind TEXT NOT NULL,
                retention_policy_id TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS cost_ledger_entries (
                entry_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                job_id TEXT NOT NULL,
                job_kind TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS recurring_workflows (
                workflow_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                schedule TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS job_pipelines (
                pipeline_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                triggered_by TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS settlement_requests (
                settlement_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                status TEXT NOT NULL,
                provider_id TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._db.commit()
        self._initialized = True
        logger.info("control_plane_storage_initialized", db_path=self._db_path)

    # --- Settlement persistence ---

    def save_settlement_request(self, request: Any) -> None:
        if self._db is None:
            return
        payload = orjson.dumps(request.to_dict()).decode()
        now = request.resolved_at or request.created_at
        self._db.execute(
            """
            INSERT OR REPLACE INTO settlement_requests
            (settlement_id, data, status, provider_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                request.settlement_id,
                payload,
                request.status,
                request.payment.provider_id,
                request.created_at,
                now,
            ),
        )
        self._db.commit()

    def list_settlement_requests(
        self, *, status: str = "", limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        if status:
            rows = self._db.execute(
                "SELECT data FROM settlement_requests WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT data FROM settlement_requests ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [orjson.loads(row[0]) for row in rows]

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

    def save_product_job_record(self, record: Any) -> None:
        if self._db is None:
            return
        payload = orjson.dumps(record.to_dict()).decode()
        self._db.execute(
            """
            INSERT OR REPLACE INTO product_job_records
            (job_id, data, job_kind, status, workspace_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.job_id,
                payload,
                record.job_kind.value,
                record.status,
                record.workspace_id,
                record.created_at,
                record.updated_at,
            ),
        )
        self._db.commit()

    def load_product_job_record(self, job_id: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT data FROM product_job_records WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return orjson.loads(row[0])

    def list_product_job_records(
        self,
        *,
        job_kind: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        query = """
            SELECT data FROM product_job_records
            WHERE 1 = 1
        """
        params: list[Any] = []
        if job_kind:
            query += " AND job_kind = ?"
            params.append(job_kind)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(query, tuple(params)).fetchall()
        return [orjson.loads(row[0]) for row in rows]

    def save_artifact_retention_record(self, record: Any) -> None:
        if self._db is None:
            return
        payload = orjson.dumps(record.to_dict()).decode()
        self._db.execute(
            """
            INSERT OR REPLACE INTO artifact_retention_records
            (record_id, data, job_id, job_kind, artifact_kind, retention_policy_id, status, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_id,
                payload,
                record.job_id,
                record.job_kind.value,
                record.artifact_kind.value,
                record.retention_policy_id,
                record.status.value,
                record.expires_at,
                record.updated_at,
            ),
        )
        self._db.commit()

    def load_artifact_retention_record(self, record_id: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT data FROM artifact_retention_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            return None
        return orjson.loads(row[0])

    def list_artifact_retention_records(
        self,
        *,
        status: str = "",
        job_id: str = "",
        artifact_kind: str = "",
        retention_policy_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        query = """
            SELECT data FROM artifact_retention_records
            WHERE 1 = 1
        """
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if job_id:
            query += " AND job_id = ?"
            params.append(job_id)
        if artifact_kind:
            query += " AND artifact_kind = ?"
            params.append(artifact_kind)
        if retention_policy_id:
            query += " AND retention_policy_id = ?"
            params.append(retention_policy_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(query, tuple(params)).fetchall()
        return [orjson.loads(row[0]) for row in rows]

    def save_cost_ledger_entry(self, entry: Any) -> None:
        if self._db is None:
            return
        payload = orjson.dumps(entry.to_dict()).decode()
        self._db.execute(
            """
            INSERT OR REPLACE INTO cost_ledger_entries
            (entry_id, data, job_id, job_kind, recorded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                entry.entry_id,
                payload,
                entry.job_id,
                entry.job_kind.value,
                entry.recorded_at,
            ),
        )
        self._db.commit()

    def list_cost_ledger_entries(
        self,
        *,
        job_id: str = "",
        job_kind: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        query = """
            SELECT data FROM cost_ledger_entries
            WHERE 1 = 1
        """
        params: list[Any] = []
        if job_id:
            query += " AND job_id = ?"
            params.append(job_id)
        if job_kind:
            query += " AND job_kind = ?"
            params.append(job_kind)
        query += " ORDER BY recorded_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(query, tuple(params)).fetchall()
        return [orjson.loads(row[0]) for row in rows]

    # --- Recurring Workflow persistence ---

    def save_recurring_workflow(self, workflow: Any) -> None:
        if self._db is None:
            return
        from datetime import UTC, datetime
        payload = orjson.dumps(workflow.to_dict()).decode()
        now = datetime.now(UTC).isoformat()
        self._db.execute(
            """
            INSERT OR REPLACE INTO recurring_workflows
            (workflow_id, data, name, status, schedule, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow.workflow_id,
                payload,
                workflow.name,
                workflow.status,
                workflow.schedule,
                workflow.created_at,
                now,
            ),
        )
        self._db.commit()

    def list_recurring_workflows(
        self, *, status: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        if status:
            rows = self._db.execute(
                "SELECT data FROM recurring_workflows WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT data FROM recurring_workflows ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [orjson.loads(row[0]) for row in rows]

    def delete_recurring_workflow(self, workflow_id: str) -> None:
        if self._db is None:
            return
        self._db.execute(
            "DELETE FROM recurring_workflows WHERE workflow_id = ?",
            (workflow_id,),
        )
        self._db.commit()

    # --- Job Pipeline persistence ---

    def save_job_pipeline(self, pipeline: Any) -> None:
        if self._db is None:
            return
        from datetime import UTC, datetime
        payload = orjson.dumps(pipeline.to_dict()).decode()
        now = datetime.now(UTC).isoformat()
        self._db.execute(
            """
            INSERT OR REPLACE INTO job_pipelines
            (pipeline_id, data, name, status, triggered_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pipeline.pipeline_id,
                payload,
                pipeline.name,
                pipeline.status,
                pipeline.triggered_by,
                pipeline.created_at,
                now,
            ),
        )
        self._db.commit()

    def list_job_pipelines(
        self, *, status: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        if status:
            rows = self._db.execute(
                "SELECT data FROM job_pipelines WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT data FROM job_pipelines ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [orjson.loads(row[0]) for row in rows]

    # --- Hard-delete methods for automated pruning ---

    def _safe_delete(self, sql: str, params: tuple[str, ...]) -> int:
        """Execute a DELETE and return rowcount. Returns 0 if table missing."""
        if self._db is None:
            return 0
        import sqlite3
        try:
            cursor = self._db.execute(sql, params)
            self._db.commit()
            return cursor.rowcount
        except sqlite3.OperationalError:
            return 0

    def hard_delete_pruned_artifacts(self, older_than_days: int = 90) -> int:
        """Hard-delete artifact retention records that have been PRUNED for >N days."""
        from datetime import UTC, datetime, timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        return self._safe_delete(
            "DELETE FROM artifact_retention_records WHERE status = 'PRUNED' AND updated_at < ?",
            (cutoff,),
        )

    def hard_delete_old_traces(self, older_than_days: int = 90) -> int:
        """Hard-delete execution trace records older than N days."""
        from datetime import UTC, datetime, timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        return self._safe_delete(
            "DELETE FROM execution_trace_records WHERE created_at < ?",
            (cutoff,),
        )

    def hard_delete_old_plans(self, older_than_days: int = 365) -> int:
        """Hard-delete job plan records older than N days."""
        from datetime import UTC, datetime, timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        return self._safe_delete(
            "DELETE FROM job_plan_records WHERE updated_at < ?",
            (cutoff,),
        )

    def hard_delete_old_pipelines(self, older_than_days: int = 180) -> int:
        """Hard-delete completed/failed pipelines older than N days."""
        from datetime import UTC, datetime, timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        return self._safe_delete(
            "DELETE FROM job_pipelines WHERE status IN ('completed', 'failed') AND updated_at < ?",
            (cutoff,),
        )

    def get_stats(self) -> dict[str, Any]:
        if self._db is None:
            return {
                "plans": 0,
                "traces": 0,
                "deliveries": 0,
                "product_jobs": 0,
                "retained_artifacts": 0,
                "cost_entries": 0,
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
        product_jobs = self._db.execute(
            "SELECT COUNT(*) FROM product_job_records"
        ).fetchone()[0]
        retained_artifacts = self._db.execute(
            "SELECT COUNT(*) FROM artifact_retention_records"
        ).fetchone()[0]
        cost_entries = self._db.execute(
            "SELECT COUNT(*) FROM cost_ledger_entries"
        ).fetchone()[0]
        workflows = self._db.execute(
            "SELECT COUNT(*) FROM recurring_workflows"
        ).fetchone()[0]
        pipelines = self._db.execute(
            "SELECT COUNT(*) FROM job_pipelines"
        ).fetchone()[0]
        return {
            "plans": plans,
            "traces": traces,
            "deliveries": deliveries,
            "product_jobs": product_jobs,
            "retained_artifacts": retained_artifacts,
            "cost_entries": cost_entries,
            "recurring_workflows": workflows,
            "job_pipelines": pipelines,
        }

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        self._initialized = False
