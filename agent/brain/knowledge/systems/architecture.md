# Architektúra Agent Life Space
_Kategória: systems | Tags: architecture, modules, design | Aktualizované: 2026-03-24_

## Princíp
- Message-driven architektúra
- Moduly komunikujú cez message bus (router.py)
- Priority queue pre správy
- Dead letter queue pre neúspešné správy

## Moduly
```
agent/
├── core/
│   ├── agent.py          — orchestrátor, spája všetko
│   ├── messages.py       — JSON správy medzi modulmi
│   ├── router.py         — message bus, priority queue
│   ├── job_runner.py     — joby s timeoutom, circuit breaker
│   ├── watchdog.py       — heartbeat, zdravie, restart
│   └── llm_router.py     — template prompty, JSON schema
├── brain/
│   ├── decision_engine.py — algo vs LLM rozhodovanie
│   ├── skills.json        — registry schopností
│   └── knowledge/         — knowledge base (toto)
├── memory/
│   └── store.py           — 4 typy pamäte, SQLite
├── tasks/
│   └── manager.py         — úlohy, dependencies, priority
├── finance/
│   └── tracker.py         — rozpočet, approval flow
├── social/
│   ├── telegram_bot.py    — Telegram interface
│   └── telegram_handler.py — spracovanie správ
├── logs/
│   └── logger.py          — JSON logy, secret redaction
└── vault/
    └── secrets.py         — šifrované kľúče
```

## Flow správy od Daniela
1. Telegram API → telegram_bot.py (polling)
2. → router.py (message bus)
3. → telegram_handler.py (spracovanie)
4. → decision_engine.py (algo alebo LLM?)
5. → llm_router.py (Claude Opus 4.6)
6. → odpoveď späť cez Telegram
