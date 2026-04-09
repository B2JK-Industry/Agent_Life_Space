"""
Agent Life Space — Self-update capability.

Owner-only deterministic fast-forward update from a public git remote.

Design constraints:
  * Owner-only — denied for non-owner / group context.
  * Read-only checks first: dirty worktree → fail-closed.
  * `git fetch` against the configured remote.
  * Only fast-forward updates (`pull --ff-only`) — no rebase, no
    `reset --hard`, no destructive operations.
  * No self-kill. After a successful pull, the operator (or the
    existing systemd / supervisor / watchdog) restarts the process.
  * No LLM, no tool-use, no interactive approval prompts.
  * All exit paths return a small dataclass so callers can format
    a deterministic Telegram reply.

This module is intentionally tiny: it shells out to git in a
constrained way and reports the result. The brain layer is
responsible for owner gating and intent routing.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────


@dataclass
class SelfUpdateResult:
    """Outcome of a self-update attempt."""

    status: str               # "denied" | "no_repo" | "no_remote" |
                              # "dirty" | "up_to_date" | "updated" |
                              # "fast_forward_unavailable" | "error"
    message: str              # Human-readable summary for Telegram
    before_sha: str = ""
    after_sha: str = ""
    branch: str = ""
    remote_url: str = ""
    fetched_commits: int = 0
    # When True, the caller is expected to schedule a graceful
    # shutdown so the process supervisor (systemd / supervisor /
    # docker restart=always) can bring up a fresh process with the
    # newly pulled code. This is set ONLY when:
    #   * status == "updated"
    #   * AGENT_SELF_RESTART_AFTER_UPDATE is enabled
    #   * a process supervisor is detected (or explicitly declared)
    should_self_restart: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "before_sha": self.before_sha,
            "after_sha": self.after_sha,
            "branch": self.branch,
            "remote_url": self.remote_url,
            "fetched_commits": self.fetched_commits,
            "should_self_restart": self.should_self_restart,
            **self.extra,
        }


# ─────────────────────────────────────────────
# Self-restart opt-in
# ─────────────────────────────────────────────


def _is_self_restart_requested() -> bool:
    """Has the operator opted in to automatic post-update restart?

    Reads ``AGENT_SELF_RESTART_AFTER_UPDATE``. Truthy values:
    ``1``, ``true``, ``yes``, ``on``, ``systemd``. Default is OFF.
    """
    raw = os.environ.get("AGENT_SELF_RESTART_AFTER_UPDATE", "").strip().lower()
    return raw in {"1", "true", "yes", "on", "systemd", "supervisor", "docker"}


def _detect_process_supervisor() -> str:
    """Return the name of the supervising process manager, or "" if none.

    Detection order (cheapest first):
      1. ``AGENT_PROCESS_SUPERVISOR`` env (operator override)
      2. systemd: ``INVOCATION_ID`` is set when running under a unit
      3. supervisord: ``SUPERVISOR_ENABLED`` env
      4. docker / kubernetes: ``container`` env or ``/.dockerenv`` file
    """
    explicit = os.environ.get("AGENT_PROCESS_SUPERVISOR", "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("INVOCATION_ID"):
        return "systemd"
    if os.environ.get("SUPERVISOR_ENABLED"):
        return "supervisord"
    container_env = os.environ.get("container", "")
    if container_env:
        return container_env
    try:
        if os.path.exists("/.dockerenv"):
            return "docker"
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────
# git helpers
# ─────────────────────────────────────────────


async def _run_git(
    args: list[str],
    *,
    cwd: str,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    """Run a git command and return (rc, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return 124, "", "git command timed out"
        return (
            proc.returncode or 0,
            (out or b"").decode("utf-8", errors="replace"),
            (err or b"").decode("utf-8", errors="replace"),
        )
    except FileNotFoundError:
        return 127, "", "git binary not found in PATH"
    except Exception as exc:
        return 1, "", f"git invocation failed: {exc}"


# ─────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────


async def run_self_update(
    *,
    repo_root: str,
    is_owner: bool,
    is_group: bool = False,
) -> SelfUpdateResult:
    """Run the self-update workflow.

    Args:
        repo_root: Absolute path to the project root (the git working
            tree). The brain layer typically passes
            ``agent._data_dir.parent``.
        is_owner: Whether the request comes from the owner.
        is_group: Whether the request originated from a group chat
            (used as an extra denial signal — group context cannot
            request self-update even if owner is in the group).
    """
    if not is_owner or is_group:
        logger.info("self_update_denied", is_owner=is_owner, is_group=is_group)
        return SelfUpdateResult(
            status="denied",
            message="Self-update is owner-only and cannot be triggered from a group chat.",
        )

    if not repo_root or not os.path.isdir(os.path.join(repo_root, ".git")):
        return SelfUpdateResult(
            status="no_repo",
            message=(
                "This deployment is not a git working tree, so self-update "
                "is not available. Pull the project from "
                "https://github.com/B2JK-Industry/Agent_Life_Space and "
                "redeploy from a git checkout to enable it."
            ),
        )

    # 1. Determine current branch.
    rc, stdout, stderr = await _run_git(
        ["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root,
    )
    if rc != 0:
        return SelfUpdateResult(
            status="error",
            message=_friendly_git_error("rev-parse HEAD", stderr or stdout),
        )
    branch = stdout.strip()
    if not branch or branch == "HEAD":
        return SelfUpdateResult(
            status="error",
            message=(
                "I'm in a detached-HEAD state and self-update only "
                "supports a tracked branch (typically `main`)."
            ),
            branch=branch,
        )

    # 2. Find the remote tracking ref. We don't trust a hardcoded name
    #    like "origin" — operators may have renamed it.
    rc, stdout, _ = await _run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=repo_root,
    )
    upstream = stdout.strip() if rc == 0 else ""
    if not upstream:
        return SelfUpdateResult(
            status="no_remote",
            message=(
                f"Branch `{branch}` has no upstream tracking ref, so I "
                "cannot fast-forward. Configure a remote with "
                "`git branch --set-upstream-to=origin/main` and retry."
            ),
            branch=branch,
        )
    remote_name = upstream.split("/", 1)[0] if "/" in upstream else "origin"

    # 3. Get current SHA + remote URL.
    rc, before_sha, _ = await _run_git(["rev-parse", "HEAD"], cwd=repo_root)
    before_sha = before_sha.strip()
    rc, remote_url_out, _ = await _run_git(
        ["config", "--get", f"remote.{remote_name}.url"],
        cwd=repo_root,
    )
    remote_url = remote_url_out.strip()

    # 4. Dirty worktree? fail-closed.
    rc, status_out, status_err = await _run_git(
        ["status", "--porcelain"], cwd=repo_root,
    )
    if rc != 0:
        return SelfUpdateResult(
            status="error",
            message=_friendly_git_error("status", status_err or status_out),
            branch=branch,
            before_sha=before_sha,
            remote_url=remote_url,
        )
    if status_out.strip():
        modified = [line[3:].strip() for line in status_out.strip().splitlines()][:5]
        return SelfUpdateResult(
            status="dirty",
            message=(
                "Self-update refused: the working tree has uncommitted "
                "changes. Commit or stash first.\n"
                f"  • Modified (sample): {', '.join(modified) or 'see git status'}"
            ),
            branch=branch,
            before_sha=before_sha,
            remote_url=remote_url,
        )

    # 5. Fetch remote (no merge, no rebase).
    rc, fetch_out, fetch_err = await _run_git(
        ["fetch", "--prune", remote_name],
        cwd=repo_root,
        timeout=120.0,
    )
    if rc != 0:
        return SelfUpdateResult(
            status="error",
            message=_friendly_git_error(f"fetch {remote_name}", fetch_err or fetch_out),
            branch=branch,
            before_sha=before_sha,
            remote_url=remote_url,
        )

    # 6. How many commits behind upstream?
    rc, count_out, _ = await _run_git(
        ["rev-list", "--count", f"HEAD..{upstream}"],
        cwd=repo_root,
    )
    behind = int(count_out.strip()) if count_out.strip().isdigit() else 0
    rc, ahead_out, _ = await _run_git(
        ["rev-list", "--count", f"{upstream}..HEAD"],
        cwd=repo_root,
    )
    ahead = int(ahead_out.strip()) if ahead_out.strip().isdigit() else 0

    if behind == 0:
        return SelfUpdateResult(
            status="up_to_date",
            message=f"Already up to date on `{branch}` ({before_sha[:7]}). Nothing to pull.",
            branch=branch,
            before_sha=before_sha,
            after_sha=before_sha,
            remote_url=remote_url,
            fetched_commits=0,
        )

    if ahead > 0:
        return SelfUpdateResult(
            status="fast_forward_unavailable",
            message=(
                f"Cannot fast-forward: branch `{branch}` is {ahead} "
                f"commit(s) ahead of `{upstream}` (and {behind} behind). "
                "I refuse to rebase or reset; resolve the divergence "
                "manually first."
            ),
            branch=branch,
            before_sha=before_sha,
            remote_url=remote_url,
            fetched_commits=behind,
        )

    # 7. Fast-forward pull.
    rc, pull_out, pull_err = await _run_git(
        ["pull", "--ff-only", remote_name, branch],
        cwd=repo_root,
        timeout=180.0,
    )
    if rc != 0:
        return SelfUpdateResult(
            status="error",
            message=_friendly_git_error(f"pull --ff-only {remote_name} {branch}", pull_err or pull_out),
            branch=branch,
            before_sha=before_sha,
            remote_url=remote_url,
            fetched_commits=behind,
        )

    rc, after_sha, _ = await _run_git(["rev-parse", "HEAD"], cwd=repo_root)
    after_sha = after_sha.strip()

    # Decide whether to schedule a self-restart. The opt-in flag is
    # checked AGAINST a detected supervisor — we refuse to self-kill
    # in an environment where nothing would bring the process back.
    self_restart_requested = _is_self_restart_requested()
    supervisor = _detect_process_supervisor()
    should_self_restart = bool(self_restart_requested and supervisor)

    if should_self_restart:
        message = (
            f"Fast-forwarded `{branch}` from {before_sha[:7]} to "
            f"{after_sha[:7]} ({behind} commit(s)).\n\n"
            f"Process supervisor detected: `{supervisor}`. I will now "
            "drain in-flight work and exit gracefully so the supervisor "
            "starts a fresh process with the new code. You should see "
            "the new version reply to your next message."
        )
    elif self_restart_requested and not supervisor:
        # Operator asked for auto-restart but the environment cannot
        # support it. We pull successfully but refuse to self-kill,
        # AND we surface the misconfiguration so it can be fixed.
        message = (
            f"Fast-forwarded `{branch}` from {before_sha[:7]} to "
            f"{after_sha[:7]} ({behind} commit(s)).\n\n"
            "AGENT_SELF_RESTART_AFTER_UPDATE is set but no process "
            "supervisor was detected (no INVOCATION_ID, no "
            "AGENT_PROCESS_SUPERVISOR, no /.dockerenv). I will not "
            "self-kill in an unsupervised environment — restart "
            "manually, then set AGENT_PROCESS_SUPERVISOR=systemd "
            "(or run me under systemd / supervisord / docker)."
        )
    else:
        message = (
            f"Fast-forwarded `{branch}` from {before_sha[:7]} to "
            f"{after_sha[:7]} ({behind} commit(s)).\n"
            "A restart through your existing ops mechanism (systemd, "
            "supervisor, watchdog) is required for the new code to "
            "take effect — I will not self-kill (set "
            "AGENT_SELF_RESTART_AFTER_UPDATE=1 to enable automatic "
            "self-restart under a supervisor)."
        )

    return SelfUpdateResult(
        status="updated",
        message=message,
        branch=branch,
        before_sha=before_sha,
        after_sha=after_sha,
        remote_url=remote_url,
        fetched_commits=behind,
        should_self_restart=should_self_restart,
    )


def _friendly_git_error(action: str, raw: str) -> str:
    """Map a noisy git error string to a short human sentence."""
    msg = (raw or "").strip().lower()
    if not msg:
        return f"git {action} failed (no detail)."
    if "permission denied" in msg or "could not read" in msg:
        return f"git {action} failed: permission denied."
    if "could not resolve host" in msg:
        return f"git {action} failed: could not reach the remote (DNS / network)."
    if "authentication failed" in msg:
        return f"git {action} failed: authentication failed."
    if "not a git repository" in msg:
        return f"git {action} failed: not a git working tree."
    if "diverged" in msg or "non-fast-forward" in msg:
        return (
            f"git {action} refused: branch has diverged from upstream — "
            "fast-forward not possible."
        )
    # Truncate; never echo more than ~200 chars to the user.
    return f"git {action} failed: {(raw or '').strip()[:200]}"


__all__ = ["SelfUpdateResult", "run_self_update"]
