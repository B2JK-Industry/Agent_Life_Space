"""
Agent Life Space — Build Verification

Runs verification steps (test, lint, typecheck) against a workspace
and produces structured results.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import structlog

from agent.build.models import VerificationKind, VerificationResult

logger = structlog.get_logger(__name__)

# Default commands per verification kind.
# Can be overridden per-job or per-workspace.
DEFAULT_COMMANDS: dict[VerificationKind, list[str]] = {
    VerificationKind.TEST: ["python3", "-m", "pytest", "tests/", "-q", "--tb=short"],
    VerificationKind.LINT: ["python3", "-m", "ruff", "check", "."],
    VerificationKind.TYPECHECK: ["python3", "-m", "mypy", ".", "--ignore-missing-imports"],
}

_MAX_OUTPUT_BYTES = 50_000


def run_verification_step(
    kind: VerificationKind,
    workspace_path: str,
    command: list[str] | None = None,
    timeout_seconds: int = 120,
) -> VerificationResult:
    """Run a single verification step in the workspace directory.

    Returns a VerificationResult with pass/fail, exit code, and output.
    """
    cmd = command or DEFAULT_COMMANDS.get(kind, [])
    if not cmd:
        return VerificationResult(
            kind=kind,
            passed=False,
            command="<no command configured>",
            exit_code=-1,
            stderr=f"No command configured for {kind.value}",
        )

    cmd_str = " ".join(cmd)
    cwd = Path(workspace_path)
    if not cwd.is_dir():
        return VerificationResult(
            kind=kind,
            passed=False,
            command=cmd_str,
            exit_code=-1,
            stderr=f"Workspace path does not exist: {workspace_path}",
        )

    start = time.time()
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        duration_ms = (time.time() - start) * 1000

        stdout = result.stdout[:_MAX_OUTPUT_BYTES] if result.stdout else ""
        stderr = result.stderr[:_MAX_OUTPUT_BYTES] if result.stderr else ""

        return VerificationResult(
            kind=kind,
            passed=result.returncode == 0,
            command=cmd_str,
            exit_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=round(duration_ms, 1),
        )
    except subprocess.TimeoutExpired:
        duration_ms = (time.time() - start) * 1000
        return VerificationResult(
            kind=kind,
            passed=False,
            command=cmd_str,
            exit_code=-1,
            stderr=f"Timeout after {timeout_seconds}s",
            duration_ms=round(duration_ms, 1),
        )
    except Exception as e:
        duration_ms = (time.time() - start) * 1000
        return VerificationResult(
            kind=kind,
            passed=False,
            command=cmd_str,
            exit_code=-1,
            stderr=str(e),
            duration_ms=round(duration_ms, 1),
        )


def run_verification_suite(
    workspace_path: str,
    steps: list[VerificationKind] | None = None,
    custom_commands: dict[VerificationKind, list[str]] | None = None,
    timeout_seconds: int = 120,
) -> list[VerificationResult]:
    """Run a suite of verification steps. Returns all results.

    Default steps: [TEST, LINT]. Override via steps parameter.
    """
    if steps is None:
        steps = [VerificationKind.TEST, VerificationKind.LINT]

    results: list[VerificationResult] = []
    for kind in steps:
        cmd = (custom_commands or {}).get(kind)
        result = run_verification_step(
            kind=kind,
            workspace_path=workspace_path,
            command=cmd,
            timeout_seconds=timeout_seconds,
        )
        results.append(result)
        logger.info(
            "build_verification_step",
            kind=kind.value,
            passed=result.passed,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
        )
    return results
