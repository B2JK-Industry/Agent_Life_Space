"""
Agent Life Space — Obolos CLI Executor

Thin async wrapper around the `obolos` CLI binary.
All commands use `--json` for machine-readable output.
Errors are detected via exit code + stderr parsing.

This module does NOT depend on the ALS gateway layer.
It spawns `obolos` as a subprocess directly.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Configurable path; auto-detected from PATH if not set.
_CLI_BINARY: str | None = None


def _find_cli() -> str | None:
    """Find the obolos binary on PATH."""
    global _CLI_BINARY  # noqa: PLW0603
    if _CLI_BINARY is not None:
        return _CLI_BINARY
    found = shutil.which("obolos")
    if not found:
        # Common user-local install path
        import os
        local_bin = os.path.expanduser("~/.local/bin/obolos")
        if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
            found = local_bin
    _CLI_BINARY = found
    return found


def cli_available() -> bool:
    """Check whether the obolos CLI is installed and reachable."""
    return _find_cli() is not None


async def run_cli(
    *args: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run an obolos CLI command and return parsed JSON result.

    Returns:
        {"ok": True, "data": <parsed JSON>, "raw": <stdout>}
        {"ok": False, "error": <message>, "exit_code": <int>, "raw": <stderr>}
    """
    binary = _find_cli()
    if not binary:
        return {"ok": False, "error": "obolos CLI not installed", "exit_code": -1, "raw": ""}

    cmd = [binary, *args, "--json"]
    logger.debug("obolos_cli_exec", cmd=" ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except TimeoutError:
        return {"ok": False, "error": f"obolos CLI timeout ({timeout}s)", "exit_code": -1, "raw": ""}
    except Exception as e:
        return {"ok": False, "error": f"obolos CLI exec failed: {e}", "exit_code": -1, "raw": ""}

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    exit_code = proc.returncode or 0

    if exit_code != 0:
        error_msg = stderr.removeprefix("Error: ").strip() if stderr else f"exit code {exit_code}"
        logger.warning("obolos_cli_error", cmd=" ".join(args), exit_code=exit_code, error=error_msg[:200])
        return {"ok": False, "error": error_msg, "exit_code": exit_code, "raw": stderr}

    # Parse JSON from stdout
    try:
        data = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        data = {"raw_output": stdout}

    return {"ok": True, "data": data, "raw": stdout}


# ─── Convenience wrappers ───


async def cli_search(query: str = "") -> dict[str, Any]:
    args = ["search"]
    if query:
        args.append(query)
    return await run_cli(*args)


async def cli_balance() -> dict[str, Any]:
    return await run_cli("balance")


async def cli_listing_list(*, status: str = "") -> dict[str, Any]:
    args = ["listing", "list"]
    if status:
        args.append(f"--status={status}")
    return await run_cli(*args)


async def cli_listing_info(listing_id: str) -> dict[str, Any]:
    return await run_cli("listing", "info", listing_id)


async def cli_listing_bid(
    listing_id: str,
    *,
    price: float,
    delivery_hours: int = 0,
    message: str = "",
) -> dict[str, Any]:
    args = ["listing", "bid", listing_id, "--price", str(price)]
    if delivery_hours:
        args.extend(["--delivery", str(delivery_hours)])
    if message:
        args.extend(["--message", message])
    return await run_cli(*args)


async def cli_job_list() -> dict[str, Any]:
    return await run_cli("job", "list")


async def cli_listing_create(
    *,
    title: str,
    description: str = "",
    max_budget: float = 0,
    deadline: str = "7d",
) -> dict[str, Any]:
    args = ["listing", "create", "--title", title]
    if description:
        args.extend(["--description", description])
    if max_budget:
        args.extend(["--max-budget", str(max_budget)])
    if deadline:
        args.extend(["--deadline", deadline])
    return await run_cli(*args)


async def cli_job_info(job_id: str) -> dict[str, Any]:
    return await run_cli("job", "info", job_id)


async def cli_job_submit(job_id: str, *, deliverable: str) -> dict[str, Any]:
    return await run_cli("job", "submit", job_id, "--deliverable", deliverable)


async def cli_job_complete(job_id: str, *, reason: str = "") -> dict[str, Any]:
    args = ["job", "complete", job_id]
    if reason:
        args.extend(["--reason", reason])
    return await run_cli(*args)


async def cli_job_reject(job_id: str, *, reason: str = "") -> dict[str, Any]:
    args = ["job", "reject", job_id]
    if reason:
        args.extend(["--reason", reason])
    return await run_cli(*args)


async def cli_reputation_check(address: str) -> dict[str, Any]:
    return await run_cli("reputation", "check", address)
