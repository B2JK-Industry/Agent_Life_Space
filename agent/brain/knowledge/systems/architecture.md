# Architektúra Agent Life Space
_Kategória: systems | Tags: architecture, modules, design | Aktualizované: 2026-03-24_

## Princíp
- Message-driven architektúra
- Moduly komunikujú cez message bus (router.py)
- Priority queue pre správy
- Dead letter queue pre neúspešné správy
- Semantic routing pre klasifikáciu správ
- Response quality detector (auto-eskalácia Haiku → Sonnet)

## Moduly
```
agent/
├── core/
│   ├── agent.py           — orchestrátor, spája všetko
│   ├── agent_loop.py      — hlavná slučka agenta
│   ├── messages.py        — JSON správy medzi modulmi
│   ├── models.py          — dátové modely
│   ├── router.py          — message bus, priority queue
│   ├── job_runner.py      — joby s timeoutom, circuit breaker
│   ├── watchdog.py        — heartbeat, zdravie, restart
│   ├── llm_router.py      — template prompty, JSON schema
│   ├── llm_client.py      — LLM klient
│   ├── cron.py            — cron úlohy
│   ├── maintenance.py     — server maintenance
│   ├── response_quality.py — quality detector, auto-eskalácia
│   ├── sandbox.py         — Docker sandbox pre cudzí kód
│   ├── utils.py           — pomocné utility
│   └── web.py             — web requesty
├── brain/
│   ├── decision_engine.py  — algo vs LLM rozhodovanie
│   ├── dispatcher.py       — dispečer správ
│   ├── knowledge.py        — knowledge base vyhľadávanie
│   ├── learning.py         — learning systém v2
│   ├── programmer.py       — programátorské schopnosti
│   ├── semantic_router.py  — klasifikácia správ
│   ├── skills.json         — registry schopností
│   ├── skills.py           — skills management
│   └── knowledge/          — knowledge base (.md súbory)
├── memory/
│   ├── store.py            — 4 typy pamäte, SQLite
│   ├── consolidation.py    — konsolidácia pamäte
│   ├── rag.py              — RAG retrieval
│   └── semantic_cache.py   — sémantický cache
├── tasks/
│   └── manager.py          — úlohy, dependencies, priority
├── finance/
│   └── tracker.py          — rozpočet, approval flow
├── social/
│   ├── telegram_bot.py     — Telegram interface
│   └── telegram_handler.py — spracovanie správ
├── logs/
│   └── logger.py           — JSON logy, secret redaction
├── vault/
│   └── secrets.py          — šifrované kľúče
├── docs/                   — dokumentácia a výskum
├── projects/               — projektový modul
└── work/                   — pracovný priestor
```

## Flow správy od Daniela
1. Telegram API → telegram_bot.py (polling)
2. → router.py (message bus)
3. → dispatcher.py → semantic_router.py (klasifikácia)
4. → knowledge.py (KB search) → decision_engine.py
5. → llm_router.py (Claude Opus 4.6, Haiku pre jednoduché)
6. → response_quality.py (auto-eskalácia ak treba)
7. → odpoveď späť cez Telegram
