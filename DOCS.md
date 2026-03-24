# Agent Life Space — Dokumentácia

Self-hosted autonómny agent "John" na Ubuntu serveri (Acer Aspire V, i7-5500U, 8GB RAM, NVIDIA 840M).

---

## Rýchly štart

```bash
source .venv/bin/activate
python -m agent              # Spusti agenta
python -m agent --status     # Stav
python -m agent --health     # Zdravie
python -m pytest tests/ -q   # Testy (329 testov)
```

Env premenné (systemd service):
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_USER_ID=...
CLAUDE_CODE_OAUTH_TOKEN=...
GITHUB_TOKEN=...
```

---

## Architektúra — 7-vrstvový cascade

Každá správa prechádza od najlacnejšej po najdrahšiu vrstvu:

```
Daniel (Telegram)
  │
  ▼
1. Slash commands (/status, /health, /tasks...)     → 0 tokenov
  │
  ▼
2. InternalDispatcher (regex patterny)              → 0 tokenov
  │
  ▼
3. Semantic Router (MiniLM embeddingy, SK+EN)       → 0 tokenov
  │
  ▼
4. Semantic Cache (podobná otázka = cached odpoveď) → 0 tokenov
  │
  ▼
5. Self-RAG (knowledge base cez embeddingy)         → 0 tokenov (direct)
  │                                                   alebo kontext pre LLM
  ▼
6. Haiku / Sonnet (jednoduché / konverzácia)        → ~$0.01-0.04
  │
  ▼
7. Opus (programovanie, code, git)                  → ~$0.15-0.50
```

---

## Moduly

### Core

| Modul | Súbor | Čo robí |
|-------|-------|---------|
| Orchestrátor | `agent/core/agent.py` | Spája všetky moduly, lifecycle |
| Message Protocol | `agent/core/messages.py` | JSON správy, immutabilné, TTL, priority |
| Message Router | `agent/core/router.py` | Priority queue, dead letters, retry |
| Model Router | `agent/core/models.py` | Haiku/Sonnet/Opus routing podľa typu úlohy |
| Job Runner | `agent/core/job_runner.py` | Timeout, exponential backoff, circuit breaker |
| Watchdog | `agent/core/watchdog.py` | Heartbeat, modul health (HEALTHY→DEAD), auto-restart |
| LLM Router | `agent/core/llm_router.py` | Template prompty, JSON schema validácia |
| Work Loop | `agent/core/agent_loop.py` | Background fronta úloh, Sonnet |
| Cron | `agent/core/cron.py` | Periodické joby: health (1h), memory (6h), report (8:00), maintenance (3h), consolidation (2h) |
| Maintenance | `agent/core/maintenance.py` | Disk, RAM, stale procesy, cache, sieť |
| Web Access | `agent/core/web.py` | HTTP fetch, JSON API, scraping s rate limitom (10/min) |
| Docker Sandbox | `agent/core/sandbox.py` | Izolované spúšťanie kódu (256MB RAM, no network, read-only) |

### Brain

| Modul | Súbor | Čo robí |
|-------|-------|---------|
| Decision Engine | `agent/brain/decision_engine.py` | Algo vs LLM routing, task scoring, finance pre-check |
| InternalDispatcher | `agent/brain/dispatcher.py` | Regex patterny pre stav/zdravie/úlohy — 0 tokenov |
| Semantic Router | `agent/brain/semantic_router.py` | MiniLM embeddingy, intent detection, SK+EN |
| Skills Registry | `agent/brain/skills.py` | 20 skills, lifecycle UNKNOWN→MASTERED, auto-testing |
| Knowledge Base | `agent/brain/knowledge.py` | .md súbory v kategóriách (skills, systems, people, projects, learned) |
| Learning System | `agent/brain/learning.py` | Prepája skills + knowledge + memory, try_skill() |
| Programmer | `agent/brain/programmer.py` | Code review, error analýza, programming workflow |

### Memory

| Modul | Súbor | Čo robí |
|-------|-------|---------|
| Memory Store | `agent/memory/store.py` | 4-vrstvová pamäť (working, episodic, semantic, procedural), SQLite |
| Consolidation | `agent/memory/consolidation.py` | Episodic → semantic/procedural, dedup, frequency analysis |
| Semantic Cache | `agent/memory/semantic_cache.py` | Cache LLM odpovedí (cosine > 0.90), TTL 1h |
| Self-RAG | `agent/memory/rag.py` | Embedding index nad knowledge base, HIGH/MEDIUM/LOW routing |

### Social

| Modul | Súbor | Čo robí |
|-------|-------|---------|
| Telegram Bot | `agent/social/telegram_bot.py` | Long polling, groups, whitelist, Markdown fallback |
| Telegram Handler | `agent/social/telegram_handler.py` | Správy → cascade → odpoveď, usage tracking |

### Other

| Modul | Súbor | Čo robí |
|-------|-------|---------|
| Tasks | `agent/tasks/manager.py` | CREATED→QUEUED→RUNNING→COMPLETED, dependencies, priority |
| Finance | `agent/finance/tracker.py` | Propose→Approve→Complete, budget limits, audit |
| Logger | `agent/logs/logger.py` | JSON logy, secret redaction, rotation |
| Vault | `agent/vault/secrets.py` | Fernet/AES šifrovanie, PBKDF2, audit trail |

---

## Telegram príkazy

| Príkaz | Čo robí | LLM? |
|--------|---------|------|
| `/status` | Stav agenta | Nie |
| `/health` | CPU, RAM, disk, moduly | Nie |
| `/tasks` | Zoznam úloh | Nie |
| `/memory [keyword]` | Hľadanie v pamäti | Nie |
| `/budget` | Finančný stav | Nie |
| `/newtask [názov]` | Vytvorenie úlohy | Nie |
| `/consolidate` | Konsolidácia pamäte | Nie |
| `/web [url]` | Stiahnutie a čítanie stránky | Nie |
| `/sandbox [python]` | Spustenie kódu v Docker sandboxe | Nie |
| `/review [file]` | Code review | Nie |
| `/usage` | Spotreba tokenov | Nie |
| `/queue` | Stav pracovnej fronty | Nie |
| `/help` | Zoznam príkazov | Nie |
| Voľný text | Konverzácia cez cascade | Áno |

---

## Model routing

| Typ úlohy | Model | Max turns | Timeout |
|-----------|-------|-----------|---------|
| Pozdrav (ahoj, ok, ďakujem) | Haiku | 1 | 60s |
| Krátka faktická otázka | Haiku | 1 | 60s |
| Konverzácia | Sonnet | 3 | 180s |
| Analýza, research | Sonnet | 3 | 180s |
| Work queue úlohy | Sonnet | 3 | 180s |
| Programovanie | Opus | 15 | 300s |

Konfigurácia: `agent/core/models.py`

---

## Learning systém

**Skills** (`agent/brain/skills.json`):
```
UNKNOWN → TESTING → LEARNED → MASTERED (5+ úspechov)
                  → FAILED (retry neskôr)
```

**Knowledge Base** (`agent/brain/knowledge/`):
- `skills/` — návody k schopnostiam
- `systems/` — ako fungujú veci (server, GitHub, Telegram)
- `people/` — Daniel Babjak, kontakty
- `projects/` — aktívne projekty (hackathony)
- `learned/` — čo sa naučil z experimentov

**Auto-update**: Skills sa automaticky aktualizujú z Claude odpovedí (success/failure detection).

---

## Memory systém

4 typy pamäte v SQLite:

| Typ | Účel | Decay |
|-----|------|-------|
| Working | Aktuálny kontext | Rýchly (5×) |
| Episodic | Čo sa stalo | Normálny |
| Semantic | Fakty, vzory | Normálny |
| Procedural | Postupy, recepty | Normálny |

**Consolidation** (každé 2h): episodic → semantic + procedural

**Semantic Cache**: Ak John už odpovedal na podobnú otázku (cosine > 0.90), vráti cache.

**Self-RAG**: Pred LLM hľadá v knowledge base cez embeddingy. HIGH match → priama odpoveď.

---

## Bezpečnosť

1. Financie — Agent navrhuje, Daniel schvaľuje. Vždy.
2. Secrets — Šifrované na disku (Fernet/AES), redaktované v logoch.
3. Žiadne wallet prístupy, žiadne smart contracty.
4. Sandbox — Docker kontajnery: 256MB RAM, no network, read-only, timeout.
5. Timeout na všetkom — žiadny proces beží nekonečne.
6. Dead letter queue — žiadne tiché zlyhanie.
7. Rate limiting — web access max 10 req/min.
8. Anti-stochastika — deterministický algoritmus kde nie je nutný LLM.

---

## Testy

329 testov v 16 súboroch:

| Súbor | Testov | Čo testuje |
|-------|--------|------------|
| test_messages.py | 23 | Message protocol, immutabilita, TTL |
| test_router.py | 12 | Routing, dead letters, priority |
| test_job_runner.py | 12 | Timeout, retry, circuit breaker |
| test_watchdog.py | 14 | Health states, restart, alerts |
| test_memory.py | 19 | Store, query, decay, persistence |
| test_brain.py | 25 | Scoring, classification, cache |
| test_finance.py | 16 | Approval flow, budget |
| test_tasks.py | 16 | Lifecycle, dependencies |
| test_llm_router.py | 19 | Templates, JSON validation |
| test_vault.py | 16 | Encryption, audit |
| test_logger.py | 13 | Redaction, rotation |
| test_integration.py | 14 | Cross-module flows |
| test_consolidation.py | 13 | Pattern extraction, dedup |
| test_learning.py | 23 | Skills, KB, auto-testing |
| test_brain_memory.py | 24 | Brain+memory integration, E2E |
| test_programmer.py | 23 | Code review, error analysis |
| test_models.py | 17 | Task classification, model routing |
| test_semantic_router.py | 7 | Intent definitions, classification |
| test_telegram_review.py | 8 | /review command |
| test_utils.py | 2 | Slovak time formatting |

```bash
python -m pytest tests/ -q           # Rýchly beh
python -m pytest tests/ -v           # Detailný
python -m pytest tests/test_X.py     # Jeden súbor
```

---

## Štrukúra súborov

```
Agent_Life_Space/
├── CLAUDE.md                  # Pravidlá pre agenta
├── JOHN.md                    # Identita agenta
├── DOCS.md                    # Táto dokumentácia
├── pyproject.toml             # Python konfigurácia
├── agent/
│   ├── __main__.py            # Entry point, Telegram, cron
│   ├── core/
│   │   ├── agent.py           # Orchestrátor
│   │   ├── agent_loop.py      # Background work queue
│   │   ├── cron.py            # Periodické joby
│   │   ├── job_runner.py      # Job execution
│   │   ├── llm_router.py      # LLM templates + schema
│   │   ├── maintenance.py     # Server maintenance
│   │   ├── messages.py        # JSON message protocol
│   │   ├── models.py          # Model routing (Haiku/Sonnet/Opus)
│   │   ├── router.py          # Message bus
│   │   ├── sandbox.py         # Docker sandbox
│   │   ├── utils.py           # Utility funkcie
│   │   ├── watchdog.py        # Health monitoring
│   │   └── web.py             # HTTP/scraping
│   ├── brain/
│   │   ├── decision_engine.py # Algo vs LLM
│   │   ├── dispatcher.py      # Internal dispatch (0 tokenov)
│   │   ├── knowledge.py       # Knowledge base (.md)
│   │   ├── learning.py        # Skills + KB + memory
│   │   ├── programmer.py      # Code review, workflow
│   │   ├── semantic_router.py # MiniLM intent detection
│   │   └── skills.py          # Skill registry
│   ├── memory/
│   │   ├── consolidation.py   # Episodic → semantic/procedural
│   │   ├── rag.py             # Self-RAG embedding search
│   │   ├── semantic_cache.py  # LLM response cache
│   │   └── store.py           # 4-type SQLite memory
│   ├── tasks/
│   │   └── manager.py         # Task lifecycle
│   ├── finance/
│   │   └── tracker.py         # Budget + approval
│   ├── social/
│   │   ├── telegram_bot.py    # Telegram polling
│   │   └── telegram_handler.py # Message handling + cascade
│   ├── logs/
│   │   └── logger.py          # JSON logging
│   └── vault/
│       └── secrets.py         # Encrypted secrets
└── tests/                     # 329 testov
```

---

## Čo je dokončené

- [x] 7-vrstvový cascade (0 tokenov → Haiku → Sonnet → Opus)
- [x] Semantic router s MiniLM (slovenčina + angličtina)
- [x] Semantic cache pre LLM odpovede
- [x] Self-RAG nad knowledge base
- [x] 20 skills s auto-testing
- [x] 15+ knowledge base entries
- [x] Memory consolidation (episodic → semantic/procedural)
- [x] Docker sandbox
- [x] Web scraping + API access
- [x] Telegram komunikácia (bot + groups)
- [x] Code review + programmer workflow
- [x] Server maintenance (cron)
- [x] 329 testov

## Čo je plánované

- [ ] Plné využitie NVIDIA 840M GPU (OCR, image processing)
- [ ] RAG s FTS5 full-text search
- [ ] Proaktivita (vlastná iniciatíva, nie len reakcia)
- [ ] Data collection pipeline (RSS, monitoring)
- [ ] Earning: hackathony, freelance
- [ ] Plugin systém pre nové schopnosti
