"""
Agent Life Space — Real Estate Telegram CRUD Commands

Handles /realestate list|add|remove|pause|resume|show subcommands.
All methods are async and return plain text strings suitable for Telegram.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from agent.realestate.models import SearchConfig
from agent.realestate.store import RealEstateStore

logger = structlog.get_logger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,50}$")

_USAGE = (
    "*Real-estate watcher*\n"
    "/realestate list — všetky vyhľadávania\n"
    "/realestate add <name> [key=value ...] — pridaj search\n"
    "/realestate remove <name> — zmaž search\n"
    "/realestate pause <name> — pozastav search\n"
    "/realestate resume <name> — obnov search\n"
    "/realestate show <name> — detail searche\n\n"
    "Príklad: `/realestate add byty2kk category_sub_cb=4 locality_region_id=10 price_max=8000000`"
)


def _validate_name(name: str) -> str | None:
    """Return error string if name is invalid, else None."""
    if not name:
        return "Chýba názov searche."
    if not _NAME_RE.match(name):
        return f"Neplatný názov `{name}`. Povolené: a-z, A-Z, 0-9, _ (max 50 znakov)."
    return None


def _parse_kv_args(tokens: list[str]) -> dict[str, Any]:
    """Parse ['key=value', ...] into a dict. Values are cast to int/float if possible."""
    params: dict[str, Any] = {}
    for token in tokens:
        if "=" not in token:
            continue
        k, _, v = token.partition("=")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        try:
            params[k] = int(v)
        except ValueError:
            try:
                params[k] = float(v)
            except ValueError:
                params[k] = v
    return params


class RealestateTelegramCommands:
    """Handles /realestate subcommands. All methods return Telegram-ready strings."""

    def __init__(self, store: RealEstateStore) -> None:
        self._store = store

    async def handle(self, subcommand: str, args: list[str]) -> str:
        """Dispatch subcommand to the appropriate method."""
        sub = subcommand.lower() if subcommand else ""

        if sub in {"list", ""}:
            return await self.cmd_list()
        if sub == "add":
            return await self.cmd_add(args)
        if sub == "remove":
            name = args[0] if args else ""
            return await self.cmd_remove(name)
        if sub == "pause":
            name = args[0] if args else ""
            return await self.cmd_pause(name)
        if sub == "resume":
            name = args[0] if args else ""
            return await self.cmd_resume(name)
        if sub == "show":
            name = args[0] if args else ""
            return await self.cmd_show(name)

        return _USAGE

    # ── Subcommand handlers ────────────────────────────────────────────────

    async def cmd_list(self) -> str:
        searches = await self._store.list_all()
        if not searches:
            return (
                "Žiadne real-estate vyhľadávania.\n\n"
                "Pridaj: `/realestate add <name> key=value ...`"
            )
        lines = [f"*Real-estate searches* ({len(searches)}):"]
        for s in searches:
            status = "✅ aktívny" if s.active else "⏸ pozastavený"
            lines.append(f"• `{s.name}` — {status}, min_score={s.min_score}")
        return "\n".join(lines)

    async def cmd_add(self, args: list[str]) -> str:
        if not args:
            return "Použi: `/realestate add <name> [key=value ...]`"

        name = args[0]
        err = _validate_name(name)
        if err:
            return err

        params = _parse_kv_args(args[1:])

        # Extract min_score from params if provided (not an API param)
        min_score = int(params.pop("min_score", 60))
        if not (0 <= min_score <= 100):
            return "min_score musí byť 0–100."

        config = SearchConfig(
            name=name,
            params_json=params,
            active=True,
            min_score=min_score,
        )

        try:
            await self._store.add_search(config)
        except Exception as exc:
            if "UNIQUE" in str(exc) or "unique" in str(exc).lower():
                return f"Search `{name}` už existuje. Použi `/realestate show {name}` pre detail."
            logger.exception("realestate.telegram_cmds.add_failed", name=name)
            return f"Chyba pri pridávaní searche: {exc}"

        param_summary = ", ".join(f"{k}={v}" for k, v in params.items()) or "bez parametrov"
        return (
            f"Search `{name}` pridaný.\n"
            f"Parametre: {param_summary}\n"
            f"min_score: {min_score}"
        )

    async def cmd_remove(self, name: str) -> str:
        err = _validate_name(name)
        if err:
            return err

        deleted = await self._store.remove_search(name)
        if deleted:
            return f"Search `{name}` bol zmazaný."
        return f"Search `{name}` neexistuje."

    async def cmd_pause(self, name: str) -> str:
        err = _validate_name(name)
        if err:
            return err

        updated = await self._store.pause_search(name)
        if updated:
            return f"Search `{name}` pozastavený. Obnov: `/realestate resume {name}`"
        return f"Search `{name}` neexistuje."

    async def cmd_resume(self, name: str) -> str:
        err = _validate_name(name)
        if err:
            return err

        updated = await self._store.resume_search(name)
        if updated:
            return f"Search `{name}` obnovený a aktívny."
        return f"Search `{name}` neexistuje."

    async def cmd_show(self, name: str) -> str:
        err = _validate_name(name)
        if err:
            return err

        config = await self._store.get_search(name)
        if config is None:
            return f"Search `{name}` neexistuje."

        status = "✅ aktívny" if config.active else "⏸ pozastavený"
        created = config.created_at.strftime("%Y-%m-%d %H:%M")
        param_lines = "\n".join(
            f"  {k}: {v}" for k, v in sorted(config.params_json.items())
        ) or "  (žiadne)"

        return (
            f"*Search: {config.name}*\n"
            f"Stav: {status}\n"
            f"min_score: {config.min_score}\n"
            f"Vytvorený: {created}\n"
            f"Parametre:\n{param_lines}"
        )
