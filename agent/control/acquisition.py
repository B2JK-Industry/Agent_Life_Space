"""
Agent Life Space — Repository Acquisition

Deterministic import path for supported git sources.
Allows unified operator intake to acquire repositories behind honest gating
instead of pretending git_url work can already execute without preparation.
"""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass
class RepoAcquisitionPreview:
    """Qualification-time view of a git_url intake."""

    supported: bool
    source_kind: str = "unknown"
    normalized_url: str = ""
    risk_level: str = "medium"
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    environment_profile_id: str = "repo_import_mirror"

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "source_kind": self.source_kind,
            "normalized_url": self.normalized_url,
            "risk_level": self.risk_level,
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "environment_profile_id": self.environment_profile_id,
        }


@dataclass
class RepoAcquisitionResult:
    """Runtime result of importing a repository from git_url."""

    acquired: bool
    acquisition_id: str = ""
    repo_path: str = ""
    source_kind: str = "unknown"
    normalized_url: str = ""
    environment_profile_id: str = "repo_import_mirror"
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "acquired": self.acquired,
            "acquisition_id": self.acquisition_id,
            "repo_path": self.repo_path,
            "source_kind": self.source_kind,
            "normalized_url": self.normalized_url,
            "environment_profile_id": self.environment_profile_id,
            "warnings": list(self.warnings),
            "error": self.error,
            "created_at": self.created_at,
        }


def inspect_git_url(git_url: str) -> RepoAcquisitionPreview:
    """Determine whether a git_url can be acquired through the supported path."""
    if not git_url:
        return RepoAcquisitionPreview(
            supported=False,
            blockers=["git_url is required for acquisition inspection."],
        )

    parsed = urlparse(git_url)
    if git_url.startswith("file://"):
        repo_path = Path(parsed.path).resolve()
        blockers: list[str] = []
        warnings: list[str] = []
        if not repo_path.exists():
            blockers.append(f"file:// source does not exist: {repo_path}")
        elif not (repo_path / ".git").exists():
            blockers.append(f"file:// source is not a git repository: {repo_path}")
        else:
            warnings.append("Local file:// repository will be cloned into the managed acquisition cache.")
        return RepoAcquisitionPreview(
            supported=not blockers,
            source_kind="file_url",
            normalized_url=repo_path.as_uri(),
            risk_level="low",
            warnings=warnings,
            blockers=blockers,
        )

    if git_url.startswith("git@"):
        return RepoAcquisitionPreview(
            supported=True,
            source_kind="ssh_git",
            normalized_url=git_url,
            risk_level="medium",
            warnings=[
                "SSH git acquisition is supported, but runtime clone still depends on host git/network availability."
            ],
        )

    if parsed.scheme == "https" and parsed.netloc:
        return RepoAcquisitionPreview(
            supported=True,
            source_kind="https_git",
            normalized_url=git_url,
            risk_level="medium",
            warnings=[
                "HTTPS git acquisition is supported, but runtime clone still depends on host git/network availability."
            ],
        )

    return RepoAcquisitionPreview(
        supported=False,
        normalized_url=git_url,
        blockers=[
            "Only file://, https://, and git@ SSH repository sources are supported for git_url acquisition."
        ],
    )


class RepoAcquisitionService:
    """Clone supported repositories into a managed acquisition cache."""

    def __init__(self, root_path: str) -> None:
        self._root = Path(root_path)

    def inspect(self, git_url: str) -> RepoAcquisitionPreview:
        return inspect_git_url(git_url)

    def acquire(self, git_url: str) -> RepoAcquisitionResult:
        preview = self.inspect(git_url)
        acquisition_id = self._acquisition_id(preview.normalized_url or git_url)
        if not preview.supported:
            return RepoAcquisitionResult(
                acquired=False,
                acquisition_id=acquisition_id,
                source_kind=preview.source_kind,
                normalized_url=preview.normalized_url or git_url,
                environment_profile_id=preview.environment_profile_id,
                warnings=list(preview.warnings),
                error="; ".join(preview.blockers),
            )

        self._root.mkdir(parents=True, exist_ok=True)
        target = self._target_path(acquisition_id)
        if target.exists() and (target / ".git").exists():
            refresh = self._run_git(
                ["git", "-C", str(target), "fetch", "--all", "--tags", "--prune"],
                timeout=120,
            )
            if refresh["ok"]:
                return RepoAcquisitionResult(
                    acquired=True,
                    acquisition_id=acquisition_id,
                    repo_path=str(target),
                    source_kind=preview.source_kind,
                    normalized_url=preview.normalized_url,
                    environment_profile_id=preview.environment_profile_id,
                    warnings=list(preview.warnings),
                )
            # Fall back to the cached clone if fetch is the only failing step.
            return RepoAcquisitionResult(
                acquired=True,
                acquisition_id=acquisition_id,
                repo_path=str(target),
                source_kind=preview.source_kind,
                normalized_url=preview.normalized_url,
                environment_profile_id=preview.environment_profile_id,
                warnings=[
                    *preview.warnings,
                    "Acquisition cache reused because refresh failed; working from the last successful clone.",
                ],
                error=refresh["error"],
            )

        if target.exists() and not (target / ".git").exists():
            target = self._target_path(f"{acquisition_id}-{uuid.uuid4().hex[:6]}")

        clone_command = ["git", "clone", "--quiet"]
        if preview.source_kind == "file_url":
            clone_command.append("--no-hardlinks")
        clone_command.extend([preview.normalized_url or git_url, str(target)])
        result = self._run_git(clone_command, timeout=120)
        if not result["ok"]:
            return RepoAcquisitionResult(
                acquired=False,
                acquisition_id=acquisition_id,
                repo_path=str(target),
                source_kind=preview.source_kind,
                normalized_url=preview.normalized_url,
                environment_profile_id=preview.environment_profile_id,
                warnings=list(preview.warnings),
                error=result["error"],
            )

        return RepoAcquisitionResult(
            acquired=True,
            acquisition_id=acquisition_id,
            repo_path=str(target),
            source_kind=preview.source_kind,
            normalized_url=preview.normalized_url,
            environment_profile_id=preview.environment_profile_id,
            warnings=list(preview.warnings),
        )

    def _target_path(self, acquisition_id: str) -> Path:
        return self._root / acquisition_id

    def _acquisition_id(self, normalized_url: str) -> str:
        return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:12]

    def _run_git(self, command: list[str], *, timeout: int) -> dict[str, Any]:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed git argv, shell disabled
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except Exception as exc:  # pragma: no cover - defensive runtime path
            return {"ok": False, "error": str(exc)}

        if completed.returncode == 0:
            return {"ok": True, "stdout": completed.stdout.strip()}
        error = (completed.stderr or completed.stdout).strip()
        return {
            "ok": False,
            "error": error or f"git command failed with exit code {completed.returncode}",
        }
