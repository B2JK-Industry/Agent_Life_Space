"""
Agent Life Space — Web Monitor Service

Orchestrates: fetch → extract → filter → snapshot → diff → report.
Manages monitor configs and snapshot persistence.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from agent.web_monitor.extraction import extract_items
from agent.web_monitor.models import (
    DiffResult,
    FilterSpec,
    MonitorConfig,
    MonitorItem,
    Snapshot,
)

logger = structlog.get_logger(__name__)


def diff_snapshots(current: Snapshot, previous: Snapshot | None) -> DiffResult:
    """Compare current vs previous snapshot, return new/removed items."""
    if previous is None:
        return DiffResult(
            new_items=list(current.items),
            total_current=len(current.items),
            total_previous=0,
        )

    prev_fps = {i.fingerprint for i in previous.items}
    curr_fps = {i.fingerprint for i in current.items}

    new_items = [i for i in current.items if i.fingerprint not in prev_fps]
    removed_items = [i for i in previous.items if i.fingerprint not in curr_fps]

    return DiffResult(
        new_items=new_items,
        removed_items=removed_items,
        total_current=len(current.items),
        total_previous=len(previous.items),
    )


def apply_filters(items: list[MonitorItem], filters: FilterSpec) -> list[MonitorItem]:
    """Apply filter spec to item list."""
    return [item for item in items if filters.matches(item)]


def render_report(diff: DiffResult, config: MonitorConfig) -> str:
    """Render a human-readable monitoring report from a diff result."""
    parts: list[str] = []
    parts.append(f"*Monitoring report: {config.name or config.url}*\n")
    parts.append(f"URL: {config.url}")
    parts.append(f"Time: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    parts.append(f"Items: {diff.total_current} current, {diff.total_previous} previous\n")

    if not diff.has_changes:
        parts.append("No new items since last check.")
        return "\n".join(parts)

    if diff.new_items:
        parts.append(f"*{len(diff.new_items)} new item(s):*")
        for item in diff.new_items[:10]:  # cap display at 10
            line = f"  • {item.title}"
            if item.price is not None:
                line += f" — {item.price:,.0f} {item.price_currency}".rstrip()
            if item.location:
                line += f" ({item.location})"
            if item.url:
                line += f"\n    {item.url}"
            parts.append(line)
        if len(diff.new_items) > 10:
            parts.append(f"  ... and {len(diff.new_items) - 10} more")

    if diff.removed_items:
        parts.append(f"\n*{len(diff.removed_items)} removed item(s)*")

    return "\n".join(parts)


class WebMonitorService:
    """Manages monitor configs, snapshots, and execution."""

    def __init__(self, data_dir: str | Path = "") -> None:
        self._data_dir = Path(data_dir) if data_dir else Path("agent/web_monitor/data")
        self._configs_dir = self._data_dir / "configs"
        self._snapshots_dir = self._data_dir / "snapshots"
        self._configs: dict[str, MonitorConfig] = {}

    def initialize(self) -> None:
        self._configs_dir.mkdir(parents=True, exist_ok=True)
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._load_configs()
        logger.info("web_monitor_initialized", monitors=len(self._configs))

    def _load_configs(self) -> None:
        for f in self._configs_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                config = MonitorConfig.from_dict(data)
                self._configs[config.monitor_id] = config
            except Exception:
                logger.warning("web_monitor_config_load_error", file=str(f))

    def create_monitor(
        self,
        name: str,
        url: str,
        schedule: str = "daily",
        filters: FilterSpec | None = None,
        project_id: str = "",
        owner_id: str = "",
    ) -> MonitorConfig:
        """Create and persist a new monitoring config."""
        config = MonitorConfig(
            monitor_id=uuid.uuid4().hex[:12],
            name=name,
            url=url,
            schedule=schedule,
            filters=filters or FilterSpec(),
            project_id=project_id,
            owner_id=owner_id,
        )
        self._configs[config.monitor_id] = config
        self._save_config(config)
        logger.info("web_monitor_created", monitor_id=config.monitor_id, url=url)
        return config

    def get_monitor(self, monitor_id: str) -> MonitorConfig | None:
        return self._configs.get(monitor_id)

    def list_monitors(self, active_only: bool = False) -> list[MonitorConfig]:
        configs = list(self._configs.values())
        if active_only:
            configs = [c for c in configs if c.active]
        return sorted(configs, key=lambda c: c.created_at, reverse=True)

    def _save_config(self, config: MonitorConfig) -> None:
        path = self._configs_dir / f"{config.monitor_id}.json"
        path.write_text(
            json.dumps(config.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def save_snapshot(self, monitor_id: str, snapshot: Snapshot) -> None:
        """Persist a snapshot for a monitor."""
        snap_dir = self._snapshots_dir / monitor_id
        snap_dir.mkdir(parents=True, exist_ok=True)
        # Keep only latest + previous
        latest_path = snap_dir / "latest.json"
        previous_path = snap_dir / "previous.json"
        if latest_path.exists():
            # Rotate latest → previous
            if previous_path.exists():
                previous_path.unlink()
            latest_path.rename(previous_path)
        latest_path.write_text(
            json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_previous_snapshot(self, monitor_id: str) -> Snapshot | None:
        """Load the previous snapshot for diffing."""
        path = self._snapshots_dir / monitor_id / "previous.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Snapshot.from_dict(data)
        except Exception:
            return None

    def load_latest_snapshot(self, monitor_id: str) -> Snapshot | None:
        """Load the latest snapshot."""
        path = self._snapshots_dir / monitor_id / "latest.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Snapshot.from_dict(data)
        except Exception:
            return None

    async def run_monitor(
        self,
        monitor_id: str,
        fetch_fn: Any = None,
    ) -> dict[str, Any]:
        """Execute one monitoring run: fetch → extract → filter → diff → report.

        Args:
            monitor_id: Which monitor config to use.
            fetch_fn: Async callable(url) → dict with 'text', 'content_type', 'status'.
                      If None, uses agent.core.web.WebAccess.

        Returns dict with: config, snapshot, diff, report, success.
        """
        config = self.get_monitor(monitor_id)
        if config is None:
            return {"success": False, "error": f"Monitor {monitor_id} not found"}

        # Fetch
        if fetch_fn is None:
            from agent.core.web import WebAccess
            web = WebAccess()
            try:
                result = await web.fetch_url(config.url)
            finally:
                await web.close()
        else:
            result = await fetch_fn(config.url)

        if not result.get("text"):
            return {
                "success": False,
                "error": f"Fetch failed: {result.get('error', 'empty response')}",
                "config": config.to_dict(),
            }

        # Extract
        content_type = result.get("content_type", "")
        items = extract_items(result["text"], content_type, config.url)

        # Filter
        if config.filters:
            items = apply_filters(items, config.filters)

        # Snapshot
        current = Snapshot(url=config.url, items=items, item_count=len(items))
        previous = self.load_latest_snapshot(monitor_id)
        self.save_snapshot(monitor_id, current)

        # Diff
        diff = diff_snapshots(current, previous)

        # Report
        report = render_report(diff, config)

        logger.info(
            "web_monitor_run_complete",
            monitor_id=monitor_id,
            items=len(items),
            new=len(diff.new_items),
            removed=len(diff.removed_items),
        )

        return {
            "success": True,
            "config": config.to_dict(),
            "snapshot": current.to_dict(),
            "diff": diff.to_dict(),
            "report": report,
        }

    def get_stats(self) -> dict[str, Any]:
        active = sum(1 for c in self._configs.values() if c.active)
        return {
            "total_monitors": len(self._configs),
            "active_monitors": active,
        }
