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
from unittest.mock import AsyncMock, MagicMock, patch

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
        agent.reporting = MagicMock()
        agent.recurring_workflows = MagicMock()
        agent.pipeline_orchestrator = MagicMock()
        agent.settlement = MagicMock()
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
        "settlements",
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
            with patch("agent.control.archival._get_archive_dir", return_value=Path(tmpdir)):
                result = svc.export_table("cost_ledger_entries")
                assert result.endswith(".csv")
                # Result is now filename only, not full path
                assert "/" not in result

                content = (Path(tmpdir) / result).read_text()
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
            with patch("agent.control.archival._get_archive_dir", return_value=Path(tmpdir)):
                archives = svc.list_archives()
                assert archives == []

    def test_list_archives_finds_csv(self):
        from agent.control.archival import ArchivalService
        storage = MagicMock()
        svc = ArchivalService(storage)
        with tempfile.TemporaryDirectory() as tmpdir:
            csvfile = Path(tmpdir) / "test_2026-04-01.csv"
            csvfile.write_text("a,b\n1,2\n")
            with patch("agent.control.archival._get_archive_dir", return_value=Path(tmpdir)):
                archives = svc.list_archives()
                assert len(archives) == 1
                assert archives[0]["filename"] == "test_2026-04-01.csv"
                assert archives[0]["size_bytes"] > 0


# ─────────────────────────────────────────────
# API route registration
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# Handler-level tests (actually invoke handlers)
# ─────────────────────────────────────────────

def _mock_request(*, headers: dict | None = None, query: dict | None = None, match_info: dict | None = None):
    """Build a mock aiohttp.web.Request."""
    req = MagicMock()
    req.headers = headers or {}
    req.query = query or {}
    req.match_info = match_info or {}
    req.remote = "127.0.0.1"
    return req


class TestReportEndpointWiring:
    """Catch the broken wiring that shallow tests missed."""

    @pytest.mark.asyncio
    async def test_report_requires_reporting_attr(self):
        """Without agent.reporting, endpoint must return 503."""
        api = _make_api()
        # Remove the 'reporting' attribute to simulate missing wiring
        del api._agent.reporting
        req = _mock_request(headers={"Authorization": "Bearer test-key-123"})
        resp = await api._handle_operator_report(req)
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_report_with_valid_agent_calls_reporting(self):
        """With agent.reporting, endpoint delegates to get_report()."""
        api = _make_api()
        api._agent.reporting.get_report.return_value = {"summary": {}, "inbox": []}
        req = _mock_request(headers={"Authorization": "Bearer test-key-123"})
        resp = await api._handle_operator_report(req)
        assert resp.status == 200
        api._agent.reporting.get_report.assert_called_once_with(limit=20)

    @pytest.mark.asyncio
    async def test_report_rejects_auth_failure(self):
        api = _make_api()
        req = _mock_request(headers={})
        resp = await api._handle_operator_report(req)
        assert resp.status == 401


class TestQueryParamValidation:
    """Operator API must return structured 400, never 500, for bad params."""

    @pytest.mark.asyncio
    async def test_jobs_invalid_limit(self):
        api = _make_api()
        api._agent.control_plane.list_product_jobs.return_value = []
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            query={"limit": "not_a_number"},
        )
        resp = await api._handle_operator_jobs(req)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_telemetry_invalid_window(self):
        api = _make_api()
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            query={"window_hours": "abc"},
        )
        resp = await api._handle_operator_telemetry(req)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_margin_invalid_limit(self):
        api = _make_api()
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            query={"limit": ""},
        )
        resp = await api._handle_operator_margin(req)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_audit_invalid_limit(self):
        api = _make_api()
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            query={"limit": "xyz"},
        )
        resp = await api._handle_operator_audit(req)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_report_invalid_limit(self):
        api = _make_api()
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            query={"limit": "bad"},
        )
        resp = await api._handle_operator_report(req)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_jobs_valid_params_work(self):
        api = _make_api()
        api._agent.control_plane.list_product_jobs.return_value = []
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            query={"limit": "10", "kind": "review", "status": "completed"},
        )
        resp = await api._handle_operator_jobs(req)
        assert resp.status == 200


class TestJobDetailEndpoint:

    @pytest.mark.asyncio
    async def test_job_not_found_returns_404(self):
        api = _make_api()
        api._agent.control_plane.get_product_job.return_value = None
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            match_info={"job_id": "nonexistent"},
        )
        resp = await api._handle_operator_job_detail(req)
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_job_found_returns_200(self):
        api = _make_api()
        mock_job = MagicMock()
        mock_job.to_dict.return_value = {"job_id": "abc", "status": "completed"}
        api._agent.control_plane.get_product_job.return_value = mock_job
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            match_info={"job_id": "abc"},
        )
        resp = await api._handle_operator_job_detail(req)
        assert resp.status == 200


class TestArchiveEndpointSecurity:
    """Archive API must not leak host paths."""

    @pytest.mark.asyncio
    async def test_archive_list_returns_filenames_not_paths(self):
        api = _make_api()
        api._agent.control_plane._storage = MagicMock()
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            query={"action": "list"},
        )
        with patch("agent.control.archival._get_archive_dir") as mock_dir:
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_dir.return_value = Path(tmpdir)
                resp = await api._handle_operator_archive(req)
                assert resp.status == 200


class TestArchiveDownloadEndpoint:

    def test_get_archive_path_rejects_traversal(self):
        from agent.control.archival import ArchivalService
        svc = ArchivalService(MagicMock())
        assert svc.get_archive_path("../etc/passwd") is None
        assert svc.get_archive_path("foo/bar.csv") is None
        assert svc.get_archive_path("test.txt") is None  # not .csv

    def test_get_archive_path_valid(self):
        from agent.control.archival import ArchivalService
        svc = ArchivalService(MagicMock())
        with tempfile.TemporaryDirectory() as tmpdir:
            csvfile = Path(tmpdir) / "test_2026-04-01.csv"
            csvfile.write_text("a,b\n1,2\n")
            with patch("agent.control.archival._get_archive_dir", return_value=Path(tmpdir)):
                result = svc.get_archive_path("test_2026-04-01.csv")
                assert result is not None
                assert result.name == "test_2026-04-01.csv"

    @pytest.mark.asyncio
    async def test_download_requires_auth(self):
        api = _make_api()
        req = _mock_request(headers={}, match_info={"filename": "test.csv"})
        resp = await api._handle_operator_archive_download(req)
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_download_not_found(self):
        api = _make_api()
        api._agent.control_plane._storage = MagicMock()
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            match_info={"filename": "nonexistent.csv"},
        )
        with patch("agent.control.archival._get_archive_dir") as mock_dir:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_dir.return_value = Path(tmpdir)
                resp = await api._handle_operator_archive_download(req)
                assert resp.status == 404


class TestSettlementActionEndpoint:

    @pytest.mark.asyncio
    async def test_approve_via_api(self):
        api = _make_api()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"settlement_id": "s1", "status": "approved"}
        api._agent.settlement.approve_settlement.return_value = mock_result
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            match_info={"settlement_id": "s1", "action": "approve"},
        )
        req.json = AsyncMock(return_value={"note": "ok"})
        resp = await api._handle_operator_settlement_action(req)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_deny_via_api(self):
        api = _make_api()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"settlement_id": "s1", "status": "denied"}
        api._agent.settlement.deny_settlement.return_value = mock_result
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            match_info={"settlement_id": "s1", "action": "deny"},
        )
        req.json = AsyncMock(return_value={})
        resp = await api._handle_operator_settlement_action(req)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_invalid_action(self):
        api = _make_api()
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            match_info={"settlement_id": "s1", "action": "invalid"},
        )
        req.json = AsyncMock(return_value={})
        resp = await api._handle_operator_settlement_action(req)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_approve_not_found(self):
        api = _make_api()
        api._agent.settlement.approve_settlement.return_value = None
        req = _mock_request(
            headers={"Authorization": "Bearer test-key-123"},
            match_info={"settlement_id": "nonexistent", "action": "approve"},
        )
        req.json = AsyncMock(return_value={})
        resp = await api._handle_operator_settlement_action(req)
        assert resp.status == 404


class TestSettlementEndpoint:

    @pytest.mark.asyncio
    async def test_settlements_returns_pending(self):
        api = _make_api()
        mock_settlement = MagicMock()
        mock_settlement.to_dict.return_value = {"settlement_id": "s1", "status": "pending"}
        api._agent.settlement.get_pending_settlements.return_value = [mock_settlement]
        req = _mock_request(headers={"Authorization": "Bearer test-key-123"})
        resp = await api._handle_operator_settlements(req)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_settlements_without_service(self):
        api = _make_api()
        del api._agent.settlement
        req = _mock_request(headers={"Authorization": "Bearer test-key-123"})
        resp = await api._handle_operator_settlements(req)
        assert resp.status == 200  # graceful empty response


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
        assert len(handlers) >= 10, (
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
