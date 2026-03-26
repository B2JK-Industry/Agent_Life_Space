# Changelog

All notable changes to Agent Life Space are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/):
- PATCH (1.0.x) — bug fixes, small opravy
- MINOR (1.x.0) — nové features, spätne kompatibilné
- MAJOR (x.0.0) — breaking changes (len so schválením)

## [Unreleased]

### Added
- **Memory provenance model** — epistemic status (observed/user_asserted/inferred/verified/stale) for all memory entries
- **Memory expiry** — entries can have expires_at, auto-mark stale
- **FTS5 retrieval** — full-text search for conversation memory (replaces LIKE)
- **Tool capability manifest** — risk_level, side_effect_class, owner_only, approval, audit_label per tool
- **Policy audit trail** — ring buffer of all policy decisions, queryable
- **Workspace persistence** — SQLite-backed workspace lifecycle, audit trail, recovery after restart
- **Centralized persona** — agent/core/persona.py, single source of truth for prompts
- **Explainable routing** — classify_task_detailed returns signal breakdown
- **Routing eval tests** — parametrized test suite for classification quality
- **CI architecture invariants** — automated checks for persona duplication, hardcoded paths, sandbox default

### Changed
- **Host file access blocked by default** — AGENT_SANDBOX_ONLY default changed from "0" to "1"
- **Routing scoring** — multi-signal with explicit weights instead of implicit thresholds
- **Tool policy** — capability-driven decisions instead of flat risk bucket
- **CI** — added mypy type check step, DeprecationWarning as error

### Security
- Host filesystem access via CLI now requires explicit AGENT_SANDBOX_ONLY=0
- All tool executions logged with audit_label for traceability

## [1.0.0] — 2026-03-26

First stable release. Všetko od 0.1-beta po predchádzajúce dev verzie zjednotené.

### Core
- **7-layer cascade** — 5 vrstiev lokálneho spracovania pred LLM (šetrí tokeny)
- **Provider-agnostic LLM** — ClaudeCliProvider, AnthropicProvider, OpenAiProvider
- **ModelTier system** — FAST/BALANCED/POWERFUL mapované per provider
- **AgentBrain** — channel-agnostic message processing, zero shared state
- **Tool use** — 10 nástrojov pre LLM function calling + ToolUseLoop (multi-turn)
- **SandboxExecutor** — Docker sandbox (256MB, no network, read-only FS)
- **Channel abstraction** — Channel ABC, IncomingMessage/OutgoingMessage, ChannelRegistry

### Memory & Knowledge
- **4-type memory** — episodic, semantic, procedural, working + consolidation + decay
- **Persistent conversation** — SQLite-backed, prežije reštart
- **RAG** — knowledge base retrieval
- **Semantic cache** — embedding-based response caching

### Communication
- **Telegram bot** — 15+ commands, typing indicators, group chat support
- **Agent-to-Agent API** — HTTP endpoint (port 8420) s API key autentifikáciou

### Finance & Security
- **Budget module** — propose → approve → complete workflow (human-in-the-loop)
- **Encrypted vault** — Fernet AES-128, PBKDF2 480K iterations
- **ETH + BTC wallets** — v šifrovanom vaulte, nikdy nezverejnené
- **Input sanitization** — prompt injection guard (EN + SK)
- **Owner identification** — safe mode pre non-owners v skupinách
- **50 automated security audit tests**

### Infrastructure
- **PID lockfile** — zabraňuje duplicitným inštanciám
- **Message queue persistence** — SQLite, prežije crash
- **Watchdog** — dead man switch + escalation protocol
- **GitHub Actions CI** — lint (ruff) + tests + security audit
- **705+ tests** — unit + integration + e2e + security, $0.00 token cost

### Security Fixes (included in 1.0.0)
- Race conditions fixed (AgentBrain, zero shared state)
- Safe mode check moved before command dispatch
- SQL injection fix in persistent_conversation
- Vault fail-fast without encryption key
- API binds to 127.0.0.1 by default
- Error messages sanitized (no internal paths leaked)

## Pre-release History

### [0.9.0] — 2026-03-20
- Persistent conversation system (MemGPT-style)

### [0.8.0] — 2026-03-15
- Watchdog dead man switch, response quality detector

### [0.7.0] — 2026-03-10
- Agent-to-Agent HTTP API, group chat support

### [0.6.0] — 2026-03-05
- Tool pre-routing, projects + workspace modules

### [0.5.0] — 2026-02-25
- Learning system v2, sandbox improvements

### [0.4.0] — 2026-02-18
- Wallet support (ETH + BTC), finance module, knowledge base

### [0.3.0] — 2026-02-10
- Semantic cache + RAG, semantic router, 5-layer cascade

### [0.2.0] — 2026-02-01
- Docker sandbox, web module, programmer brain

### [0.1-beta] — 2026-01-20
- Initial agent scaffold, 11 modules, 219 tests
