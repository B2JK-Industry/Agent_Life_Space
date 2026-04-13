"""
Agent Life Space — Web Monitor Models

Item model for extracted web content, snapshot for diffing,
filter spec for narrowing results.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class MonitorItem:
    """Single extracted item from a web page."""

    title: str = ""
    url: str = ""
    summary: str = ""
    raw_fields: dict[str, Any] = field(default_factory=dict)
    # Optional structured fields
    price: float | None = None
    price_currency: str = ""
    location: str = ""
    published_at: str = ""

    @property
    def fingerprint(self) -> str:
        """Stable identity hash from key fields."""
        parts = [self.title, self.url, str(self.price or ""), self.location]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "price": self.price,
            "price_currency": self.price_currency,
            "location": self.location,
            "published_at": self.published_at,
            "raw_fields": self.raw_fields,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MonitorItem:
        return cls(
            title=d.get("title", ""),
            url=d.get("url", ""),
            summary=d.get("summary", ""),
            raw_fields=d.get("raw_fields", {}),
            price=d.get("price"),
            price_currency=d.get("price_currency", ""),
            location=d.get("location", ""),
            published_at=d.get("published_at", ""),
        )


@dataclass
class FilterSpec:
    """Filter rules for narrowing extracted items."""

    title_contains: str = ""
    title_not_contains: str = ""
    price_max: float | None = None
    price_min: float | None = None
    location_contains: str = ""

    def matches(self, item: MonitorItem) -> bool:
        if self.title_contains and self.title_contains.lower() not in item.title.lower():
            return False
        if self.title_not_contains and self.title_not_contains.lower() in item.title.lower():
            return False
        if self.price_max is not None and item.price is not None and item.price > self.price_max:
            return False
        if self.price_min is not None and item.price is not None and item.price < self.price_min:
            return False
        if self.location_contains and self.location_contains.lower() not in item.location.lower():
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "title_contains": self.title_contains,
            "title_not_contains": self.title_not_contains,
            "price_max": self.price_max,
            "price_min": self.price_min,
            "location_contains": self.location_contains,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FilterSpec:
        return cls(
            title_contains=d.get("title_contains", ""),
            title_not_contains=d.get("title_not_contains", ""),
            price_max=d.get("price_max"),
            price_min=d.get("price_min"),
            location_contains=d.get("location_contains", ""),
        )


@dataclass
class Snapshot:
    """Point-in-time snapshot of extracted items."""

    url: str = ""
    items: list[MonitorItem] = field(default_factory=list)
    taken_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    item_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "items": [i.to_dict() for i in self.items],
            "taken_at": self.taken_at,
            "item_count": self.item_count or len(self.items),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Snapshot:
        return cls(
            url=d.get("url", ""),
            items=[MonitorItem.from_dict(i) for i in d.get("items", [])],
            taken_at=d.get("taken_at", ""),
            item_count=d.get("item_count", 0),
        )


@dataclass
class DiffResult:
    """Comparison of current vs previous snapshot."""

    new_items: list[MonitorItem] = field(default_factory=list)
    removed_items: list[MonitorItem] = field(default_factory=list)
    total_current: int = 0
    total_previous: int = 0

    @property
    def has_changes(self) -> bool:
        return len(self.new_items) > 0 or len(self.removed_items) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_items": [i.to_dict() for i in self.new_items],
            "removed_items": [i.to_dict() for i in self.removed_items],
            "total_current": self.total_current,
            "total_previous": self.total_previous,
            "new_count": len(self.new_items),
            "removed_count": len(self.removed_items),
        }


@dataclass
class MonitorConfig:
    """Persisted configuration for a monitoring job."""

    monitor_id: str = ""
    name: str = ""
    url: str = ""
    schedule: str = "daily"
    filters: FilterSpec = field(default_factory=FilterSpec)
    project_id: str = ""
    owner_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitor_id": self.monitor_id,
            "name": self.name,
            "url": self.url,
            "schedule": self.schedule,
            "filters": self.filters.to_dict(),
            "project_id": self.project_id,
            "owner_id": self.owner_id,
            "created_at": self.created_at,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MonitorConfig:
        return cls(
            monitor_id=d.get("monitor_id", ""),
            name=d.get("name", ""),
            url=d.get("url", ""),
            schedule=d.get("schedule", "daily"),
            filters=FilterSpec.from_dict(d.get("filters", {})),
            project_id=d.get("project_id", ""),
            owner_id=d.get("owner_id", ""),
            created_at=d.get("created_at", ""),
            active=d.get("active", True),
        )
