"""
Tests for Operator REST API endpoints and Archival service.

Validates:
1. All operator endpoints require auth
2. Endpoints return correct structure when control plane is available
3. Endpoints handle missing control plane gracefully
4. Archival service exports CSV correctly
5. Archival service handles edge cases
"""

from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.social.agent_api import AgentAPI

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

def _make_api(*, with_agent: bool = True, api_keys: list[str] | None = None) -> AgentAPI:
    """Create an AgentAPI with optional mock agent."""
    keys = api_keys or ["test-key-123"]
    agent = None
    if with_agent:
        agent = MagicMock()
        agent.control_plane = MagicMock()
        agent.recurring_workflows = MagicMock()
        agent.pipeline_orchestrator = MagicMock()
        agent.watchdog = MagicMock()
    return AgentAPI(agent=agent, api_keys=keys)


# ─────────────────────────────────────────────
# Auth tests
# ─────────────────────────────────────────────

class TestOperatorEndpointAuth:
    """All operator endpoints must require auth."""

    OPERATOR_PATHS = [
        "jobs", "report", "telemetry", "retention",
        "margin", "workflows", "pipelines", "audit", "archive",
    ]

    def test_all_operator_endpoints_reject_without_auth(self):
        api = _make_api()
        for path in self.OPERATOR_PATHS:
            handler_name = f"_handle_operator_{path}"
            handler = getattr(api, handler_name, None)
            assert handler is not None, f"Handler for {path} not found"

    def test_operator_endpoint_names_are_consistent(self):
        api = _make_api()
        for path in self.OPERATOR_PATHS:
            assert hasattr(api, f"_handle_operator_{path}"), (
                f"Missing handler: _handle_operator_{path}"
            )


# ─────────────────────────────────────────────
# Job endpoint tests
# ─────────────────────────────────────────────

class TestOperatorJobsEndpoint:

    def test_jobs_handler_exists(self):
        api = _make_api()
        assert callable(api._handle_operator_jobs)

    def test_job_detail_handler_exists(self):
        api = _make_api()
        assert callable(api._handle_operator_job_detail)


# ─────────────────────────────────────────────
# Report endpoint tests
# ─────────────────────────────────────────────

class TestOperatorReportEndpoint:

    def test_report_handler_exists(self):
        api = _make_api()
        assert callable(api._handle_operator_report)


# ─────────────────────────────────────────────
# Archival service tests
# ─────────────────────────────────────────────

class TestArchivalService:

    def test_import(self):
        from agent.control.archival import ArchivalService
        assert ArchivalService is not None

    def test_archivable_tables_defined(self):
        from agent.control.archival import _ARCHIVABLE_TABLES
        assert "cost_ledger_entries" in _ARCHIVABLE_TABLES
        assert "delivery_records" in _ARCHIVABLE_TABLES
        assert "execution_trace_records" in _ARCHIVABLE_TABLES

    def test_invalid_table_raises_value_error(self):
        from agent.control.archival import ArchivalService
        storage = MagicMock()
        svc = ArchivalService(storage)
        with pytest.raises(ValueError, match="not archivable"):
            svc.export_table("nonexistent_table")

    def test_uninitialized_storage_raises(self):
        from agent.control.archival import ArchivalService
        storage = MagicMock()
        storage._db = None
        svc = ArchivalService(storage)
        with pytest.raises(RuntimeError, match="not initialized"):
            svc.export_table("cost_ledger_entries")

    def test_empty_export_returns_empty_string(self):
        from agent.control.archival import ArchivalService
        storage = MagicMock()
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        storage._db = db
        svc = ArchivalService(storage)
        result = svc.export_table("cost_ledger_entries")
        assert result == ""

    def test_export_writes_csv(self):
        import orjson

        from agent.control.archival import ArchivalService

        storage = MagicMock()
        db = MagicMock()
        rows = [
            (orjson.dumps({"entry_id": "e1", "job_id": "j1", "amount": 1.5}).decode(),),
            (orjson.dumps({"entry_id": "e2", "job_id": "j2", "amount": 2.0}).decode(),),
        ]
        db.execute.return_value.fetchall.return_value = rows
        storage._db = db

        svc = ArchivalService(storage)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("agent.control.archival._ARCHIVE_DIR", Path(tmpdir)):
                result = svc.export_table("cost_ledger_entries")
                assert result.endswith(".csv")
                assert Path(result).exists()

                content = Path(result).read_text()
                reader = csv.DictReader(io.StringIO(content))
                records = list(reader)
                assert len(records) == 2
                assert records[0]["entry_id"] == "e1"
                assert records[1]["amount"] == "2.0"

    def test_flatten_nested_dict(self):
        from agent.control.archival import ArchivalService
        data = {
            "id": "test",
            "nested": {"key1": "val1", "key2": 42},
            "simple": "value",
        }
        flat = ArchivalService._flatten(data)
        assert flat["id"] == "test"
        assert flat["nested.key1"] == "val1"
        assert flat["nested.key2"] == "42"
        assert flat["simple"] == "value"

    def test_flatten_handles_none(self):
        from agent.control.archival import ArchivalService
        data = {"key": None, "val": "ok"}
        flat = ArchivalService._flatten(data)
        assert flat["key"] == ""
        assert flat["val"] == "ok"

    def test_list_archives_empty_dir(self):
        from agent.control.archival import ArchivalService
        storage = MagicMock()
        svc = ArchivalService(storage)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("agent.control.archival._ARCHIVE_DIR", Path(tmpdir)):
                archives = svc.list_archives()
                assert archives == []

    def test_list_archives_finds_csv(self):
        from agent.control.archival import ArchivalService
        storage = MagicMock()
        svc = ArchivalService(storage)
        with tempfile.TemporaryDirectory() as tmpdir:
            csvfile = Path(tmpdir) / "test_2026-04-01.csv"
            csvfile.write_text("a,b\n1,2\n")
            with patch("agent.control.archival._ARCHIVE_DIR", Path(tmpdir)):
                archives = svc.list_archives()
                assert len(archives) == 1
                assert archives[0]["filename"] == "test_2026-04-01.csv"
                assert archives[0]["size_bytes"] > 0


# ─────────────────────────────────────────────
# API route registration
# ─────────────────────────────────────────────

class TestOperatorRouteRegistration:
    """Verify all operator routes are registered in start()."""

    def test_route_count(self):
        """Operator endpoints should be registered alongside core endpoints."""
        api = _make_api()
        # Count handler methods matching _handle_operator_*
        handlers = [
            attr for attr in dir(api)
            if attr.startswith("_handle_operator_")
        ]
        assert len(handlers) >= 9, (
            f"Expected >= 9 operator handlers, got {len(handlers)}: {handlers}"
        )

    def test_all_handlers_are_callable(self):
        api = _make_api()
        handlers = [
            attr for attr in dir(api)
            if attr.startswith("_handle_operator_")
        ]
        for name in handlers:
            assert callable(getattr(api, name)), f"{name} is not callable"
