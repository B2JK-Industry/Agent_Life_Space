"""
Tests for finance risk templates and audit export.
"""

from __future__ import annotations

from agent.finance.risk_templates import (
    RISK_TEMPLATES,
    export_audit_trail,
    get_template,
    list_templates,
    validate_against_template,
)


class TestRiskTemplates:
    """Risk templates provide expense validation."""

    def test_all_templates_present(self):
        expected = ["api_subscription", "cloud_compute", "domain_hosting",
                     "tool_license", "llm_api_usage", "data_purchase"]
        for cat in expected:
            assert cat in RISK_TEMPLATES

    def test_get_template(self):
        t = get_template("api_subscription")
        assert t is not None
        assert t.risk_level == "medium"

    def test_get_nonexistent(self):
        assert get_template("nonexistent") is None

    def test_validate_within_range(self):
        result = validate_against_template("api_subscription", 10.0)
        assert result["template_found"]
        assert result["within_typical_range"]
        assert len(result["warnings"]) == 0

    def test_validate_over_max(self):
        result = validate_against_template("api_subscription", 100.0)
        assert not result["within_typical_range"]
        assert len(result["warnings"]) == 1
        assert "nad typickým maximom" in result["warnings"][0]

    def test_validate_under_min(self):
        result = validate_against_template("cloud_compute", 0.01)
        assert not result["within_typical_range"]
        assert "pod typickým minimom" in result["warnings"][0]

    def test_validate_unknown_category(self):
        result = validate_against_template("unknown", 10.0)
        assert not result["template_found"]

    def test_list_templates(self):
        templates = list_templates()
        assert len(templates) >= 6
        assert all("category" in t for t in templates)


class TestAuditExport:
    """Audit trail can be exported as CSV."""

    def test_export_empty(self):
        csv = export_audit_trail([])
        assert "id,type,status" in csv

    def test_export_with_data(self):
        txs = [
            {"id": "tx1", "type": "expense", "status": "completed",
             "amount_usd": 10.0, "description": "API sub", "category": "api",
             "created_at": "2026-03-26"},
        ]
        csv = export_audit_trail(txs)
        assert "tx1" in csv
        assert "10.0" in csv

    def test_export_escapes_commas(self):
        txs = [
            {"id": "tx2", "description": "test, with comma"},
        ]
        csv = export_audit_trail(txs)
        assert '"test, with comma"' in csv
