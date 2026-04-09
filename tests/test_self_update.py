"""
Regression tests for the deterministic self-update capability.

The self_update module is intentionally tiny: it shells out to git
in a constrained way and reports the outcome. These tests cover the
six exit branches:

  • denied (non-owner / group)
  • no_repo (no .git)
  • no_remote (no upstream tracking ref)
  • dirty (uncommitted changes)
  • up_to_date (no commits behind)
  • fast_forward_unavailable (diverged)
  • updated (successful fast-forward)

We use a real on-disk git repo for the up_to_date case and mock
``_run_git`` for the rest. No network is required.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.self_update import SelfUpdateResult, run_self_update

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _git(*args: str, cwd: str) -> None:
    """Run git synchronously for fixture setup."""
    subprocess.run(  # noqa: S603, UP022 - test fixture; pinned argv
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


@pytest.fixture
def real_git_repo():
    """Create a tiny on-disk git repo with a single commit."""
    with tempfile.TemporaryDirectory() as tmp:
        _git("init", "-q", "-b", "main", cwd=tmp)
        with open(os.path.join(tmp, "README.md"), "w") as f:
            f.write("hi\n")
        _git("add", ".", cwd=tmp)
        _git("commit", "-q", "-m", "initial", cwd=tmp)
        yield tmp


# ─────────────────────────────────────────────
# Owner gating
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_denied_for_non_owner():
    result = await run_self_update(
        repo_root="/tmp/whatever", is_owner=False, is_group=False,
    )
    assert isinstance(result, SelfUpdateResult)
    assert result.status == "denied"
    assert "owner" in result.message.lower()


@pytest.mark.asyncio
async def test_denied_for_group_chat():
    result = await run_self_update(
        repo_root="/tmp/whatever", is_owner=True, is_group=True,
    )
    assert result.status == "denied"


# ─────────────────────────────────────────────
# Repo / remote checks
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_repo_returns_no_repo_status():
    with tempfile.TemporaryDirectory() as tmp:
        result = await run_self_update(
            repo_root=tmp, is_owner=True, is_group=False,
        )
    assert result.status == "no_repo"
    assert "git" in result.message.lower()


@pytest.mark.asyncio
async def test_no_upstream_branch(real_git_repo):
    """A repo with no upstream tracking ref returns no_remote."""
    result = await run_self_update(
        repo_root=real_git_repo, is_owner=True, is_group=False,
    )
    assert result.status == "no_remote"
    assert "upstream" in result.message.lower() or "tracking" in result.message.lower()


# ─────────────────────────────────────────────
# Mocked git paths
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dirty_worktree_fail_closed():
    """A dirty worktree must fail-closed before any fetch."""
    fake = AsyncMock(side_effect=[
        (0, "main\n", ""),                  # rev-parse --abbrev-ref HEAD
        (0, "origin/main\n", ""),           # rev-parse @{u}
        (0, "abc1234567\n", ""),            # rev-parse HEAD
        (0, "git@github.com:foo/bar.git\n", ""),  # config remote.origin.url
        (0, " M agent/core/brain.py\n", ""),  # status --porcelain
    ])

    with patch("agent.core.self_update._run_git", fake), \
         patch("os.path.isdir", return_value=True):
        result = await run_self_update(
            repo_root="/fake/repo", is_owner=True, is_group=False,
        )

    assert result.status == "dirty"
    assert "uncommitted" in result.message.lower() or "stash" in result.message.lower()
    # fetch must NOT have been called.
    assert all(call.args[0][0] != "fetch" for call in fake.call_args_list)


@pytest.mark.asyncio
async def test_up_to_date():
    fake = AsyncMock(side_effect=[
        (0, "main\n", ""),                  # rev-parse --abbrev-ref HEAD
        (0, "origin/main\n", ""),           # rev-parse @{u}
        (0, "abc1234567\n", ""),            # rev-parse HEAD
        (0, "https://github.com/foo/bar\n", ""),  # config remote.origin.url
        (0, "", ""),                        # status --porcelain (clean)
        (0, "", ""),                        # fetch
        (0, "0\n", ""),                     # rev-list --count HEAD..upstream
        (0, "0\n", ""),                     # rev-list --count upstream..HEAD
    ])

    with patch("agent.core.self_update._run_git", fake), \
         patch("os.path.isdir", return_value=True):
        result = await run_self_update(
            repo_root="/fake/repo", is_owner=True, is_group=False,
        )

    assert result.status == "up_to_date"
    assert "up to date" in result.message.lower()


@pytest.mark.asyncio
async def test_fast_forward_unavailable_when_diverged():
    fake = AsyncMock(side_effect=[
        (0, "main\n", ""),                  # rev-parse --abbrev-ref HEAD
        (0, "origin/main\n", ""),           # rev-parse @{u}
        (0, "abc1234567\n", ""),            # rev-parse HEAD
        (0, "https://github.com/foo/bar\n", ""),  # remote URL
        (0, "", ""),                        # clean
        (0, "", ""),                        # fetch
        (0, "3\n", ""),                     # behind
        (0, "2\n", ""),                     # ahead — diverged
    ])

    with patch("agent.core.self_update._run_git", fake), \
         patch("os.path.isdir", return_value=True):
        result = await run_self_update(
            repo_root="/fake/repo", is_owner=True, is_group=False,
        )

    assert result.status == "fast_forward_unavailable"
    assert "diverged" in result.message.lower() or "ahead" in result.message.lower() or "fast-forward" in result.message.lower()


@pytest.mark.asyncio
async def test_successful_fast_forward():
    fake = AsyncMock(side_effect=[
        (0, "main\n", ""),                  # rev-parse --abbrev-ref HEAD
        (0, "origin/main\n", ""),           # rev-parse @{u}
        (0, "abc1234567\n", ""),            # before
        (0, "https://github.com/foo/bar\n", ""),  # remote URL
        (0, "", ""),                        # clean
        (0, "", ""),                        # fetch
        (0, "5\n", ""),                     # behind
        (0, "0\n", ""),                     # ahead = 0 → ff possible
        (0, "Updating abc1234..def5678\n", ""),  # pull --ff-only
        (0, "def5678901\n", ""),            # rev-parse HEAD after
    ])

    with patch("agent.core.self_update._run_git", fake), \
         patch("os.path.isdir", return_value=True):
        result = await run_self_update(
            repo_root="/fake/repo", is_owner=True, is_group=False,
        )

    assert result.status == "updated"
    assert result.before_sha.startswith("abc")
    assert result.after_sha.startswith("def")
    assert result.fetched_commits == 5
    assert "restart" in result.message.lower()


@pytest.mark.asyncio
async def test_git_error_friendly():
    """A failing git command should produce a short, human sentence."""
    fake = AsyncMock(side_effect=[
        (0, "main\n", ""),                  # branch
        (1, "", "fatal: could not resolve host: github.com"),  # rev-parse @{u} fails
    ])

    with patch("agent.core.self_update._run_git", fake), \
         patch("os.path.isdir", return_value=True):
        result = await run_self_update(
            repo_root="/fake/repo", is_owner=True, is_group=False,
        )

    # Either we get an error or a no_remote (depending on which branch
    # parses the empty stdout). Both are acceptable as long as the
    # message is short and not raw.
    assert result.status in {"no_remote", "error"}
    assert len(result.message) < 500
    assert "fatal:" not in result.message  # raw git noise stripped
