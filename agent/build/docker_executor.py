"""
Agent Life Space — Docker Project Executor

Runs generated projects inside Docker containers:
1. Write files to temp directory
2. Mount into container
3. pip install dependencies
4. Run pytest + lint
5. Return structured results
6. Optional: Opus auto-fix retry loop

Safety:
- Container: 512MB RAM, 1 CPU, 5min timeout, no network for tests
- Network enabled only during pip install phase
- Host temp dir mounted read-only (writable copy inside container)
- Container auto-removed after execution
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from agent.build.models import BuildOperation

logger = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 300  # 5 min for full build cycle
_DEFAULT_MEMORY = "512m"
_DEFAULT_CPUS = "1.0"
_DOCKER_IMAGE = "python:3.12-slim"
_MAX_RETRIES = 2


@dataclass
class ProjectBuildResult:
    """Result of building and testing a project in Docker."""

    success: bool = False
    files_written: int = 0
    deps_installed: bool = False
    deps_output: str = ""
    test_passed: bool = False
    test_output: str = ""
    lint_passed: bool = False
    lint_output: str = ""
    exit_code: int = 0
    error: str = ""
    retries: int = 0
    total_cost_usd: float = 0.0
    container_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "files_written": self.files_written,
            "deps_installed": self.deps_installed,
            "test_passed": self.test_passed,
            "test_output": self.test_output[:3000],
            "lint_passed": self.lint_passed,
            "lint_output": self.lint_output[:1000],
            "error": self.error[:500],
            "retries": self.retries,
            "total_cost_usd": round(self.total_cost_usd, 4),
        }

    @property
    def summary(self) -> str:
        parts = [f"files={self.files_written}"]
        if self.deps_installed:
            parts.append("deps=OK")
        parts.append(f"tests={'PASS' if self.test_passed else 'FAIL'}")
        parts.append(f"lint={'PASS' if self.lint_passed else 'FAIL'}")
        if self.retries:
            parts.append(f"retries={self.retries}")
        return " | ".join(parts)


async def run_project_in_docker(
    operations: list[BuildOperation],
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    memory: str = _DEFAULT_MEMORY,
    retry_on_failure: bool = True,
    max_retries: int = _MAX_RETRIES,
    description: str = "",
) -> ProjectBuildResult:
    """Build and test a generated project inside Docker.

    1. Write operation files to temp dir
    2. pip install in container (with network)
    3. pytest in container (no network)
    4. If tests fail and retry enabled, ask Opus to fix → retry

    Returns ProjectBuildResult with full details.
    """
    result = ProjectBuildResult()

    # Write files to temp dir
    project_dir = tempfile.mkdtemp(prefix="als-build-")
    try:
        files_written = _write_project_files(project_dir, operations)
        result.files_written = files_written
        logger.info("docker_executor_files_written",
                     count=files_written, dir=project_dir)

        if files_written == 0:
            result.error = "No files to build"
            return result

        # Phase 1: Install dependencies into project_dir/.deps so the next
        # phases (no network) see them. Each Docker phase uses --rm so
        # site-packages installed inside the container are lost; we install
        # to a target dir that lives in the writable bind-mounted project_dir.
        #
        # Always install pytest+ruff so test/lint phases have their tools,
        # regardless of whether codegen put them in requirements.txt
        # (Opus often splits runtime vs dev deps).  Then layer any project
        # requirements files on top.
        deps_script_parts = [
            "pip install -q --target=/work/.deps pytest ruff 2>&1",
        ]
        for req_name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
            if (Path(project_dir) / req_name).exists():
                deps_script_parts.append(
                    f"pip install -q --target=/work/.deps -r /work/{req_name} 2>&1 || true",
                )
        deps_result = await _docker_run_phase(
            project_dir=project_dir,
            script=" && ".join(deps_script_parts),
            network=True,
            timeout=180,
            memory=memory,
            phase="deps",
        )
        result.deps_installed = deps_result["success"]
        result.deps_output = deps_result["output"]
        if not deps_result["success"]:
            result.error = f"Dependency install failed: {deps_result['output'][:300]}"
            logger.warning("docker_executor_deps_failed",
                           output=deps_result["output"][:500])
            # Continue anyway — tests might reveal the issue

        # Phase 2: Run tests (no network)
        test_result = await _run_tests_in_docker(project_dir, memory, timeout)
        result.test_passed = test_result["success"]
        result.test_output = test_result["output"]
        result.exit_code = test_result.get("exit_code", 1)

        # Phase 3: Lint (no network — ruff installed in deps phase to /work/.deps)
        # Exclude .deps/ since it contains installed third-party packages
        # whose lint warnings would dominate output (1MB+ of noise).
        lint_result = await _docker_run_phase(
            project_dir=project_dir,
            script=(
                "cd /work && export PATH=/work/.deps/bin:$PATH && "
                "export PYTHONPATH=/work/.deps:/work && "
                "python -m ruff check --select E,F,W "
                "--exclude .deps --exclude __pycache__ . 2>&1 || true"
            ),
            network=False,
            timeout=60,
            memory=memory,
            phase="lint",
        )
        result.lint_passed = lint_result["success"] or "error" not in lint_result["output"].lower()
        result.lint_output = lint_result["output"]

        # Retry loop: if tests failed, ask Opus to fix
        if not result.test_passed and retry_on_failure and description:
            for attempt in range(max_retries):
                logger.info("docker_executor_retry",
                            attempt=attempt + 1, max=max_retries)
                result.retries = attempt + 1

                fixed_ops, cost = await _ask_opus_to_fix(
                    operations=operations,
                    test_output=result.test_output,
                    description=description,
                )
                result.total_cost_usd += cost

                if not fixed_ops:
                    logger.warning("docker_executor_fix_failed", attempt=attempt + 1)
                    break

                # Rewrite files and rerun
                operations = fixed_ops
                _write_project_files(project_dir, operations)

                # Reinstall deps in case the fix changed requirements files.
                # Same logic as initial deps phase: always pytest+ruff plus
                # whichever requirements files exist now.
                retry_parts = [
                    "pip install -q --target=/work/.deps pytest ruff 2>&1",
                ]
                for req_name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
                    if (Path(project_dir) / req_name).exists():
                        retry_parts.append(
                            f"pip install -q --target=/work/.deps -r /work/{req_name} 2>&1 || true",
                        )
                await _docker_run_phase(
                    project_dir=project_dir,
                    script=" && ".join(retry_parts),
                    network=True, timeout=180, memory=memory, phase="deps_retry",
                )

                test_result = await _run_tests_in_docker(project_dir, memory, timeout)
                result.test_passed = test_result["success"]
                result.test_output = test_result["output"]
                result.exit_code = test_result.get("exit_code", 1)

                if result.test_passed:
                    logger.info("docker_executor_retry_success", attempt=attempt + 1)
                    break

        result.success = result.test_passed
        return result

    finally:
        # Cleanup temp dir
        try:
            shutil.rmtree(project_dir, ignore_errors=True)
        except Exception:
            pass


def _write_project_files(
    project_dir: str,
    operations: list[BuildOperation],
) -> int:
    """Write BuildOperation files to project directory. Returns count."""
    count = 0
    for op in operations:
        if not op.path or not op.content:
            continue
        file_path = Path(project_dir) / op.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(op.content, encoding="utf-8")
        count += 1
    return count


async def _run_tests_in_docker(
    project_dir: str,
    memory: str,
    timeout: int,
) -> dict[str, Any]:
    """Run pytest in Docker container. No network.

    Deps were installed into /work/.deps in the previous phase via --target,
    so PYTHONPATH must include it for `python -m pytest` to find pytest.
    """
    # Detect pytest requirement (heuristic: requirements.txt mentions pytest
    # OR a tests/ dir exists).  Either way the deps phase already installed it.
    req_path = Path(project_dir) / "requirements.txt"
    tests_dir = Path(project_dir) / "tests"
    has_pytest = (
        (req_path.exists() and "pytest" in req_path.read_text())
        or tests_dir.is_dir()
    )

    if has_pytest:
        script = (
            "cd /work && export PYTHONPATH=/work/.deps:/work && "
            "python -m pytest tests/ -v --tb=short --no-header 2>&1 || "
            "python -m pytest . -v --tb=short --no-header 2>&1"
        )
    else:
        script = (
            "cd /work && export PYTHONPATH=/work/.deps:/work && "
            "python -m pytest -v --tb=short --no-header 2>&1"
        )

    return await _docker_run_phase(
        project_dir=project_dir,
        script=script,
        network=False,
        timeout=timeout,
        memory=memory,
        phase="test",
    )


async def _docker_run_phase(
    *,
    project_dir: str,
    script: str,
    network: bool,
    timeout: int,
    memory: str,
    phase: str,
) -> dict[str, Any]:
    """Run a script phase in Docker with the project mounted."""
    import uuid
    container_name = f"als-{phase}-{uuid.uuid4().hex[:8]}"
    network_flag = "bridge" if network else "none"

    docker_args = [
        "docker", "run", "--rm",
        f"--name={container_name}",
        f"--memory={memory}",
        f"--cpus={_DEFAULT_CPUS}",
        f"--network={network_flag}",
        "--pids-limit=100",
        "--security-opt=no-new-privileges",
        # Mount project writable so /work/.deps (pip --target install)
        # persists across deps → test → lint phases (each phase is --rm).
        "-v", f"{project_dir}:/work",
        _DOCKER_IMAGE,
        "bash", "-c",
        f"cd /work && {script}",
    ]

    docker_cmd = f"sg docker -c {shlex.quote(' '.join(shlex.quote(a) for a in docker_args))}"

    logger.info("docker_executor_phase", phase=phase, network=network,
                container=container_name)

    try:
        proc = await asyncio.create_subprocess_shell(
            docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except TimeoutError:
            kill_cmd = f"sg docker -c 'docker kill {container_name}'"
            kill_proc = await asyncio.create_subprocess_shell(
                kill_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(kill_proc.communicate(), timeout=5)
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            logger.warning("docker_executor_timeout", phase=phase, timeout=timeout)
            return {
                "success": False,
                "output": f"Timeout after {timeout}s",
                "exit_code": -1,
            }

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        output = (stdout + "\n" + stderr).strip()
        exit_code = proc.returncode or 0

        # Detect OOM kill (Docker exit code 137 = SIGKILL from OOM)
        if exit_code == 137:
            logger.error("docker_executor_oom", phase=phase, memory=memory)
            output = f"Container killed by OOM (exit 137, limit={memory}). " + output

        logger.info("docker_executor_phase_done", phase=phase,
                    exit_code=exit_code, output_len=len(output))

        return {
            "success": exit_code == 0,
            "output": output[:5000],
            "exit_code": exit_code,
        }

    except Exception as e:
        logger.error("docker_executor_phase_error", phase=phase, error=str(e))
        return {
            "success": False,
            "output": str(e),
            "exit_code": -1,
        }


async def _ask_opus_to_fix(
    operations: list[BuildOperation],
    test_output: str,
    description: str,
) -> tuple[list[BuildOperation] | None, float]:
    """Ask Opus to fix code based on test failures. Returns (fixed_ops, cost)."""
    try:
        from agent.build.codegen import _parse_operations
        from agent.core.llm_provider import GenerateRequest, get_provider
        from agent.core.models import OPUS

        # Build file listing with size cap to avoid token limit issues
        _MAX_FILE_LISTING_CHARS = 30_000
        file_listing = ""
        for op in operations:
            entry = f"\n### {op.path}\n```python\n{op.content}\n```\n"
            if len(file_listing) + len(entry) > _MAX_FILE_LISTING_CHARS:
                file_listing += f"\n... ({len(operations)} files total, truncated)\n"
                break
            file_listing += entry

        prompt = (
            f"The following project was generated but tests failed.\n\n"
            f"## Original description\n{description[:500]}\n\n"
            f"## Current files\n{file_listing}\n\n"
            f"## Test output (FAILED)\n```\n{test_output[:3000]}\n```\n\n"
            f"Fix the code so tests pass. Respond with ONLY a JSON array of "
            f"write_file operations (same format as before). Include ALL files, "
            f"not just the changed ones. No markdown fences around the JSON."
        )

        provider = get_provider()
        response = await provider.generate(GenerateRequest(
            messages=[{"role": "user", "content": prompt}],
            model=OPUS.model_id,
            timeout=300,
            max_turns=1,
            no_tools=True,  # Pure text-in/JSON-out fix generation
        ))

        if not response.success or not response.text:
            return None, response.cost_usd

        fixed_ops = _parse_operations(response.text.strip(), 20)
        logger.info("docker_executor_fix_generated",
                    files=len(fixed_ops),
                    cost=round(response.cost_usd, 4))
        return fixed_ops, response.cost_usd

    except Exception as e:
        logger.error("docker_executor_fix_error", error=str(e))
        return None, 0.0
