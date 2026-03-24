"""
Agent Life Space — Docker Sandbox

Bezpečný priestor na experimenty. John môže spúšťať cudzí kód
bez rizika pre server.

Safety:
    - Kontajner beží s --rm (auto-delete po skončení)
    - --network=none (žiadny internet z kontajnera, ak nie je povolený)
    - --memory limit (max 256MB RAM)
    - --cpus limit (max 1 CPU)
    - Timeout na každý run (default 60s)
    - Read-only filesystem (--read-only), zápis len do /tmp
    - Žiadne volume mounts do host systému (okrem explicit)
    - Runs via 'sg docker' wrapper (group permission)

Usage:
    sandbox = DockerSandbox()
    result = await sandbox.run_python("print('hello')")
    result = await sandbox.run_code("node", "console.log('hi')")
    result = await sandbox.run_command("alpine", ["ls", "-la"])
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Safety defaults
_DEFAULT_TIMEOUT = 60
_MAX_TIMEOUT = 300
_DEFAULT_MEMORY = "256m"
_DEFAULT_CPUS = "1.0"


class SandboxResult:
    """Result of a sandboxed execution."""

    def __init__(
        self,
        success: bool,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        timed_out: bool = False,
        image: str = "",
    ) -> None:
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.image = image

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout[:5000],
            "stderr": self.stderr[:2000],
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "image": self.image,
        }


class DockerSandbox:
    """
    Docker-based sandbox for safe code execution.
    """

    def __init__(
        self,
        memory_limit: str = _DEFAULT_MEMORY,
        cpu_limit: str = _DEFAULT_CPUS,
        network: bool = False,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._memory = memory_limit
        self._cpus = cpu_limit
        self._network = network
        self._timeout = min(timeout, _MAX_TIMEOUT)

    async def run_python(
        self,
        code: str,
        packages: list[str] | None = None,
        timeout: int | None = None,
    ) -> SandboxResult:
        """
        Run Python code in isolated container.
        Optionally install pip packages first.
        """
        image = "python:3.12-slim"
        timeout = min(timeout or self._timeout, _MAX_TIMEOUT)

        if packages:
            # Install packages then run code
            pip_install = " && ".join(f"pip install -q {p}" for p in packages)
            cmd = f"bash -c '{pip_install} && python3 -c {_shell_escape(code)}'"
        else:
            cmd = f"python3 -c {_shell_escape(code)}"

        return await self._docker_run(image, cmd, timeout)

    async def run_code(
        self,
        language: str,
        code: str,
        timeout: int | None = None,
    ) -> SandboxResult:
        """
        Run code in specified language.
        Supports: python, node, bash, ruby.
        """
        timeout = min(timeout or self._timeout, _MAX_TIMEOUT)

        lang_config = {
            "python": ("python:3.12-slim", f"python3 -c {_shell_escape(code)}"),
            "node": ("node:20-slim", f"node -e {_shell_escape(code)}"),
            "bash": ("alpine:latest", f"sh -c {_shell_escape(code)}"),
            "ruby": ("ruby:3.2-slim", f"ruby -e {_shell_escape(code)}"),
        }

        config = lang_config.get(language.lower())
        if not config:
            return SandboxResult(
                success=False,
                stderr=f"Unsupported language: {language}. Supported: {list(lang_config.keys())}",
                exit_code=1,
            )

        image, cmd = config
        return await self._docker_run(image, cmd, timeout)

    async def run_command(
        self,
        image: str,
        command: list[str],
        timeout: int | None = None,
    ) -> SandboxResult:
        """Run arbitrary command in specified Docker image."""
        timeout = min(timeout or self._timeout, _MAX_TIMEOUT)
        cmd = " ".join(command)
        return await self._docker_run(image, cmd, timeout)

    async def _docker_run(
        self,
        image: str,
        command: str,
        timeout: int,
    ) -> SandboxResult:
        """Core Docker run with all safety constraints."""
        network_flag = "bridge" if self._network else "none"

        docker_cmd = (
            f"sg docker -c 'docker run --rm "
            f"--memory={self._memory} "
            f"--cpus={self._cpus} "
            f"--network={network_flag} "
            f"--read-only "
            f"--tmpfs /tmp:rw,size=64m "
            f"--pids-limit=50 "
            f"--security-opt=no-new-privileges "
            f"{image} {command}'"
        )

        logger.info(
            "sandbox_run",
            image=image,
            memory=self._memory,
            network=network_flag,
            timeout=timeout,
        )

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
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("sandbox_timeout", image=image, timeout=timeout)
                return SandboxResult(
                    success=False,
                    stderr=f"Timeout after {timeout}s",
                    timed_out=True,
                    image=image,
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0

            success = exit_code == 0
            logger.info(
                "sandbox_done",
                image=image,
                exit_code=exit_code,
                stdout_len=len(stdout),
            )

            return SandboxResult(
                success=success,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                image=image,
            )

        except Exception as e:
            logger.error("sandbox_error", image=image, error=str(e))
            return SandboxResult(
                success=False,
                stderr=str(e),
                exit_code=1,
                image=image,
            )

    async def check_docker(self) -> dict[str, Any]:
        """Check if Docker is available."""
        try:
            proc = await asyncio.create_subprocess_shell(
                "sg docker -c 'docker info --format json' 2>/dev/null",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return {
                "available": proc.returncode == 0,
                "info": stdout.decode()[:500] if proc.returncode == 0 else "",
            }
        except Exception as e:
            return {"available": False, "error": str(e)}


def _shell_escape(code: str) -> str:
    """Escape code for shell command."""
    escaped = code.replace("'", "'\\''")
    return f"'{escaped}'"
