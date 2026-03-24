# Agent Life Space

Self-hosted autonomous AI agent that lives on your server.

7-layer cascade minimizes API calls. Learns from outcomes. Runs code in Docker sandbox. Encrypted vault for secrets.

## Quick Start

```bash
git clone https://github.com/B2JK-Industry/Agent_Life_Space.git
cd Agent_Life_Space
cp .env.example .env  # edit with your tokens
docker compose up -d
```

Or manual install:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install sentence-transformers
python scripts/setup_vault.py
export TELEGRAM_BOT_TOKEN="..." TELEGRAM_USER_ID="..." CLAUDE_CODE_OAUTH_TOKEN="..."
python -m agent
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 7-LAYER CASCADE                      │
│                                                      │
│  1. /commands      → direct response (0 API calls)  │
│  2. Dispatcher     → regex patterns (0 API calls)   │
│  3. Semantic Router→ MiniLM local (~470MB RAM)       │
│  4. Semantic Cache → similar question? (local)       │
│  5. Self-RAG       → knowledge base (local embed.)   │
│  6. Haiku/Sonnet   → simple/conversation (API)      │
│  7. Opus           → programming (API)               │
└─────────────────────────────────────────────────────┘
```

## Features

- **Cascade routing** — 5 layers before LLM, minimizes API calls
- **Learning system** — model escalation on failure, prompt augmentation from past errors
- **Docker sandbox** — mandatory, isolated code execution (256MB, no network, read-only FS)
- **Encrypted vault** — Fernet AES-128 + PBKDF2, wallet support (ETH/BTC)
- **Memory** — 4 types (episodic, semantic, procedural, working), consolidation, decay
- **20 skills** — auto-testing, UNKNOWN → LEARNED → MASTERED lifecycle
- **Watchdog** — heartbeats, auto-restart, CPU/RAM alerts, circuit breaker
- **Finance** — human-in-the-loop approval for all transactions
- **430+ tests** — unit, integration, E2E scenarios

## Requirements

- 4-core CPU, 8GB RAM, 100GB disk
- Python 3.12+, Docker, Git
- Telegram Bot + Claude Max subscription (or Anthropic API key)

## Docs

- [BLUEPRINT.md](BLUEPRINT.md) — full architecture, cascade details, setup guide
- [VERIFICATION.md](VERIFICATION.md) — 8 E2E deployment scenarios, verification checklist

## License

MIT
