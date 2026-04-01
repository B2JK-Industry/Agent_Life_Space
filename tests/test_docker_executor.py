"""Tests for Docker project executor."""

from __future__ import annotations

import tempfile
from pathlib import Path

from agent.build.docker_executor import (
    ProjectBuildResult,
    _write_project_files,
)
from agent.build.models import BuildOperation, BuildOperationType


class TestWriteProjectFiles:
    """Test file writing to temp directory."""

    def test_writes_files(self):
        ops = [
            BuildOperation(
                operation_type=BuildOperationType.WRITE_FILE,
                path="app/main.py",
                content="print('hello')",
            ),
            BuildOperation(
                operation_type=BuildOperationType.WRITE_FILE,
                path="tests/test_app.py",
                content="import pytest",
            ),
        ]
        with tempfile.TemporaryDirectory() as d:
            count = _write_project_files(d, ops)
            assert count == 2
            assert (Path(d) / "app" / "main.py").read_text() == "print('hello')"
            assert (Path(d) / "tests" / "test_app.py").read_text() == "import pytest"

    def test_creates_nested_dirs(self):
        ops = [
            BuildOperation(
                operation_type=BuildOperationType.WRITE_FILE,
                path="a/b/c/deep.py",
                content="x = 1",
            ),
        ]
        with tempfile.TemporaryDirectory() as d:
            count = _write_project_files(d, ops)
            assert count == 1
            assert (Path(d) / "a" / "b" / "c" / "deep.py").exists()

    def test_skips_empty_content(self):
        ops = [
            BuildOperation(path="empty.py", content=""),
            BuildOperation(path="real.py", content="x = 1"),
        ]
        with tempfile.TemporaryDirectory() as d:
            count = _write_project_files(d, ops)
            assert count == 1
            assert not (Path(d) / "empty.py").exists()

    def test_skips_empty_path(self):
        ops = [BuildOperation(path="", content="x = 1")]
        with tempfile.TemporaryDirectory() as d:
            count = _write_project_files(d, ops)
            assert count == 0


class TestProjectBuildResult:
    """Test result dataclass."""

    def test_default_failure(self):
        r = ProjectBuildResult()
        assert not r.success
        assert r.files_written == 0

    def test_to_dict(self):
        r = ProjectBuildResult(
            success=True,
            files_written=5,
            test_passed=True,
            lint_passed=True,
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["files_written"] == 5

    def test_summary(self):
        r = ProjectBuildResult(
            files_written=3,
            deps_installed=True,
            test_passed=True,
            lint_passed=False,
            retries=1,
        )
        s = r.summary
        assert "files=3" in s
        assert "deps=OK" in s
        assert "tests=PASS" in s
        assert "lint=FAIL" in s
        assert "retries=1" in s

    def test_truncates_long_output(self):
        r = ProjectBuildResult(test_output="x" * 10000)
        d = r.to_dict()
        assert len(d["test_output"]) <= 3000
