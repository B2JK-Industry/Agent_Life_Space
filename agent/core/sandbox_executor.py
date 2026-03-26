"""
Agent Life Space — Sandbox Executor

High-level API pre bezpečné spúšťanie kódu v Docker sandbox.
Nad DockerSandbox pridáva:
    - Iteráciu (run → error → fix → re-run)
    - Multi-file projekty
    - Pytest integráciu
    - Štruktúrované výsledky

Použitie:
    executor = SandboxExecutor()
    result = await executor.execute_python("print('hello')")
    result = await executor.run_tests(source_code, test_code)
    result = await executor.iterate(code, fix_callback, max_rounds=5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from agent.core.sandbox import DockerSandbox, SandboxResult

logger = structlog.get_logger(__name__)


async def create_workspace_for_task(
    task_name: str = "",
    task_id: str = "",
    workspace_manager: Any = None,
) -> str:
    """Create an isolated workspace directory for a programming task.

    Returns the workspace path. Uses provided WorkspaceManager to avoid
    hidden coupling (creating own instance).

    Args:
        workspace_manager: Shared WorkspaceManager instance. If None, falls back
                          to temp directory (no persistence, no audit trail).
    """
    if workspace_manager is not None:
        try:
            ws = workspace_manager.create(name=task_name or "sandbox-task", task_id=task_id)
            workspace_manager.activate(ws.id)
            logger.info("workspace_created", id=ws.id, path=ws.path)
            return ws.path
        except Exception as e:
            logger.warning("workspace_create_failed", error=str(e))

    # Fallback: temp directory (no persistence)
    import tempfile
    path = tempfile.mkdtemp(prefix="agent-workspace-")
    logger.warning("workspace_fallback_temp", path=path)
    return path


@dataclass
class ExecutionResult:
    """Structured execution result."""

    success: bool = False
    output: str = ""
    error: str = ""
    exit_code: int = 0
    timed_out: bool = False
    iterations: int = 0
    language: str = ""
    test_passed: int = 0
    test_failed: int = 0
    files_produced: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output[:5000],
            "error": self.error[:2000],
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "iterations": self.iterations,
            "language": self.language,
            "test_passed": self.test_passed,
            "test_failed": self.test_failed,
        }


class SandboxExecutor:
    """
    High-level sandbox execution API.
    All code runs in Docker — never on host FS.
    """

    def __init__(
        self,
        memory_limit: str = "256m",
        cpu_limit: str = "1.0",
        network: bool = False,
        timeout: int = 120,
    ) -> None:
        self._sandbox = DockerSandbox(
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            network=network,
            timeout=timeout,
        )
        self._timeout = timeout

    async def check_available(self) -> bool:
        """Check if Docker sandbox is available."""
        status = await self._sandbox.check_docker()
        return status.get("available", False)

    async def execute_python(
        self,
        code: str,
        packages: list[str] | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """Run Python code in sandbox."""
        result = await self._sandbox.run_python(code, packages, timeout)
        return self._to_execution_result(result, "python")

    async def execute_code(
        self,
        language: str,
        code: str,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """Run code in any supported language."""
        result = await self._sandbox.run_code(language, code, timeout)
        return self._to_execution_result(result, language)

    async def run_tests(
        self,
        source_code: str,
        test_code: str,
        source_filename: str = "module.py",
        timeout: int | None = None,
    ) -> ExecutionResult:
        """
        Run pytest on source + test code in sandbox.
        Both files are created inside the container.
        """
        # Build a script that writes both files and runs pytest
        combined = f'''
import sys, os
os.makedirs("/tmp/project", exist_ok=True)

# Write source file
with open("/tmp/project/{source_filename}", "w") as f:
    f.write("""{_escape_triple_quotes(source_code)}""")

# Write test file
with open("/tmp/project/test_{source_filename}", "w") as f:
    f.write("""{_escape_triple_quotes(test_code)}""")

# Run pytest
sys.path.insert(0, "/tmp/project")
os.chdir("/tmp/project")

import subprocess
result = subprocess.run(
    [sys.executable, "-m", "pytest", f"test_{source_filename}", "-v", "--tb=short"],
    capture_output=True, text=True, cwd="/tmp/project",
)
print(result.stdout)
if result.stderr:
    print(result.stderr, file=sys.stderr)
sys.exit(result.returncode)
'''

        result = await self._sandbox.run_python(
            combined,
            packages=["pytest"],
            timeout=timeout or self._timeout,
        )

        exec_result = self._to_execution_result(result, "python")

        # Parse pytest output for pass/fail counts
        if "passed" in exec_result.output:
            import re
            match = re.search(r"(\d+) passed", exec_result.output)
            if match:
                exec_result.test_passed = int(match.group(1))
        if "failed" in exec_result.output:
            import re
            match = re.search(r"(\d+) failed", exec_result.output)
            if match:
                exec_result.test_failed = int(match.group(1))

        return exec_result

    async def iterate(
        self,
        code: str,
        fix_callback: Any = None,
        max_rounds: int = 5,
        language: str = "python",
    ) -> ExecutionResult:
        """
        Run code, if error, call fix_callback(code, error) -> fixed_code.
        Repeat until success or max_rounds.
        """
        current_code = code
        last_result = None

        for i in range(max_rounds):
            last_result = await self.execute_code(language, current_code)
            last_result.iterations = i + 1

            if last_result.success:
                return last_result

            if fix_callback is None:
                return last_result

            # Ask callback (usually LLM) to fix the error
            try:
                fixed = await fix_callback(current_code, last_result.error or last_result.output)
                if fixed and fixed != current_code:
                    current_code = fixed
                    logger.info("sandbox_iterate_fix", round=i + 1)
                else:
                    return last_result  # No fix available
            except Exception as e:
                logger.error("sandbox_iterate_callback_error", error=str(e))
                return last_result

        return last_result or ExecutionResult(error="Max iterations reached")

    @staticmethod
    def _to_execution_result(result: SandboxResult, language: str) -> ExecutionResult:
        """Convert SandboxResult to ExecutionResult."""
        return ExecutionResult(
            success=result.success,
            output=result.stdout,
            error=result.stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            language=language,
        )


def _escape_triple_quotes(code: str) -> str:
    """Escape triple quotes in code for embedding in Python string."""
    return code.replace('"""', r'\"\"\"').replace("\\", "\\\\")
