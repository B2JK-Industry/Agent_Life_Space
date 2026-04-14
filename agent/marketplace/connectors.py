"""
Agent Life Space — Marketplace Connector Protocol

Each platform connector normalizes its API into the common
Opportunity/Bid models. The connector protocol defines what
every platform adapter must implement.

Connectors do NOT make HTTP calls directly — they delegate to
the existing ExternalGatewayService which handles auth, rate
limiting, retries, and 402 payment flows.
"""

from __future__ import annotations

from typing import Any, Protocol

from agent.marketplace.models import Bid, Evaluation, Opportunity


class MarketplaceConnector(Protocol):
    """Protocol that every marketplace connector must implement."""

    @property
    def platform_id(self) -> str:
        """Unique platform identifier, e.g. 'obolos.tech'."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable platform name."""
        ...

    async def fetch_opportunities(
        self, gateway: Any, *, category: str = "", limit: int = 20,
    ) -> list[Opportunity]:
        """Fetch available opportunities from the platform catalog."""
        ...

    async def fetch_opportunity_detail(
        self, gateway: Any, platform_id: str,
    ) -> Opportunity | None:
        """Fetch full detail for a single opportunity."""
        ...

    def evaluate_opportunity(
        self, opportunity: Opportunity, agent_capabilities: list[str],
    ) -> Evaluation:
        """Deterministic feasibility check against agent capabilities."""
        ...

    def prepare_bid(
        self, opportunity: Opportunity, evaluation: Evaluation,
    ) -> Bid:
        """Draft a bid/response for a feasible opportunity."""
        ...

    async def submit_bid(
        self, gateway: Any, bid: Bid,
    ) -> dict[str, Any]:
        """Submit bid to the platform. Returns platform response."""
        ...


class ConnectorRegistry:
    """Registry of available marketplace connectors."""

    def __init__(self) -> None:
        self._connectors: dict[str, MarketplaceConnector] = {}

    def register(self, connector: MarketplaceConnector) -> None:
        self._connectors[connector.platform_id] = connector

    def get(self, platform_id: str) -> MarketplaceConnector | None:
        return self._connectors.get(platform_id)

    def list_platforms(self) -> list[str]:
        return list(self._connectors.keys())

    def all(self) -> list[MarketplaceConnector]:
        return list(self._connectors.values())
