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
- **Delivery packages** — shared build/review delivery lifecycle with deterministic bundle previews, approval linkage, and explicit handoff state
- **Reviewer handoff artifacts** — operator summaries and copy-paste-ready PR comment packs now persist as first-class review artifacts
- **Planner handoff + traces** — persisted `JobPlan` records and durable qualification/budget/capability/delivery traces
- **Delivery lifecycle tracking** — prepared → awaiting approval → approved/rejected → handed off with audit events
- **Workspace joins** — workspaces now link to jobs, artifacts, approvals, and delivery bundles
- **Retained artifact records** — build/review/delivery outputs now carry policy, expiry, recoverability, and prune-state metadata
- **Persisted product jobs** — shared control-plane record of build/review job metadata, status, usage, and artifacts
- **Per-job cost ledger** — durable usage/token/cost entries with report and CLI inspection
- **Runtime budget governance** — hard-cap, stop-loss, and approval-gated intake execution
- **Managed repo acquisition** — supported `git_url` intake can clone/import into a controlled local mirror before runtime routing
- **Evidence export** — `--export-evidence-job` assembles internal or client-safe review packages with artifacts, traces, retention, and traceability
- **Environment profiles** — explicit review/build/acquisition/export execution profiles exposed through the runtime model
- **Controlled-environment deployment** — local-owner, operator-controlled, and enterprise-hardened runtime posture now has explicit deployment guidance
- **Multi-step approvals** — risky intake and delivery paths can require more than one approval deterministically
- **Shared policy registry** — deterministic job persistence, artifact retention, delivery, review-gate, and gateway defaults
- **Structured denials** — shared machine-readable blocker payloads across policy, intake, delivery, and evidence export flows
- **Control-plane queries** — shared inspection across build, review, task, job-runner, agent-loop, artifact, plan, delivery, and workspace state
- **Runtime model** — explicit coexistence rules for product jobs, planning tasks, infrastructure jobs, and conversational queue items
- **Release readiness gate** — deterministic CLI/CI quality and gateway posture gate before release or handoff
- **Operator dashboard** — authenticated `/dashboard` surface for jobs, settlements, retention, audit, operator metrics, and one-click LLM runtime control
- **Settlement workflow** — persisted 402/top-up approval flow across API, dashboard, and Telegram with retry support
- **Setup doctor** — `python -m agent --setup-doctor` audits self-host identity, LLM, gateway, and operator posture before first run
- **Operator CLI surfaces** — `--report`, `--runtime-model`, `--llm-runtime-*`, `--export-evidence-job`, `--export-evidence-mode client_safe`, `--list-plans`, `--list-traces`, `--list-workspaces`, `--list-deliveries`, `--list-persisted-jobs`, `--list-retained-artifacts`, `--prune-expired-retained-artifacts`, `--list-cost-ledger`, unified `--intake-*`, and explicit delivery handoff
- **1668+ tests** — unit + integration + e2e + security + routing evals + adversarial, $0.00 token cost

## Quick Start

> **For first-time setup**, follow [`docs/SETUP_LOCAL.md`](docs/SETUP_LOCAL.md).
> It walks you through generating your own credentials, where to store
> them, and how to keep your personal data out of the repo. Nothing
> below references any specific operator's environment.

```bash
git clone https://github.com/B2JK-Industry/Agent_Life_Space.git
cd Agent_Life_Space
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install sentence-transformers
```

```bash
export AGENT_PROJECT_ROOT="$PWD"            # recommended for self-host + systemd
export AGENT_DATA_DIR="$PWD/.agent_runtime" # keeps runtime DBs/logs out of the source tree
export AGENT_PIDFILE_PATH="$PWD/.agent-life-space.pid"
export TELEGRAM_BOT_TOKEN="your_token"      # from @BotFather
export TELEGRAM_USER_ID="your_id"           # your Telegram user ID
export AGENT_NAME="MyAgent"                 # recommended
export AGENT_SERVER_NAME="my-server"        # recommended
export AGENT_VAULT_KEY="your_key"           # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export AGENT_API_KEY="your_api_key"         # python -c "import secrets; print(f'agent_api_{secrets.token_urlsafe(24)}')"

# choose one LLM backend:
# CLI backend (Claude Code installed and logged in on the same host)
export LLM_BACKEND="cli"
# export CLAUDE_CODE_OAUTH_TOKEN="..."

# or API backend
# export LLM_BACKEND="api"
# export LLM_PROVIDER="anthropic"
# export ANTHROPIC_API_KEY="sk-ant-..."

# optional: leave owner fields empty and let the first authorized Telegram message teach the owner profile
# export AGENT_OWNER_NAME="Your name"
# export AGENT_OWNER_FULL_NAME="Your full name"

.venv/bin/python -m agent --setup-doctor
.venv/bin/python -m agent
```

See **[Deployment guide](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Deployment)** for full setup (Docker, systemd, Cloudflare tunnel, firewall).
After startup, the dashboard is available at `/dashboard` on the API port and uses the same `AGENT_API_KEY`.

### Runtime LLM Control

You can detach the LLM entirely, or switch between CLI and API backends without editing `.env` each time. The runtime override is persisted under `AGENT_DATA_DIR/control/llm_runtime.json`.

```bash
.venv/bin/python -m agent --llm-runtime-status
.venv/bin/python -m agent --llm-runtime-disable --llm-runtime-note "maintenance"
.venv/bin/python -m agent --llm-runtime-enable --llm-runtime-backend cli --llm-runtime-note "back to Claude CLI"
.venv/bin/python -m agent --llm-runtime-enable --llm-runtime-backend api --llm-runtime-provider anthropic
.venv/bin/python -m agent --llm-runtime-follow-env --llm-runtime-enable
```

The same control surface is available via:
- `GET /api/operator/llm`
- `POST /api/operator/llm`
- `/dashboard` LLM Runtime panel

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
| `review/` | Structured review runtime, evidence export, reviewer handoff | Stable |
| `build/` | Build execution, delivery packages, bundle lifecycle | Stable |
| `control/` | Control-plane queries, runtime model, release readiness gate | Stable |
| `work/` | Isolated workspaces | Beta |

~39,000 lines of code. Details: **[Modules wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Modules)**

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
.venv/bin/python -m agent --setup-doctor
.venv/bin/python -m pytest tests/ -q   # 1668+ passed, offline, $0.00
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
| Finance | Partial | Approval + settlement workflows exist across Telegram, API, and dashboard, but this is still owner-operated rather than a richer multi-user finance console. |
| Multi-channel | Foundation | Telegram only in production. Discord/email are interfaces, not implemented. |
| Dashboard | Partial | API-key protected operator dashboard exists, but it is still a focused owner UI, not a broader multi-user app. |

See [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md) for security boundaries and [docs/LEARNING_MODEL.md](docs/LEARNING_MODEL.md) for learning system spec.

## License

MIT
