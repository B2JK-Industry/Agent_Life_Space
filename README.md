# Agent Life Space

Self-hosted autonomous AI agent that lives on your server. Thinks with Claude, acts through its own modules, communicates via Telegram.

**[Wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki)** | **[Architecture](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Architecture)** | **[Security](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Security)** | **[API Reference](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/API-Reference)** | **[Roadmap](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Roadmap)**

## What it does

- **7-layer cascade** — 5 layers of local processing before calling LLM (saves tokens)
- **Docker sandbox** — code runs in isolated containers (256MB, no network, read-only FS)
- **Encrypted vault** — API keys, wallet keys (ETH/BTC) encrypted with Fernet AES-128
- **Memory** — 4 types (episodic, semantic, procedural, working), consolidation, decay
- **Persistent conversation** — SQLite-backed context, survives restarts
- **Agent-to-Agent API** — HTTP endpoint for inter-agent communication
- **Learning system** — skill outcome tracking, model escalation, prompt augmentation
- **Automated security** — 50-test security audit suite replaces manual reviews
- **652+ tests** — unit + integration + e2e + security audit, $0.00 token cost

## Quick Start

```bash
git clone https://github.com/B2JK-Industry/Agent_Life_Space.git
cd Agent_Life_Space
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install sentence-transformers
```

```bash
export TELEGRAM_BOT_TOKEN="your_token"      # from @BotFather
export TELEGRAM_USER_ID="your_id"           # your Telegram user ID
export AGENT_VAULT_KEY="your_key"           # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export AGENT_API_KEY="your_api_key"         # python -c "import secrets; print(f'agent_api_{secrets.token_urlsafe(24)}')"

.venv/bin/python -m agent
```

See **[Deployment guide](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Deployment)** for full setup (Docker, systemd, Cloudflare tunnel, firewall).

## Architecture

```
Telegram message
    |
1. Input sanitization (prompt injection guard)
2. /commands -> direct response (0 tokens)
3. Dispatcher -> pattern match (0 tokens)
4. Semantic router -> embedding classification (0 tokens)
5. Semantic cache -> cached response (0 tokens)
6. RAG -> knowledge base lookup (0 tokens)
7. Claude (Haiku $0.001 | Sonnet $0.01 | Opus $0.05-0.20)
    |
Response -> Telegram + memory + learning
```

## Modules

| Module | What | Status |
|--------|------|--------|
| `core/` | Orchestrator, router, watchdog, job runner, sandbox | Stable |
| `brain/` | Decision engine, dispatcher, semantic router, skills, learning | Stable |
| `memory/` | 4-type store, persistent conversation, RAG, consolidation | Stable |
| `social/` | Telegram bot, handler, Agent-to-Agent API | Stable |
| `finance/` | Budget, proposals (human-in-the-loop), audit trail | Stable |
| `vault/` | Encrypted secrets (Fernet AES-128, PBKDF2 480K iterations) | Stable |
| `tasks/` | Task lifecycle (create -> start -> complete) | Stable |
| `projects/` | Project scoping | Beta |
| `work/` | Isolated workspaces | Beta |

~12,300 lines of code. Details: **[Modules wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Modules)**

## Security

- Input sanitization (prompt injection guard, EN + SK)
- Owner identification + safe mode for non-owners in groups
- Docker sandbox (read-only, no-network, resource limits, image whitelist)
- Encrypted vault (fail-fast without key)
- API authentication (Bearer token) + rate limiting
- Log redaction (secrets never in logs)
- PID lockfile (prevents duplicate instances)
- 50 automated security audit tests

Details: **[Security wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Security)**

## Testing

```bash
.venv/bin/python -m pytest tests/ -q   # 652 passed, ~19s, $0.00
```

| Layer | Tests | What |
|-------|-------|------|
| Unit | ~524 | Individual modules |
| Integration | 34 | Cross-module flows |
| E2E | 44 | Full agent wiring |
| Security Audit | 50 | Automated security review |

All tests are offline — no API calls, no Docker needed. Details: **[Testing wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Testing)**

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Agent status |
| `/health` | CPU, RAM, disk, modules |
| `/tasks` | Task list |
| `/memory [keyword]` | Search memory |
| `/budget` | Financial status |
| `/newtask [name]` | Create task |
| `/web [url]` | Fetch webpage |
| `/sandbox [code]` | Run Python in Docker |
| `/review [file]` | Code review |
| `/wallet` | ETH/BTC status |
| `/usage` | Token costs |
| `/help` | All commands |

## Agent-to-Agent API

```bash
curl -X POST https://your-tunnel.trycloudflare.com/api/message \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_api_key" \
  -d '{"message": "hello", "sender": "other-agent"}'
```

Details: **[API Reference wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/API-Reference)**

## License

MIT
