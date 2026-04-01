"""
Agent Life Space — Control-Plane Archival Service

Exports control-plane data to CSV before hard-delete.
Ensures compliance-grade audit trail for cost ledger,
delivery evidence, and operational traces.

Usage:
    archival = ArchivalService(storage)
    path = archival.export_table("cost_ledger_entries", older_than_days=730)
    # → agent/archive/cost_ledger_entries_2026-04-01.csv
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import orjson
import structlog

from agent.core.paths import get_project_root

logger = structlog.get_logger(__name__)


def _get_archive_dir() -> Path:
    """Resolve archive directory inside the data area (not repo source tree)."""
    return Path(get_project_root()) / "data" / "archive"

# Tables eligible for archival with their date column
_ARCHIVABLE_TABLES: dict[str, str] = {
    "cost_ledger_entries": "recorded_at",
    "delivery_records": "updated_at",
    "execution_trace_records": "created_at",
    "job_plan_records": "updated_at",
    "artifact_retention_records": "updated_at",
}


class ArchivalService:
    """Export control-plane table data to CSV before hard-delete."""

    def __init__(self, storage: Any) -> None:
        self._storage = storage

    def export_table(
        self,
        table_name: str,
        *,
        older_than_days: int = 0,
        limit: int = 50000,
    ) -> str:
        """Export rows from a table to CSV. Returns the output file path.

        If older_than_days > 0, only exports rows older than that threshold.
        """
        if table_name not in _ARCHIVABLE_TABLES:
            raise ValueError(
                f"Table '{table_name}' is not archivable. "
                f"Allowed: {', '.join(sorted(_ARCHIVABLE_TABLES))}"
            )

        db = self._storage._db
        if db is None:
            raise RuntimeError("Storage not initialized")

        date_col = _ARCHIVABLE_TABLES[table_name]
        query = f"SELECT data FROM {table_name}"  # noqa: S608
        params: list[Any] = []

        if older_than_days > 0:
            cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
            query += f" WHERE {date_col} < ?"
            params.append(cutoff)

        query += f" ORDER BY {date_col} ASC LIMIT ?"
        params.append(limit)

        rows = db.execute(query, tuple(params)).fetchall()
        if not rows:
            logger.info("archival_empty", table=table_name, older_than_days=older_than_days)
            return ""

        # Parse JSON data from each row
        records: list[dict[str, Any]] = []
        for row in rows:
            try:
                records.append(orjson.loads(row[0]))
            except Exception:  # noqa: S112
                continue

        if not records:
            return ""

        # Flatten nested dicts for CSV (one level deep)
        flat_records = [self._flatten(r) for r in records]

        # Collect all column names across all records
        all_keys: list[str] = []
        seen: set[str] = set()
        for rec in flat_records:
            for k in rec:
                if k not in seen:
                    all_keys.append(k)
                    seen.add(k)

        # Write CSV
        archive_dir = _get_archive_dir()
        archive_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        filename = f"{table_name}_{date_str}.csv"
        filepath = archive_dir / filename

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        for rec in flat_records:
            writer.writerow(rec)

        filepath.write_text(buf.getvalue(), encoding="utf-8")
        logger.info(
            "archival_exported",
            table=table_name,
            rows=len(flat_records),
            filename=filename,
        )
        # Return only the filename — never expose host filesystem paths in API responses
        return filename

    def list_archives(self) -> list[dict[str, Any]]:
        """List all existing archive files."""
        archive_dir = _get_archive_dir()
        if not archive_dir.exists():
            return []
        archives = []
        for f in sorted(archive_dir.glob("*.csv")):
            stat = f.stat()
            archives.append({
                "filename": f.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_ctime, tz=UTC).isoformat(),
            })
        return archives

    @staticmethod
    def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
        """Flatten a nested dict one level deep for CSV export."""
        result: dict[str, str] = {}
        for key, value in data.items():
            flat_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if isinstance(value, dict):
                for k2, v2 in value.items():
                    result[f"{flat_key}.{k2}"] = str(v2)
            elif isinstance(value, list):
                result[flat_key] = str(value)
            else:
                result[flat_key] = str(value) if value is not None else ""
        return result
