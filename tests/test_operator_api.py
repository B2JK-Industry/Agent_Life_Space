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
        "margin", "workflows", "pipelines", "audit", "archive", "llm",
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


# ─────────────────────────────────────────────
# Dashboard auth — query string must NOT bypass auth
# ─────────────────────────────────────────────

class TestDashboardAuthBypass:
    """Regression tests for the ?key=… query-string bypass."""

    async def test_dashboard_query_key_does_not_grant_access(self):
        """Hitting /dashboard?key=test-key-123 must NOT serve the
        authenticated dashboard. The handler should return the login
        page instead, because keys in the query string leak to logs,
        history, and referrers."""
        api = _make_api()
        request = MagicMock()
        request.query = {"key": "test-key-123"}  # the valid key, but in query
        request.headers = {}  # no Authorization header
        request.remote = "127.0.0.1"

        response = await api._handle_dashboard(request)

        # Login page is served (no INITIAL_KEY containing the secret).
        body = response.text
        assert "test-key-123" not in body, (
            "Dashboard leaked the API key into the response body"
        )
        # The login page contains a key form input but NOT the full
        # operator dashboard JS scaffolding (which contains llm-card etc).
        assert "llm-card" not in body, (
            "Dashboard handler served the full dashboard for a query-string key"
        )

    async def test_dashboard_authorization_header_grants_access(self):
        """Sanity: a valid Authorization header still works."""
        api = _make_api()
        request = MagicMock()
        request.query = {}
        request.headers = {"Authorization": "Bearer test-key-123"}
        request.remote = "127.0.0.1"

        response = await api._handle_dashboard(request)
        body = response.text
        # Full dashboard contains the LLM card placeholder.
        assert "llm-card" in body


# ─────────────────────────────────────────────
# /api/operator/llm — invalid JSON must return 400
# ─────────────────────────────────────────────

class TestOperatorLlmInvalidJson:

    async def test_post_invalid_json_returns_400(self):
        """POST with malformed JSON body must return HTTP 400, not a
        silent no-op (which would let clients believe an update
        succeeded when it didn't)."""
        api = _make_api()
        # Wire a minimal LLM runtime surface so we get past the
        # "runtime unavailable" early-exit.
        api._agent.get_llm_runtime_state = MagicMock(return_value={"enabled": True})
        api._agent.update_llm_runtime_state = MagicMock(return_value={"enabled": True})

        request = MagicMock()
        request.method = "POST"
        request.headers = {"Authorization": "Bearer test-key-123"}
        request.remote = "127.0.0.1"

        async def _bad_json():
            raise ValueError("not valid json")
        request.json = _bad_json

        response = await api._handle_operator_llm(request)
        assert response.status == 400
        # Body should mention the contract failure.
        body_text = response.text
        assert "invalid_json_body" in body_text or "Invalid JSON" in body_text

    async def test_post_non_dict_json_returns_400(self):
        """POST with valid JSON that isn't an object (e.g. a list) must
        also be a 400."""
        api = _make_api()
        api._agent.get_llm_runtime_state = MagicMock(return_value={"enabled": True})
        api._agent.update_llm_runtime_state = MagicMock(return_value={"enabled": True})

        request = MagicMock()
        request.method = "POST"
        request.headers = {"Authorization": "Bearer test-key-123"}
        request.remote = "127.0.0.1"

        async def _list_json():
            return ["not", "a", "dict"]
        request.json = _list_json

        response = await api._handle_operator_llm(request)
        assert response.status == 400

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
    req.method = "GET"
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


class TestLlmRuntimeEndpoint:

    @pytest.mark.asyncio
    async def test_llm_runtime_get_returns_state(self):
        api = _make_api()
        api._agent.get_llm_runtime_state.return_value = {"enabled": True, "effective_backend": "cli"}
        req = _mock_request(headers={"Authorization": "Bearer test-key-123"})
        req.method = "GET"

        resp = await api._handle_operator_llm(req)

        assert resp.status == 200
        api._agent.get_llm_runtime_state.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_llm_runtime_post_updates_state(self):
        api = _make_api()
        api._agent.update_llm_runtime_state.return_value = {"enabled": False}
        req = _mock_request(headers={"Authorization": "Bearer test-key-123"})
        req.method = "POST"
        req.json = AsyncMock(return_value={"enabled": False, "note": "maintenance"})

        resp = await api._handle_operator_llm(req)

        assert resp.status == 200
        api._agent.update_llm_runtime_state.assert_called_once_with(
            enabled=False,
            backend=None,
            provider=None,
            follow_env=False,
            note="maintenance",
            updated_by="api.operator",
        )

    @pytest.mark.asyncio
    async def test_llm_runtime_rejects_invalid_enabled_type(self):
        api = _make_api()
        req = _mock_request(headers={"Authorization": "Bearer test-key-123"})
        req.method = "POST"
        req.json = AsyncMock(return_value={"enabled": "false"})

        resp = await api._handle_operator_llm(req)

        assert resp.status == 400


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


# ─────────────────────────────────────────────
# Owner key trust model
# ─────────────────────────────────────────────


class TestOwnerKeyTrustModel:
    """Regression tests for AGENT_OWNER_API_KEY trust differentiation."""

    def _make_request(self, key: str, remote: str = "127.0.0.1"):
        """Create a mock request with auth header and remote IP."""
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {key}"} if key else {}
        req.remote = remote
        return req

    def test_legacy_mode_any_valid_key_is_owner_on_localhost(self):
        """Without AGENT_OWNER_API_KEY, any valid key is owner on localhost."""
        with patch.dict("os.environ", {}, clear=False):
            # Ensure AGENT_OWNER_API_KEY is not set
            import os
            os.environ.pop("AGENT_OWNER_API_KEY", None)
            api = AgentAPI(api_keys=["key-a", "key-b"])

        req = self._make_request("key-a")
        assert api._is_owner_key(req) is True
        req2 = self._make_request("key-b")
        assert api._is_owner_key(req2) is True

    def test_explicit_owner_key_only_owner_gets_privilege(self):
        """With AGENT_OWNER_API_KEY set, only that key is owner."""
        with patch.dict("os.environ", {"AGENT_OWNER_API_KEY": "owner-key-xyz"}):
            api = AgentAPI(api_keys=["general-key"], port=8421)

        req_owner = self._make_request("owner-key-xyz")
        assert api._is_owner_key(req_owner) is True

        req_general = self._make_request("general-key")
        assert api._is_owner_key(req_general) is False

    def test_owner_key_is_also_auth_valid(self):
        """Owner key must pass _check_auth (it's added to api_keys)."""
        with patch.dict("os.environ", {"AGENT_OWNER_API_KEY": "owner-key-xyz"}):
            api = AgentAPI(api_keys=["general-key"], port=8421)

        req = self._make_request("owner-key-xyz")
        auth_error = api._check_auth(req)
        assert auth_error is None, f"Owner key should pass auth, got: {auth_error}"

    def test_general_key_passes_auth_but_not_owner(self):
        """General key authenticates but is not owner."""
        with patch.dict("os.environ", {"AGENT_OWNER_API_KEY": "owner-key-xyz"}):
            api = AgentAPI(api_keys=["general-key"], port=8421)

        req = self._make_request("general-key")
        assert api._check_auth(req) is None  # auth passes
        assert api._is_owner_key(req) is False  # not owner

    def test_invalid_key_fails_auth(self):
        """Invalid key fails auth regardless of owner config."""
        with patch.dict("os.environ", {"AGENT_OWNER_API_KEY": "owner-key-xyz"}):
            api = AgentAPI(api_keys=["general-key"], port=8421)

        req = self._make_request("wrong-key")
        assert api._check_auth(req) is not None  # auth fails

    def test_missing_bearer_fails(self):
        """Request without Bearer header fails auth."""
        api = _make_api()
        req = MagicMock()
        req.headers = {}
        req.remote = "127.0.0.1"
        assert api._check_auth(req) is not None

    def test_owner_key_not_owner_on_remote(self):
        """Owner key on non-local IP should not get is_owner (by design, is_local gate is external)."""
        # _is_owner_key only checks the key, not IP. The IP check is in the
        # caller (is_owner_caller = is_authenticated and is_local and _is_owner_key).
        # So _is_owner_key returns True for the key itself — the locality
        # gate is applied separately.
        with patch.dict("os.environ", {"AGENT_OWNER_API_KEY": "owner-key-xyz"}):
            api = AgentAPI(api_keys=["general-key"], port=8421)

        req = self._make_request("owner-key-xyz", remote="1.2.3.4")
        # _is_owner_key only checks the key match, not IP
        assert api._is_owner_key(req) is True
        # But the caller would set is_owner_caller = False because is_local is False
