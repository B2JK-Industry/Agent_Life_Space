"""
Tests pre agent/core/sandbox.py — Docker sandbox.

Pokrýva:
    - SandboxResult dataclass
    - SandboxUnavailableError
    - DockerSandbox init s defaults a custom hodnotami
    - _ensure_docker — Docker check, caching, error
    - run_python, run_code, run_command — parametre a routing
    - check_docker — available/unavailable
    - Timeout handling
    - Unsupported language

Docker nie je dostupný v CI → testujeme cez mock.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.sandbox import (
    DockerSandbox,
    SandboxResult,
    SandboxUnavailableError,
    _DEFAULT_CPUS,
    _DEFAULT_MEMORY,
    _DEFAULT_TIMEOUT,
    _MAX_TIMEOUT,
)


# --- SandboxResult ---


class TestSandboxResult:
    def test_success_result(self):
        r = SandboxResult(success=True, stdout="hello", image="python:3.12-slim")
        assert r.success is True
        assert r.stdout == "hello"
        assert r.timed_out is False

    def test_error_result(self):
        r = SandboxResult(success=False, stderr="error", exit_code=1)
        assert r.success is False
        assert r.exit_code == 1

    def test_timeout_result(self):
        r = SandboxResult(success=False, timed_out=True, image="python:3.12-slim")
        assert r.timed_out is True

    def test_to_dict_truncates(self):
        r = SandboxResult(
            success=True,
            stdout="x" * 10000,
            stderr="y" * 5000,
            image="test",
        )
        d = r.to_dict()
        assert len(d["stdout"]) == 5000
        assert len(d["stderr"]) == 2000

    def test_to_dict_fields(self):
        r = SandboxResult(success=True, stdout="ok", exit_code=0, image="alpine")
        d = r.to_dict()
        assert set(d.keys()) == {"success", "stdout", "stderr", "exit_code", "timed_out", "image"}


# --- SandboxUnavailableError ---


class TestSandboxUnavailableError:
    def test_is_exception(self):
        with pytest.raises(SandboxUnavailableError):
            raise SandboxUnavailableError("Docker not found")

    def test_message(self):
        e = SandboxUnavailableError("test msg")
        assert "test msg" in str(e)


# --- DockerSandbox Init ---


class TestDockerSandboxInit:
    def test_defaults(self):
        s = DockerSandbox()
        assert s._memory == _DEFAULT_MEMORY
        assert s._cpus == _DEFAULT_CPUS
        assert s._timeout == _DEFAULT_TIMEOUT
        assert s._network is False
        assert s._docker_verified is False

    def test_custom_values(self):
        s = DockerSandbox(memory_limit="512m", cpu_limit="2.0", network=True, timeout=120)
        assert s._memory == "512m"
        assert s._cpus == "2.0"
        assert s._network is True
        assert s._timeout == 120

    def test_timeout_capped(self):
        s = DockerSandbox(timeout=9999)
        assert s._timeout == _MAX_TIMEOUT


# --- _ensure_docker ---


class TestEnsureDocker:
    @pytest.mark.asyncio
    async def test_docker_available(self):
        s = DockerSandbox()
        with patch.object(s, "check_docker", return_value={"available": True}):
            await s._ensure_docker()
            assert s._docker_verified is True

    @pytest.mark.asyncio
    async def test_docker_unavailable_raises(self):
        s = DockerSandbox()
        with patch.object(s, "check_docker", return_value={"available": False, "error": "not found"}):
            with pytest.raises(SandboxUnavailableError, match="Docker nie je dostupný"):
                await s._ensure_docker()

    @pytest.mark.asyncio
    async def test_docker_cached_after_first_check(self):
        s = DockerSandbox()
        mock_check = AsyncMock(return_value={"available": True})
        s.check_docker = mock_check

        await s._ensure_docker()
        await s._ensure_docker()
        await s._ensure_docker()

        # Should only check once due to caching
        mock_check.assert_called_once()


# --- run_python ---


class TestRunPython:
    @pytest.mark.asyncio
    async def test_run_python_calls_docker_run(self):
        s = DockerSandbox()
        s._docker_verified = True

        mock_result = SandboxResult(success=True, stdout="hello", image="python:3.12-slim")
        with patch.object(s, "_docker_run", new_callable=AsyncMock, return_value=mock_result) as mock:
            result = await s.run_python("print('hello')")

        assert result.success is True
        mock.assert_called_once()
        call_args = mock.call_args
        assert call_args[0][0] == "python:3.12-slim"
        assert call_args[0][1] == ["python3"]

    @pytest.mark.asyncio
    async def test_run_python_with_packages(self):
        s = DockerSandbox()
        s._docker_verified = True

        mock_result = SandboxResult(success=True, stdout="ok", image="python:3.12-slim")
        with patch.object(s, "_docker_run", new_callable=AsyncMock, return_value=mock_result) as mock:
            await s.run_python("import requests", packages=["requests"])

        call_args = mock.call_args
        cmd = call_args[0][1]
        assert "bash" in cmd
        assert any("pip install" in str(c) for c in cmd)

    @pytest.mark.asyncio
    async def test_run_python_timeout_capped(self):
        s = DockerSandbox()
        s._docker_verified = True

        mock_result = SandboxResult(success=True, stdout="ok", image="python:3.12-slim")
        with patch.object(s, "_docker_run", new_callable=AsyncMock, return_value=mock_result) as mock:
            await s.run_python("pass", timeout=9999)

        call_args = mock.call_args
        assert call_args[0][2] == _MAX_TIMEOUT


# --- run_code ---


class TestRunCode:
    @pytest.mark.asyncio
    async def test_unsupported_language(self):
        s = DockerSandbox()
        s._docker_verified = True

        result = await s.run_code("cobol", "DISPLAY 'HI'")
        assert result.success is False
        assert "Unsupported language" in result.stderr

    @pytest.mark.asyncio
    async def test_supported_languages(self):
        s = DockerSandbox()
        s._docker_verified = True

        for lang, expected_image in [
            ("python", "python:3.12-slim"),
            ("node", "node:20-slim"),
            ("bash", "alpine:latest"),
            ("ruby", "ruby:3.2-slim"),
        ]:
            mock_result = SandboxResult(success=True, image=expected_image)
            with patch.object(s, "_docker_run", new_callable=AsyncMock, return_value=mock_result) as mock:
                await s.run_code(lang, "code")
                assert mock.call_args[0][0] == expected_image


# --- check_docker ---


class TestCheckDocker:
    @pytest.mark.asyncio
    async def test_check_docker_available(self):
        s = DockerSandbox()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'{"ok": true}', b""))

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            with patch("asyncio.wait_for", return_value=(b'{"ok": true}', b"")):
                result = await s.check_docker()

        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_check_docker_exception(self):
        s = DockerSandbox()

        with patch("asyncio.create_subprocess_shell", side_effect=FileNotFoundError("no docker")):
            result = await s.check_docker()

        assert result["available"] is False
        assert "error" in result
