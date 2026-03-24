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
- `agent/core/` — jadro (router, agent, agent_loop, watchdog, job_runner, llm_router, llm_client, cron, maintenance, sandbox, response_quality, web)
- `agent/brain/` — rozhodovanie, dispatcher, semantic_router, skills, knowledge, learning, programmer
- `agent/memory/` — SQLite pamäť, konsolidácia, RAG, sémantický cache
- `agent/tasks/` — task manager
- `agent/finance/` — rozpočet
- `agent/social/` — Telegram bot + handler
- `agent/logs/` — logging
- `agent/vault/` — secrets
- `agent/docs/` — dokumentácia a výskum
- `agent/projects/` — projektový modul
- `agent/work/` — pracovný priestor
- `tests/` — testy
