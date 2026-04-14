"""Tests for the web monitoring capability MVP."""

from __future__ import annotations

import json
import tempfile

import pytest

from agent.web_monitor.extraction import (
    extract_items,
    extract_items_from_html,
    extract_items_from_json,
)
from agent.web_monitor.models import (
    DiffResult,
    FilterSpec,
    MonitorConfig,
    MonitorItem,
    Snapshot,
)
from agent.web_monitor.service import (
    WebMonitorService,
    apply_filters,
    diff_snapshots,
    render_report,
)

# ─── Models ───


class TestMonitorItem:
    def test_fingerprint_stable(self) -> None:
        item = MonitorItem(title="Flat 3+1", url="https://example.com/1", price=200000)
        assert len(item.fingerprint) == 16
        assert item.fingerprint == item.fingerprint  # deterministic

    def test_fingerprint_differs_on_content(self) -> None:
        a = MonitorItem(title="Flat A", url="https://a.com")
        b = MonitorItem(title="Flat B", url="https://b.com")
        assert a.fingerprint != b.fingerprint

    def test_roundtrip(self) -> None:
        item = MonitorItem(title="Test", url="https://x.com", price=100, location="Prague")
        restored = MonitorItem.from_dict(item.to_dict())
        assert restored.title == "Test"
        assert restored.price == 100
        assert restored.fingerprint == item.fingerprint


class TestFilterSpec:
    def test_price_max(self) -> None:
        f = FilterSpec(price_max=200000)
        assert f.matches(MonitorItem(title="A", price=150000))
        assert not f.matches(MonitorItem(title="B", price=250000))

    def test_price_min(self) -> None:
        f = FilterSpec(price_min=100000)
        assert f.matches(MonitorItem(title="A", price=150000))
        assert not f.matches(MonitorItem(title="B", price=50000))

    def test_title_contains(self) -> None:
        f = FilterSpec(title_contains="3+1")
        assert f.matches(MonitorItem(title="Byt 3+1 Praha"))
        assert not f.matches(MonitorItem(title="Byt 2+kk"))

    def test_location_contains(self) -> None:
        f = FilterSpec(location_contains="bratislava")
        assert f.matches(MonitorItem(title="A", location="Bratislava - Ružinov"))
        assert not f.matches(MonitorItem(title="B", location="Praha"))


# ─── Extraction ───


_SAMPLE_HTML = """
<html><body>
<div class="listing">
  <a href="/property/123">Nice flat 3+1 in Prague center</a>
  <a href="/property/456">Studio apartment in Brno</a>
  <a href="/property/789">Family house with garden near Bratislava</a>
</div>
<a href="/login">Login</a>
<a href="#">Top</a>
</body></html>
"""

_SAMPLE_JSON = json.dumps({
    "_embedded": {
        "estates": [
            {
                "name": "Byt 3+1 Praha",
                "price": 5500000,
                "locality": "Praha 5",
                "_links": {"self": {"href": "/cs/v2/estates/111"}},
            },
            {
                "name": "Byt 2+kk Brno",
                "price": 3200000,
                "locality": "Brno-město",
                "_links": {"self": {"href": "/cs/v2/estates/222"}},
            },
        ]
    }
})


class TestHtmlExtraction:
    def test_extracts_listing_links(self) -> None:
        items = extract_items_from_html(_SAMPLE_HTML, "https://example.com")
        assert len(items) >= 3
        titles = [i.title for i in items]
        assert any("Nice flat" in t for t in titles)
        assert any("Studio" in t for t in titles)

    def test_skips_navigation_links(self) -> None:
        items = extract_items_from_html(_SAMPLE_HTML, "https://example.com")
        titles = [i.title.lower() for i in items]
        assert "login" not in titles
        assert "top" not in titles

    def test_resolves_relative_urls(self) -> None:
        items = extract_items_from_html(_SAMPLE_HTML, "https://example.com")
        urls = [i.url for i in items]
        assert any("https://example.com/property/123" in u for u in urls)


class TestJsonExtraction:
    def test_extracts_nested_estates(self) -> None:
        data = json.loads(_SAMPLE_JSON)
        items = extract_items_from_json(data, "https://sreality.cz")
        assert len(items) == 2
        assert items[0].title == "Byt 3+1 Praha"
        assert items[0].price == 5500000
        assert items[0].location == "Praha 5"

    def test_resolves_self_link(self) -> None:
        data = json.loads(_SAMPLE_JSON)
        items = extract_items_from_json(data, "https://sreality.cz")
        assert "sreality.cz" in items[0].url


class TestAutoDetection:
    def test_json_detected(self) -> None:
        items = extract_items(_SAMPLE_JSON, "application/json", "https://api.example.com")
        assert len(items) == 2

    def test_html_fallback(self) -> None:
        items = extract_items(_SAMPLE_HTML, "text/html", "https://example.com")
        assert len(items) >= 3


# ─── Diff ───


class TestSnapshotDiff:
    def test_first_run_all_new(self) -> None:
        current = Snapshot(items=[MonitorItem(title="A", url="https://a.com")])
        diff = diff_snapshots(current, None)
        assert len(diff.new_items) == 1
        assert diff.total_previous == 0

    def test_no_changes(self) -> None:
        item = MonitorItem(title="A", url="https://a.com")
        current = Snapshot(items=[item])
        previous = Snapshot(items=[item])
        diff = diff_snapshots(current, previous)
        assert len(diff.new_items) == 0
        assert not diff.has_changes

    def test_new_items_detected(self) -> None:
        old_item = MonitorItem(title="Old", url="https://old.com")
        new_item = MonitorItem(title="New", url="https://new.com")
        previous = Snapshot(items=[old_item])
        current = Snapshot(items=[old_item, new_item])
        diff = diff_snapshots(current, previous)
        assert len(diff.new_items) == 1
        assert diff.new_items[0].title == "New"

    def test_removed_items_detected(self) -> None:
        item = MonitorItem(title="Gone", url="https://gone.com")
        previous = Snapshot(items=[item])
        current = Snapshot(items=[])
        diff = diff_snapshots(current, previous)
        assert len(diff.removed_items) == 1


# ─── Filtering ───


class TestApplyFilters:
    def test_price_filter(self) -> None:
        items = [
            MonitorItem(title="Cheap", price=100000),
            MonitorItem(title="Expensive", price=500000),
        ]
        result = apply_filters(items, FilterSpec(price_max=200000))
        assert len(result) == 1
        assert result[0].title == "Cheap"


# ─── Report ───


class TestReportRendering:
    def test_no_changes_report(self) -> None:
        diff = DiffResult(total_current=5, total_previous=5)
        config = MonitorConfig(name="Test", url="https://example.com")
        report = render_report(diff, config)
        assert "No new items" in report

    def test_new_items_report(self) -> None:
        diff = DiffResult(
            new_items=[MonitorItem(title="New flat", price=200000, price_currency="CZK")],
            total_current=6,
            total_previous=5,
        )
        config = MonitorConfig(name="Test", url="https://example.com")
        report = render_report(diff, config)
        assert "1 new" in report
        assert "New flat" in report
        assert "200,000" in report


# ─── Service ───


class TestWebMonitorService:
    @pytest.fixture()
    def service(self) -> WebMonitorService:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = WebMonitorService(data_dir=tmpdir)
            svc.initialize()
            yield svc

    def test_create_and_list(self, service: WebMonitorService) -> None:
        config = service.create_monitor(name="Test", url="https://example.com")
        assert config.monitor_id
        monitors = service.list_monitors()
        assert len(monitors) == 1

    def test_snapshot_persistence(self, service: WebMonitorService) -> None:
        config = service.create_monitor(name="Test", url="https://example.com")
        snap = Snapshot(url="https://example.com", items=[
            MonitorItem(title="Item 1", url="https://example.com/1"),
        ])
        service.save_snapshot(config.monitor_id, snap)
        loaded = service.load_latest_snapshot(config.monitor_id)
        assert loaded is not None
        assert len(loaded.items) == 1

    def test_snapshot_rotation(self, service: WebMonitorService) -> None:
        config = service.create_monitor(name="Test", url="https://example.com")
        snap1 = Snapshot(url="u", items=[MonitorItem(title="V1")])
        snap2 = Snapshot(url="u", items=[MonitorItem(title="V2")])
        service.save_snapshot(config.monitor_id, snap1)
        service.save_snapshot(config.monitor_id, snap2)
        latest = service.load_latest_snapshot(config.monitor_id)
        previous = service.load_previous_snapshot(config.monitor_id)
        assert latest is not None and latest.items[0].title == "V2"
        assert previous is not None and previous.items[0].title == "V1"

    @pytest.mark.asyncio
    async def test_run_monitor(self, service: WebMonitorService) -> None:
        config = service.create_monitor(name="Test", url="https://example.com")

        async def mock_fetch(url: str) -> dict:
            return {
                "text": _SAMPLE_HTML,
                "content_type": "text/html",
                "status": 200,
            }

        result = await service.run_monitor(config.monitor_id, fetch_fn=mock_fetch)
        assert result["success"]
        assert "report" in result
        assert result["diff"]["new_count"] >= 3  # first run, all new
