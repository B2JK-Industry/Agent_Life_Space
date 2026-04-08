"""
Agent Life Space — Runtime LLM Control

Persistent operator-facing LLM attachment/backend overrides.
Lets operators detach the LLM entirely, or override backend/provider
without rewriting .env for every experiment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

_ALLOWED_BACKENDS = {"cli", "api"}
_ALLOWED_PROVIDERS = {"anthropic", "openai", "local"}


def _normalize_backend(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_provider(value: str) -> str:
    return str(value or "").strip().lower()


def _resolve_data_dir(data_dir: str | Path | None = None) -> Path:
    if data_dir is not None and str(data_dir).strip():
        return Path(str(data_dir)).expanduser()
    configured = os.environ.get("AGENT_DATA_DIR", "").strip()
    return Path(configured or "agent").expanduser()


def _get_state_path(data_dir: str | Path | None = None) -> Path:
    return _resolve_data_dir(data_dir) / "control" / "llm_runtime.json"


@dataclass
class LlmRuntimeState:
    """Persisted operator override for runtime LLM posture."""

    enabled: bool = True
    backend_override: str = ""
    provider_override: str = ""
    updated_at: str = ""
    updated_by: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "backend_override": self.backend_override,
            "provider_override": self.provider_override,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LlmRuntimeState:
        return cls(
            enabled=bool(data.get("enabled", True)),
            backend_override=_normalize_backend(str(data.get("backend_override", ""))),
            provider_override=_normalize_provider(str(data.get("provider_override", ""))),
            updated_at=str(data.get("updated_at", "")),
            updated_by=str(data.get("updated_by", "")),
            note=str(data.get("note", "")),
        )


def load_llm_runtime_state(data_dir: str | Path | None = None) -> LlmRuntimeState:
    """Load persisted runtime state; missing file means default attached mode."""
    path = _get_state_path(data_dir)
    if not path.exists():
        return LlmRuntimeState()
    try:
        data = orjson.loads(path.read_bytes())
    except Exception:
        return LlmRuntimeState()
    if not isinstance(data, dict):
        return LlmRuntimeState()
    return LlmRuntimeState.from_dict(data)


def resolve_llm_runtime_state(
    *,
    data_dir: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    state: LlmRuntimeState | None = None,
) -> dict[str, Any]:
    """Return effective runtime LLM selection after env + operator overrides."""
    env = dict(environ or os.environ)
    runtime_state = state or load_llm_runtime_state(data_dir)
    env_backend = _normalize_backend(env.get("LLM_BACKEND", "cli")) or "cli"
    env_provider = _normalize_provider(env.get("LLM_PROVIDER", "anthropic")) or "anthropic"

    effective_backend = runtime_state.backend_override or env_backend
    if effective_backend == "cli":
        effective_provider = "anthropic"
    else:
        effective_provider = runtime_state.provider_override or env_provider or "anthropic"

    follows_env = not runtime_state.backend_override and not runtime_state.provider_override
    return {
        "enabled": runtime_state.enabled,
        "env_backend": env_backend,
        "env_provider": env_provider,
        "backend_override": runtime_state.backend_override,
        "provider_override": runtime_state.provider_override,
        "effective_backend": effective_backend,
        "effective_provider": effective_provider,
        "follows_env": follows_env,
        "override_active": (not runtime_state.enabled) or (not follows_env),
        "updated_at": runtime_state.updated_at,
        "updated_by": runtime_state.updated_by,
        "note": runtime_state.note,
        "state_path": str(_get_state_path(data_dir)),
    }


class LlmRuntimeControlService:
    """Persist and describe operator runtime LLM controls."""

    def __init__(self, *, data_dir: str | Path = "agent", control_plane: Any = None) -> None:
        self._data_dir = _resolve_data_dir(data_dir)
        self._control_plane = control_plane

    def get_state(self) -> dict[str, Any]:
        return resolve_llm_runtime_state(data_dir=self._data_dir)

    def update_state(
        self,
        *,
        enabled: bool | None = None,
        backend: str | None = None,
        provider: str | None = None,
        follow_env: bool = False,
        note: str = "",
        updated_by: str = "operator",
    ) -> dict[str, Any]:
        state = load_llm_runtime_state(self._data_dir)

        if enabled is not None:
            state.enabled = enabled
        if follow_env:
            state.backend_override = ""
            state.provider_override = ""
        if backend is not None:
            normalized_backend = _normalize_backend(backend)
            if normalized_backend and normalized_backend not in _ALLOWED_BACKENDS:
                raise ValueError(
                    f"Unsupported LLM backend '{backend}'. Use cli, api, or empty string."
                )
            state.backend_override = normalized_backend
            if normalized_backend == "cli":
                state.provider_override = ""
        if provider is not None:
            normalized_provider = _normalize_provider(provider)
            if normalized_provider and normalized_provider not in _ALLOWED_PROVIDERS:
                raise ValueError(
                    "Unsupported LLM provider. Use anthropic, openai, local, or empty string."
                )
            state.provider_override = normalized_provider

        summary = resolve_llm_runtime_state(
            data_dir=self._data_dir,
            state=state,
        )
        if summary["effective_backend"] == "cli" and state.provider_override:
            raise ValueError(
                "Provider override is only supported when the effective backend is api."
            )

        state.updated_at = datetime.now(UTC).isoformat()
        state.updated_by = updated_by
        state.note = note
        summary = resolve_llm_runtime_state(
            data_dir=self._data_dir,
            state=state,
        )
        self._write_state(state)

        from agent.core.llm_provider import clear_provider_cache

        clear_provider_cache()
        self._record_trace(summary)
        return summary

    def _write_state(self, state: LlmRuntimeState) -> None:
        path = _get_state_path(self._data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(state.to_dict(), option=orjson.OPT_INDENT_2))

    def _record_trace(self, summary: dict[str, Any]) -> None:
        if self._control_plane is None:
            return
        try:
            from agent.control.models import TraceRecordKind

            self._control_plane.record_trace(
                trace_kind=TraceRecordKind.CONFIGURATION,
                title="LLM runtime control updated",
                detail=(
                    f"enabled={summary['enabled']}; "
                    f"backend={summary['effective_backend']}; "
                    f"provider={summary['effective_provider']}; "
                    f"follows_env={summary['follows_env']}"
                ),
                metadata=summary,
            )
        except Exception:
            return
