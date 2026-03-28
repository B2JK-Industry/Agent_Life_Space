"""
Agent Life Space — Golden Review Cases

Shared deterministic review cases reused by runtime quality evaluation and CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoldenReviewCase:
    """Single deterministic review case with expected outcome."""

    case_id: str
    expected_verdict: str
    expected_counts: dict[str, int]
    expected_title_contains: str = ""


def list_golden_review_cases() -> list[GoldenReviewCase]:
    """Return the canonical deterministic review cases."""
    return [
        GoldenReviewCase(
            case_id="clean",
            expected_verdict="pass",
            expected_counts={"critical": 0, "high": 0, "medium": 0, "low": 0},
        ),
        GoldenReviewCase(
            case_id="secret",
            expected_verdict="fail",
            expected_counts={"critical": 1, "high": 0, "medium": 0, "low": 0},
            expected_title_contains="Potential secret:",
        ),
        GoldenReviewCase(
            case_id="eval",
            expected_verdict="pass_with_findings",
            expected_counts={"critical": 0, "high": 1, "medium": 0, "low": 0},
            expected_title_contains="eval() usage",
        ),
    ]


def seed_golden_review_repo(root: Path, case_id: str) -> str:
    """Materialize one deterministic review-case repository."""
    (root / "README.md").write_text("# Golden Repo\n", encoding="utf-8")
    (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\njobs:\n  test:\n    steps:\n      - run: pytest -q\n",
        encoding="utf-8",
    )

    if case_id == "secret":
        (root / "config.py").write_text(
            'API_KEY = "sk-abc123456789abcdef"\n',
            encoding="utf-8",
        )
    elif case_id == "eval":
        (root / "danger.py").write_text(
            "result = eval(user_input)\n",
            encoding="utf-8",
        )

    return str(root)
