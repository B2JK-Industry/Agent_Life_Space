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


class SandboxUnavailableError(Exception):
    """Docker nie je dostupný — sandbox nemôže bežať."""


class DockerSandbox:
    """
    Docker-based sandbox for safe code execution.
    POVINNÝ pre všetko čo spúšťa kód. Žiadny code execution bez sandboxu.
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
        self._docker_verified = False

    async def run_python(
        self,
        code: str,
        packages: list[str] | None = None,
        timeout: int | None = None,
    ) -> SandboxResult:
        """
        Run Python code in isolated container.
        Code is passed via stdin to avoid shell escaping issues.
        """
        image = "python:3.12-slim"
        timeout = min(timeout or self._timeout, _MAX_TIMEOUT)

        if packages:
            pip_install = " && ".join(f"pip install -q {p}" for p in packages)
            cmd = ["bash", "-c", f"{pip_install} && python3"]
        else:
            cmd = ["python3"]

        return await self._docker_run(image, cmd, timeout, stdin_data=code)

    async def run_code(
        self,
        language: str,
        code: str,
        timeout: int | None = None,
    ) -> SandboxResult:
        """
        Run code in specified language.
        Supports: python, node, bash, ruby.
        Code passed via stdin to avoid escaping issues.
        """
        timeout = min(timeout or self._timeout, _MAX_TIMEOUT)

        lang_config: dict[str, tuple[str, list[str]]] = {
            "python": ("python:3.12-slim", ["python3"]),
            "node": ("node:20-slim", ["node"]),
            "bash": ("alpine:latest", ["sh"]),
            "ruby": ("ruby:3.2-slim", ["ruby"]),
        }

        config = lang_config.get(language.lower())
        if not config:
            return SandboxResult(
                success=False,
                stderr=f"Unsupported language: {language}. Supported: {list(lang_config.keys())}",
                exit_code=1,
            )

        image, cmd = config
        return await self._docker_run(image, cmd, timeout, stdin_data=code)

    async def run_command(
        self,
        image: str,
        command: list[str],
        timeout: int | None = None,
    ) -> SandboxResult:
        """Run arbitrary command in specified Docker image."""
        timeout = min(timeout or self._timeout, _MAX_TIMEOUT)
        return await self._docker_run(image, command, timeout)

    async def _ensure_docker(self) -> None:
        """Verify Docker is available. Raises SandboxUnavailableError if not."""
        if self._docker_verified:
            return
        status = await self.check_docker()
        if not status.get("available"):
            raise SandboxUnavailableError(
                "Docker nie je dostupný. Sandbox je povinný pre spúšťanie kódu. "
                "Nainštaluj Docker: https://docs.docker.com/engine/install/"
            )
        self._docker_verified = True

    # Povolené Docker image — whitelist proti injection cez image name
    _ALLOWED_IMAGES = frozenset({
        "python:3.12-slim",
        "node:20-slim",
        "alpine:latest",
        "ruby:3.2-slim",
    })

    async def _docker_run(
        self,
        image: str,
        command: list[str],
        timeout: int,
        stdin_data: str | None = None,
    ) -> SandboxResult:
        """Core Docker run with all safety constraints."""
        await self._ensure_docker()

        # SECURITY: Validate image against whitelist
        if image not in self._ALLOWED_IMAGES:
            logger.warning("sandbox_image_rejected", image=image)
            return SandboxResult(
                success=False,
                stderr=f"Image '{image}' not in whitelist. Allowed: {sorted(self._ALLOWED_IMAGES)}",
                exit_code=1,
                image=image,
            )

        # SECURITY: Validate command elements — no shell metacharacters
        import re
        _SHELL_META = re.compile(r"[;&|`$(){}\\\"']")
        for part in command:
            if _SHELL_META.search(part) and part != command[-1]:
                # Posledný element môže byť bash -c script (stdin je bezpečnejší)
                logger.warning("sandbox_cmd_suspicious", part=part[:50])

        network_flag = "bridge" if self._network else "none"

        # Unique container name pre timeout kill
        import uuid
        container_name = f"sandbox-{uuid.uuid4().hex[:12]}"

        # Build Docker args as explicit list — nie string concatenation
        docker_args = [
            "docker", "run", "--rm",
            f"--name={container_name}",
            f"--memory={self._memory}",
            f"--cpus={self._cpus}",
            f"--network={network_flag}",
            "--read-only",
            "--tmpfs", "/tmp:rw,size=64m",
            "--pids-limit=50",
            "--security-opt=no-new-privileges",
        ]
        if stdin_data:
            docker_args.append("-i")
        docker_args.append(image)
        docker_args.extend(command)

        # sg docker -c vyžaduje shell wrapping (Linux group permissions)
        # Ale teraz s validovaným image a explicitným arg listom
        import shlex
        docker_cmd = f"sg docker -c {shlex.quote(' '.join(shlex.quote(a) for a in docker_args))}"

        logger.info(
            "sandbox_run",
            image=image,
            memory=self._memory,
            network=network_flag,
            timeout=timeout,
            container=container_name,
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                docker_cmd,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdin_bytes = stdin_data.encode() if stdin_data else None
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=stdin_bytes), timeout=timeout,
                )
            except (asyncio.TimeoutError, TimeoutError):
                # Kill the Docker container directly (proc.kill() kills only sg wrapper)
                kill_cmd = f"sg docker -c 'docker kill {container_name}'"
                kill_proc = await asyncio.create_subprocess_shell(
                    kill_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(kill_proc.communicate(), timeout=5)
                # Now kill the wrapper process too
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except (asyncio.TimeoutError, TimeoutError):
                    pass
                logger.warning("sandbox_timeout", image=image, timeout=timeout,
                               container=container_name)
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


