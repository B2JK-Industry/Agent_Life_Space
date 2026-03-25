# Agent Life Space v1.0

**Self-hosted autonomous AI agent that lives on your server.** Agent Life Space is a modular Python framework for running a personal AI agent with its own memory, skills, and learning system. It communicates via Telegram, runs code in a Docker sandbox, minimizes API costs through a 7-layer local-first cascade, and manages sensitive data in an encrypted vault. Built for a single owner who wants full control over their AI infrastructure.

## Key Features

- **7-layer cascade routing** — regex dispatcher, semantic router (MiniLM), semantic cache, Self-RAG knowledge retrieval, and model escalation (Haiku -> Sonnet -> Opus) — most queries never hit the LLM
- **Docker sandbox** — all code execution runs in isolated containers (256MB RAM, no network, read-only filesystem, image whitelist)
- **Encrypted vault** — Fernet AES-128 + PBKDF2 for API keys and wallet private keys (ETH/BTC)
- **4-type memory system** — episodic, semantic, procedural, working memory with consolidation and decay
- **Persistent conversation** — SQLite-backed context that survives restarts (core memory + rolling summary + retrieval)
- **Learning system** — tracks skill outcomes, auto-escalates models, augments prompts from experience
- **Watchdog + dead man switch** — heartbeats, auto-restart, circuit breaker, escalation protocol
- **Agent-to-Agent API** — authenticated HTTP endpoint for communication with other agents/bots
- **Anti-confabulation** — runtime facts injected into prompts when the agent is asked about its own state
- **472+ tests** across all modules

## Security Improvements in v1.0

This release includes fixes from an external security audit addressing all CRITICAL and HIGH severity findings:

- **Prompt injection blocking** — malicious input is now blocked outright (previously only logged a warning)
- **Semantic cache poisoning fix** — inputs are validated before being written to cache
- **Sandbox hardening** — Docker image whitelist enforced, all shell arguments escaped with `shlex.quote`
- **API bind address** — changed from `0.0.0.0` to `127.0.0.1` by default
- **Work queue permission gating** — only the verified owner can queue tasks
- **Group chat safe mode** — restricted command set when running in non-private chats
- **Error sanitization** — internal paths, stack traces, and module names are no longer leaked in error messages

## Breaking Changes

| Change | Migration |
|--------|-----------|
| `AGENT_OWNER_NAME` env var is now **required** | Add `Environment=AGENT_OWNER_NAME=YourName` to your systemd service file |
| Agent API requires `AGENT_API_KEY` | Generate a key: `python -c "import secrets; print(f'agent_api_{secrets.token_urlsafe(24)}')"` and add it to env |
| API binds to `127.0.0.1` by default | If you need external access, use a reverse proxy or Cloudflare tunnel (do not change bind to `0.0.0.0`) |

## Setup

See the [README](../README.md) for complete step-by-step setup instructions (10 steps from zero to running agent).

**Quick start:**
```bash
git clone https://github.com/B2JK-Industry/Agent_Life_Space.git
cd Agent_Life_Space
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install sentence-transformers
python scripts/setup_vault.py
```

## Known Limitations

- **CLI token overhead**: ~16k tokens minimum per call due to injected CLI context
- **No direct API mode**: Currently requires Claude Max subscription (CLI-based execution)
- **Single-user Telegram**: Bot API does not relay messages between bots natively
- **MiniLM on Slovak**: Semantic router accuracy is lower for Slovak-language queries
- **Tunnel instability**: Cloudflare quick tunnels change URL on restart

## What's Next

- **API migration** — move from CLI-based Claude invocation to direct Anthropic API calls, eliminating the ~16k token overhead per request
- **Moltbook integration** — connect to the social network for agents, enabling discovery and collaboration between Agent Life Space instances
- **Multi-model support** — add support for alternative LLM backends beyond Claude
- **Improved Slovak NLP** — fine-tuned semantic routing for Slovak language

---

Full changelog: [CHANGELOG.md](../CHANGELOG.md)
