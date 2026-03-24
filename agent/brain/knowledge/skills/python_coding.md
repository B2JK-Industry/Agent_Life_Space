# Python kódovanie
_Kategória: skills | Tags: python, coding, venv | Aktualizované: 2026-03-24_

## Prostredie
- Python: ~/agent-life-space/.venv/bin/python
- Pip: ~/agent-life-space/.venv/bin/pip
- Venv je aktivovaný v service

## Čo viem
- Čítať a písať Python súbory
- Spúšťať skripty cez venv Python
- Inštalovať balíky cez pip (do venv)
- Pytest: `python -m pytest tests/ -q`
- Asyncio — agent beží async

## Štruktúra projektu
- `agent/core/` — jadro (router, agent, watchdog, job_runner)
- `agent/brain/` — rozhodovanie, skills, knowledge
- `agent/memory/` — SQLite pamäť
- `agent/tasks/` — task manager
- `agent/finance/` — rozpočet
- `agent/social/` — Telegram bot + handler
- `agent/logs/` — logging
- `agent/vault/` — secrets
- `tests/` — testy
