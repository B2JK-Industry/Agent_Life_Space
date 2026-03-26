"""
Agent Life Space — Finance Risk Templates

Pre-defined templates for common expense categories.
Each template has: category, typical amount range, risk level,
approval requirement, and description.

Templates help the agent propose expenses consistently
and help the owner understand what's being proposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RiskTemplate:
    """Pre-defined risk template for an expense category."""

    category: str
    description: str
    typical_min_usd: float
    typical_max_usd: float
    risk_level: str  # low, medium, high, critical
    requires_approval: bool
    notes: str = ""


# Pre-defined templates for common agent expenses
RISK_TEMPLATES: dict[str, RiskTemplate] = {
    "api_subscription": RiskTemplate(
        category="api_subscription",
        description="API služba (OpenAI, hosting, monitoring)",
        typical_min_usd=0.50,
        typical_max_usd=50.0,
        risk_level="medium",
        requires_approval=True,
        notes="Recurring — overiť mesačný limit",
    ),
    "cloud_compute": RiskTemplate(
        category="cloud_compute",
        description="Cloud výpočtový výkon (VM, GPU, storage)",
        typical_min_usd=1.0,
        typical_max_usd=100.0,
        risk_level="high",
        requires_approval=True,
        notes="Môže eskalovať — sledovať usage",
    ),
    "domain_hosting": RiskTemplate(
        category="domain_hosting",
        description="Doména alebo hosting",
        typical_min_usd=5.0,
        typical_max_usd=50.0,
        risk_level="low",
        requires_approval=True,
    ),
    "tool_license": RiskTemplate(
        category="tool_license",
        description="Softvérová licencia alebo nástroj",
        typical_min_usd=0.0,
        typical_max_usd=30.0,
        risk_level="low",
        requires_approval=True,
    ),
    "llm_api_usage": RiskTemplate(
        category="llm_api_usage",
        description="LLM API usage (tokeny, volania)",
        typical_min_usd=0.01,
        typical_max_usd=20.0,
        risk_level="medium",
        requires_approval=False,
        notes="Auto-tracked cez usage monitoring",
    ),
    "data_purchase": RiskTemplate(
        category="data_purchase",
        description="Nákup dát alebo datasetu",
        typical_min_usd=5.0,
        typical_max_usd=200.0,
        risk_level="high",
        requires_approval=True,
        notes="Overiť licenciu a kvalitu pred nákupom",
    ),
}


def get_template(category: str) -> RiskTemplate | None:
    """Get risk template for a category."""
    return RISK_TEMPLATES.get(category)


def validate_against_template(
    category: str, amount: float
) -> dict[str, Any]:
    """
    Validate expense against template.
    Returns warnings if amount is outside typical range.
    """
    template = RISK_TEMPLATES.get(category)
    if not template:
        return {
            "valid": True,
            "template_found": False,
            "warnings": [],
        }

    warnings = []
    if amount < template.typical_min_usd:
        warnings.append(
            f"Suma ${amount:.2f} je pod typickým minimom ${template.typical_min_usd:.2f} "
            f"pre {template.description}"
        )
    if amount > template.typical_max_usd:
        warnings.append(
            f"Suma ${amount:.2f} je nad typickým maximom ${template.typical_max_usd:.2f} "
            f"pre {template.description}"
        )

    return {
        "valid": True,
        "template_found": True,
        "category": category,
        "risk_level": template.risk_level,
        "requires_approval": template.requires_approval,
        "within_typical_range": template.typical_min_usd <= amount <= template.typical_max_usd,
        "warnings": warnings,
    }


def list_templates() -> list[dict[str, Any]]:
    """List all available risk templates."""
    return [
        {
            "category": t.category,
            "description": t.description,
            "typical_range": f"${t.typical_min_usd:.2f}–${t.typical_max_usd:.2f}",
            "risk_level": t.risk_level,
            "requires_approval": t.requires_approval,
        }
        for t in RISK_TEMPLATES.values()
    ]


def export_audit_trail(transactions: list[dict[str, Any]]) -> str:
    """
    Export financial audit trail as CSV string.
    For external auditing/reporting.
    """
    if not transactions:
        return "id,type,status,amount_usd,description,category,created_at\n"

    lines = ["id,type,status,amount_usd,description,category,created_at"]
    for tx in transactions:
        line = ",".join([
            tx.get("id", ""),
            tx.get("type", ""),
            tx.get("status", ""),
            str(tx.get("amount_usd", 0)),
            f'"{tx.get("description", "")}"',
            tx.get("category", ""),
            tx.get("created_at", ""),
        ])
        lines.append(line)
    return "\n".join(lines)
