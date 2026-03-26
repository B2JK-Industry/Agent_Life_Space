"""
Test scenarios for Programmer brain module.

1. Task analysis identifies relevant files and complexity
2. Code review catches common issues
3. Error analysis identifies error types and suggests fixes
4. Programming workflow generates correct step sequence
5. Lint check validates Python syntax
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent.brain.programmer import Programmer


@pytest.fixture
def project_dir():
    """Create temporary project with Python files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create agent/ structure
        agent_dir = os.path.join(tmpdir, "agent", "core")
        os.makedirs(agent_dir)

        # Good file
        with open(os.path.join(agent_dir, "router.py"), "w") as f:
            f.write('"""\nMessage router module.\n"""\n\ndef route(msg):\n    return msg\n')

        # File with issues
        with open(os.path.join(agent_dir, "messy.py"), "w") as f:
            lines = [
                "import os\n",
                "import json\n",  # unused
                "# TODO: fix this later\n",
                "def process(data):\n",
            ]
            # Make it 60+ lines to trigger "long function" warning
            for i in range(55):
                lines.append(f"    x_{i} = {i}\n")
            lines.append("    return data\n")
            # Second function triggers detection of first being too long
            lines.append("\ndef short_func():\n    pass\n")
            f.writelines(lines)

        # Tests dir
        tests_dir = os.path.join(tmpdir, "tests")
        os.makedirs(tests_dir)
        with open(os.path.join(tests_dir, "test_router.py"), "w") as f:
            f.write("def test_route():\n    assert True\n")

        yield tmpdir


@pytest.fixture
def programmer(project_dir):
    return Programmer(project_root=project_dir)


class TestTaskAnalysis:
    """Analyze tasks before coding."""

    def test_find_relevant_files(self, programmer):
        """Analysis finds files matching task keywords."""
        analysis = programmer.analyze_task("fix the router message delivery")
        assert len(analysis.files_involved) >= 1
        assert any("router" in f for f in analysis.files_involved)

    def test_complexity_high(self, programmer):
        """Refactoring tasks are high complexity."""
        analysis = programmer.analyze_task("refactor the entire message system")
        assert analysis.complexity == "high"

    def test_complexity_medium(self, programmer):
        """Adding features is medium complexity."""
        analysis = programmer.analyze_task("add retry logic to router")
        assert analysis.complexity == "medium"

    def test_complexity_low(self, programmer):
        """Simple tasks are low complexity."""
        analysis = programmer.analyze_task("check the status")
        assert analysis.complexity == "low"

    def test_database_risk_detected(self, programmer):
        """Database changes flagged as risky."""
        analysis = programmer.analyze_task("update the SQLite database schema")
        assert any("database" in r.lower() or "schema" in r.lower() for r in analysis.risks)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestCodeReview:
    """Review code for common issues. Uses deprecated Programmer.review_file()."""

    def test_review_clean_file(self, programmer):
        """Clean file passes review."""
        review = programmer.review_file("agent/core/router.py")
        assert review.passed is True

    def test_review_finds_todo(self, programmer):
        """Review catches TODO comments."""
        review = programmer.review_file("agent/core/messy.py")
        todo_issues = [i for i in review.issues if "TODO" in i["message"]]
        assert len(todo_issues) >= 1

    def test_review_finds_long_function(self, programmer):
        """Review catches functions > 50 lines."""
        review = programmer.review_file("agent/core/messy.py")
        long_func = [i for i in review.issues if "lines" in i["message"]]
        assert len(long_func) >= 1

    def test_review_nonexistent_file(self, programmer):
        """Review of nonexistent file fails."""
        review = programmer.review_file("agent/core/nonexistent.py")
        assert review.passed is False

    def test_review_suggests_unused_imports(self, programmer):
        """Review suggests possibly unused imports."""
        review = programmer.review_file("agent/core/messy.py")
        unused = [s for s in review.suggestions if "unused import" in s.lower()]
        assert len(unused) >= 1


class TestErrorAnalysis:
    """Analyze errors to understand root cause."""

    def test_import_error(self, programmer):
        """ImportError identified correctly."""
        result = programmer.analyze_error("ModuleNotFoundError: No module named 'flask'")
        assert result["error_type"] == "import_error"
        assert result["severity"] == "high"

    def test_name_error(self, programmer):
        """NameError identified correctly."""
        result = programmer.analyze_error("NameError: name 'Path' is not defined")
        assert result["error_type"] == "name_error"
        assert "import" in result["suggested_fix"].lower()

    def test_timeout_error(self, programmer):
        """Timeout identified correctly."""
        result = programmer.analyze_error("Connection timed out after 30s")
        assert result["error_type"] == "timeout"

    def test_permission_error(self, programmer):
        """Permission denied identified correctly."""
        result = programmer.analyze_error("PermissionError: Permission denied: /var/run/docker.sock")
        assert result["error_type"] == "permission_error"

    def test_syntax_error(self, programmer):
        """SyntaxError identified correctly."""
        result = programmer.analyze_error("SyntaxError: unexpected EOF while parsing")
        assert result["error_type"] == "syntax_error"
        assert result["severity"] == "high"

    def test_key_error(self, programmer):
        """KeyError identified with .get() suggestion."""
        result = programmer.analyze_error("KeyError: 'missing_key'")
        assert result["error_type"] == "key_error"
        assert ".get(" in result["suggested_fix"]

    def test_unknown_error(self, programmer):
        """Unknown error type handled gracefully."""
        result = programmer.analyze_error("Something weird happened")
        assert result["error_type"] == "unknown"


class TestProgrammingWorkflow:
    """Structured workflow for programming tasks."""

    def test_workflow_has_steps(self, programmer):
        """Workflow generates ordered steps."""
        wf = programmer.programming_workflow("add a new endpoint to the API")
        assert len(wf["steps"]) >= 5
        assert wf["steps"][0]["action"] == "analyze"

    def test_workflow_includes_test_step(self, programmer):
        """Every workflow includes a test step."""
        wf = programmer.programming_workflow("fix the router bug")
        actions = [s["action"] for s in wf["steps"]]
        assert "write_test" in actions
        assert "test" in actions

    def test_workflow_includes_review(self, programmer):
        """Every workflow includes code review."""
        wf = programmer.programming_workflow("add logging")
        actions = [s["action"] for s in wf["steps"]]
        assert "review" in actions

    def test_high_complexity_includes_plan(self, programmer):
        """High complexity tasks include planning step."""
        wf = programmer.programming_workflow("refactor the entire memory module")
        actions = [s["action"] for s in wf["steps"]]
        assert "plan" in actions

    def test_low_complexity_skips_plan(self, programmer):
        """Low complexity tasks skip planning."""
        wf = programmer.programming_workflow("check status")
        actions = [s["action"] for s in wf["steps"]]
        assert "plan" not in actions

    def test_workflow_identifies_risks(self, programmer):
        """Workflow identifies risks for database tasks."""
        wf = programmer.programming_workflow("update the SQLite database schema")
        assert len(wf["risks"]) >= 1
