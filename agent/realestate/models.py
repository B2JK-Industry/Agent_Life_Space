"""
Agent Life Space — Real Estate Watcher Models

Dataclasses for estate listings, search configs, price history,
notification log, and score breakdown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ── URL construction helpers ──────────────────────────────────────────────────

_CATEGORY_TYPE: dict[int, str] = {1: "prodej"}
_CATEGORY_MAIN: dict[int, str] = {1: "byt", 2: "dum"}
_CATEGORY_SUB: dict[int, str] = {4: "2+kk", 5: "2+1", 6: "3+kk"}

_SREALITY_BASE = "https://www.sreality.cz/detail"


def build_url(estate: Estate) -> str:
    """Build canonical sreality.cz detail URL from estate category codes."""
    cat_type = _CATEGORY_TYPE.get(estate.category_type, str(estate.category_type))
    cat_main = _CATEGORY_MAIN.get(estate.category_main, str(estate.category_main))
    cat_sub = _CATEGORY_SUB.get(estate.category_sub, str(estate.category_sub))
    return f"{_SREALITY_BASE}/{cat_type}/{cat_main}/{cat_sub}/p/{estate.hash_id}"


# ── Core estate listing ───────────────────────────────────────────────────────


@dataclass
class Estate:
    """A single real estate listing scraped from sreality.cz."""

    hash_id: int
    title: str
    price: int  # CZK
    area_m2: float
    price_per_m2: float
    url: str
    category_type: int  # 1=prodej
    category_main: int  # 1=byt, 2=dum
    category_sub: int   # 4=2+kk, 5=2+1, 6=3+kk
    has_floor_plan: bool = False
    labels: list[str] = field(default_factory=list)
    locality: str = ""
    image_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hash_id": self.hash_id,
            "title": self.title,
            "price": self.price,
            "area_m2": self.area_m2,
            "price_per_m2": self.price_per_m2,
            "url": self.url,
            "category_type": self.category_type,
            "category_main": self.category_main,
            "category_sub": self.category_sub,
            "has_floor_plan": self.has_floor_plan,
            "labels": self.labels,
            "locality": self.locality,
            "image_url": self.image_url,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Estate:
        return cls(
            hash_id=d["hash_id"],
            title=d.get("title", ""),
            price=d.get("price", 0),
            area_m2=d.get("area_m2", 0.0),
            price_per_m2=d.get("price_per_m2", 0.0),
            url=d.get("url", ""),
            category_type=d.get("category_type", 1),
            category_main=d.get("category_main", 1),
            category_sub=d.get("category_sub", 4),
            has_floor_plan=d.get("has_floor_plan", False),
            labels=d.get("labels", []),
            locality=d.get("locality", ""),
            image_url=d.get("image_url", ""),
        )


# ── Search configuration ──────────────────────────────────────────────────────


@dataclass
class SearchConfig:
    """Persisted search definition stored in SQLite (name is PK)."""

    name: str
    params_json: dict[str, Any]
    active: bool = True
    min_score: int = 60
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # price_max is filtered client-side (sreality ignores it in API)
    @property
    def price_max(self) -> int | None:
        return self.params_json.get("price_max")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params_json": self.params_json,
            "active": self.active,
            "min_score": self.min_score,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> SearchConfig:
        """Construct from SQLite row: (name, params_json, active, min_score, created_at)."""
        name, params_raw, active, min_score, created_at_str = row
        return cls(
            name=name,
            params_json=json.loads(params_raw),
            active=bool(active),
            min_score=int(min_score),
            created_at=datetime.fromisoformat(created_at_str),
        )


# ── Price history ─────────────────────────────────────────────────────────────


@dataclass
class PriceRecord:
    """Single price snapshot for an estate within a search."""

    hash_id: int
    search_name: str
    snapshot_at: datetime
    price: int  # CZK

    def to_dict(self) -> dict[str, Any]:
        return {
            "hash_id": self.hash_id,
            "search_name": self.search_name,
            "snapshot_at": self.snapshot_at.isoformat(),
            "price": self.price,
        }

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> PriceRecord:
        """Construct from SQLite row: (hash_id, search_name, snapshot_at, price)."""
        hash_id, search_name, snapshot_at_str, price = row
        return cls(
            hash_id=int(hash_id),
            search_name=search_name,
            snapshot_at=datetime.fromisoformat(snapshot_at_str),
            price=int(price),
        )


# ── Notification log ──────────────────────────────────────────────────────────


@dataclass
class NotifLogEntry:
    """Record of a sent notification (used for deduplication)."""

    hash_id: int
    event_type: str  # e.g. "new_listing", "price_drop"
    sent_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "hash_id": self.hash_id,
            "event_type": self.event_type,
            "sent_at": self.sent_at.isoformat(),
        }

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> NotifLogEntry:
        """Construct from SQLite row: (hash_id, event_type, sent_at)."""
        hash_id, event_type, sent_at_str = row
        return cls(
            hash_id=int(hash_id),
            event_type=event_type,
            sent_at=datetime.fromisoformat(sent_at_str),
        )


# ── Scoring ───────────────────────────────────────────────────────────────────


@dataclass
class ScoreBreakdown:
    """Deterministic 0-100 score with per-component breakdown."""

    price_score: int       # 0-50: price/m2 vs median (lower = better)
    area_bonus: int        # +10 if area >= 50 m2
    price_drop_bonus: int  # +30 if price dropped >3%
    floor_plan_bonus: int  # +5 if has floor plan
    label_bonus: int       # +5 if "nova" or "exkluzivne" label present
    scam_penalty: int      # -total (sets to 0) if price < 500_000 CZK
    total: int             # final clamped 0-100
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "price_score": self.price_score,
            "area_bonus": self.area_bonus,
            "price_drop_bonus": self.price_drop_bonus,
            "floor_plan_bonus": self.floor_plan_bonus,
            "label_bonus": self.label_bonus,
            "scam_penalty": self.scam_penalty,
            "total": self.total,
            "reasons": self.reasons,
        }
