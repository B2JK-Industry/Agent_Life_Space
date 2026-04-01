"""Tests for build code generation module."""

from __future__ import annotations

import json

import pytest

from agent.build.codegen import _parse_operations
from agent.build.models import BuildOperationType


class TestParseOperations:
    """Test LLM output parsing into BuildOperations."""

    def test_valid_json_array(self):
        raw = json.dumps([
            {"operation_type": "write_file", "path": "app/main.py",
             "description": "Main", "content": "print('hello')"},
            {"operation_type": "write_file", "path": "tests/test.py",
             "description": "Test", "content": "import pytest"},
        ])
        ops = _parse_operations(raw, 20)
        assert len(ops) == 2
        assert ops[0].path == "app/main.py"
        assert ops[0].operation_type == BuildOperationType.WRITE_FILE
        assert ops[1].path == "tests/test.py"

    def test_markdown_fenced_json(self):
        inner = json.dumps([
            {"path": "app.py", "content": "x = 1", "description": "app"},
        ])
        raw = f"```json\n{inner}\n```"
        ops = _parse_operations(raw, 20)
        assert len(ops) == 1
        assert ops[0].path == "app.py"

    def test_markdown_fenced_no_language(self):
        inner = json.dumps([
            {"path": "app.py", "content": "x = 1", "description": "app"},
        ])
        raw = f"```\n{inner}\n```"
        ops = _parse_operations(raw, 20)
        assert len(ops) == 1

    def test_max_operations_enforced(self):
        items = [
            {"path": f"file{i}.py", "content": f"# file {i}", "description": f"f{i}"}
            for i in range(30)
        ]
        raw = json.dumps(items)
        ops = _parse_operations(raw, 5)
        assert len(ops) == 5

    def test_skips_empty_content(self):
        raw = json.dumps([
            {"path": "empty.py", "content": "", "description": "empty"},
            {"path": "real.py", "content": "x = 1", "description": "real"},
        ])
        ops = _parse_operations(raw, 20)
        assert len(ops) == 1
        assert ops[0].path == "real.py"

    def test_skips_invalid_paths(self):
        raw = json.dumps([
            {"path": "../escape.py", "content": "bad", "description": "escape"},
            {"path": "/absolute.py", "content": "bad", "description": "abs"},
            {"path": "good.py", "content": "ok", "description": "good"},
        ])
        ops = _parse_operations(raw, 20)
        assert len(ops) == 1
        assert ops[0].path == "good.py"

    def test_skips_non_dict_items(self):
        raw = json.dumps([
            "not a dict",
            {"path": "ok.py", "content": "x = 1", "description": "ok"},
            42,
        ])
        ops = _parse_operations(raw, 20)
        assert len(ops) == 1
        assert ops[0].path == "ok.py"

    def test_raises_on_non_array(self):
        raw = json.dumps({"not": "an array"})
        with pytest.raises(RuntimeError, match="not a JSON array"):
            _parse_operations(raw, 20)

    def test_raises_on_empty_result(self):
        raw = json.dumps([
            {"path": "../bad.py", "content": "bad", "description": "bad"},
        ])
        with pytest.raises(RuntimeError, match="no valid operations"):
            _parse_operations(raw, 20)

    def test_raises_on_invalid_json(self):
        with pytest.raises(RuntimeError, match="not valid JSON"):
            _parse_operations("this is not json at all", 20)

    def test_newlines_in_content(self):
        """LLM often returns content with real newlines inside JSON strings."""
        raw = '[{"path": "app.py", "content": "line1\nline2\nline3", "description": "app"}]'
        ops = _parse_operations(raw, 20)
        assert len(ops) == 1
        assert "\n" in ops[0].content

    def test_all_operations_are_write_file(self):
        """Codegen only generates WRITE_FILE operations regardless of input."""
        raw = json.dumps([
            {"operation_type": "delete_file", "path": "x.py",
             "content": "y", "description": "d"},
        ])
        ops = _parse_operations(raw, 20)
        assert len(ops) == 1
        # Forced to WRITE_FILE by codegen
        assert ops[0].operation_type == BuildOperationType.WRITE_FILE
