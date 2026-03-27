# Agent Life Space

Self-hosted autonomous AI agent that lives on your server. Thinks with Claude, acts through its own modules, communicates via Telegram.

**[Wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki)** | **[Architecture](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Architecture)** | **[Security](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Security)** | **[API Reference](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/API-Reference)** | **[Roadmap](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Roadmap)**

## What it does

- **9-layer cascade** — dispatch → cache → RAG → classify → LLM → quality escalation → learning → channel filter → explanation
- **Docker sandbox** — `/sandbox` code runs in isolated containers (256MB, no network, read-only FS)
- **Encrypted vault** — API keys, wallet keys (ETH/BTC) encrypted with Fernet AES-128
- **Epistemic memory** — 4 types + provenance model (observed/asserted/inferred/verified/stale), expiry, decay
- **Persistent conversation** — SQLite-backed context with FTS5 full-text search, survives restarts
- **Agent-to-Agent API** — HTTP endpoint for inter-agent communication
- **Structured review API** — `POST /api/review` runs deterministic review jobs through the shared runtime
- **Learning system** — skill outcome tracking, model escalation, prompt augmentation
- **Multi-provider LLM** — Claude CLI, Anthropic API, OpenAI, Ollama (any backend)
- **Automated security** — 127-test security audit + invariant suite
- **Tool governance** — capability manifest, policy engine, 4-step action pipeline with audit trail
- **Workspace persistence** — SQLite-backed workspaces with audit trail, limits, TTL, recovery
- **Approval queue** — structured propose → approve/deny → execute workflow with persistent storage and linkage
- **Builder delivery packages** — deterministic patch/diff export, acceptance bundle preview, and approval-gated build handoff
- **Planner handoff + traces** — persisted `JobPlan` records and durable qualification/budget/capability/delivery traces
- **Delivery lifecycle tracking** — prepared → awaiting approval → approved/rejected → handed off with audit events
- **Workspace joins** — workspaces now link to jobs, artifacts, approvals, and delivery bundles
- **Retained artifact records** — build/review/delivery outputs now carry policy, expiry, and recoverability metadata
- **Persisted product jobs** — shared control-plane record of build/review job metadata, status, usage, and artifacts
- **Per-job cost ledger** — durable usage/token/cost entries with report and CLI inspection
- **Runtime budget governance** — hard-cap, stop-loss, and approval-gated intake execution
- **Shared policy registry** — deterministic job persistence, artifact retention, delivery, review-gate, and gateway defaults
- **Control-plane queries** — shared inspection across build, review, task, job-runner, agent-loop, artifact, plan, delivery, and workspace state
- **Runtime model** — explicit coexistence rules for product jobs, planning tasks, infrastructure jobs, and conversational queue items
- **Operator CLI surfaces** — `--report`, `--runtime-model`, `--list-plans`, `--list-traces`, `--list-workspaces`, `--list-deliveries`, `--list-persisted-jobs`, `--list-retained-artifacts`, `--list-cost-ledger`, unified `--intake-*`, and explicit build delivery handoff
- **1273+ tests** — unit + integration + e2e + security + routing evals + adversarial, $0.00 token cost

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
2. /commands -> direct response (0 API calls)
3. Dispatcher -> pattern match (0 API calls)
4. Semantic router -> embedding classification (local compute)
5. Semantic cache -> cached response (local compute)
6. RAG -> knowledge base lookup (local compute)
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

~17,300 lines of code. Details: **[Modules wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Modules)**

## Security

- Input sanitization (prompt injection guard, EN + SK)
- Owner identification + safe mode for non-owners in groups
- Tool governance — capability manifest + deterministic policy engine
- Host file access blocked by default (AGENT_SANDBOX_ONLY=1)
- Docker sandbox (read-only, no-network, resource limits, image whitelist)
- Encrypted vault (fail-fast without key)
- Approval queue for risk-sensitive actions (finance, host access, external writes)
- API authentication (Bearer token) + rate limiting
- Log redaction (secrets never in logs)
- PID lockfile (prevents duplicate instances)
- 127 automated security + invariant tests

Details: **[Security wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Security)**

## Testing

```bash
.venv/bin/python -m pytest tests/ -q   # 1273+ passed, ~22s, $0.00
```

| Layer | Tests | What |
|-------|-------|------|
| Unit | ~580 | Individual modules |
| Integration | 34 | Cross-module flows |
| E2E | 44 | Full agent wiring |
| Security | 116 | Audit + invariants |
| Routing Evals | 40+ | Classification accuracy + adversarial |
| Governance | 30+ | Policy enforcement + action pipeline |

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

## Known Limitations

This project is honest about what works and what doesn't yet.

| Area | Status | What's missing |
|------|--------|---------------|
| Memory provenance | Working | Conflict detection is tag-based, not semantic. No auto-consolidation pipeline yet. |
| Tool governance | Working | Review repo/diff execution and intake budgets now run under deterministic policy boundaries, but build execution is still not governed by one fully unified engine. |
| Workspace | Working | No cleanup scheduler (must call `cleanup_expired()` manually). |
| Routing | Working | Keyword + signal heuristics. No ML-based classification. |
| Learning | Partial | Model failure tracking resets on restart. No eval set. |
| Finance | Foundation | Propose/approve flow exists and approvals are queryable, but no live operator UI. |
| Multi-channel | Foundation | Telegram only in production. Discord/email are interfaces, not implemented. |
| Dashboard | Not started | No operator UI. Everything via Telegram or CLI. |

See [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md) for security boundaries and [docs/LEARNING_MODEL.md](docs/LEARNING_MODEL.md) for learning system spec.

## License

MIT
