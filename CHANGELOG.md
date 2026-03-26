# Changelog

All notable changes to Agent Life Space are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/):
- PATCH (1.0.x) — bug fixes, small opravy
- MINOR (1.x.0) — nové features, spätne kompatibilné
- MAJOR (x.0.0) — breaking changes (len so schválením)

## [1.3.0] — 2026-03-26

Completeness release. Remaining backlog items implemented.

### Memory
- **Factual/conversational separation** — query_facts() vs query_conversations(), kind= filter
- **Memory consolidation pipeline** — inferred → verified promotion, stale auto-detection

### Learning
- **Rollback** — reset skill to UNKNOWN, clear model failures
- **Learning report** — avg confidence, mastered/failed counts

### Workspace
- **Ownership** — owner_id field on workspaces
- **Immutable audit trail** — hash-chained entries (tamper-evident)

### Finance
- **Risk templates** — 6 pre-defined expense categories with validation
- **Audit trail export** — CSV format for external auditing

### CI
- **Expanded mypy** — all new modules covered
- **Performance budget** — 60s timeout, 1000+ test count gate

## [1.2.0] — 2026-03-26

Operator-grade visibility and control release. 5 PR, 1000+ tests.

### API & Communication
- **API audit trail** — every request logged (sender, IP, status, duration)
- **Replay protection wired** — nonce + timestamp check in API handler
- **Rate-limit telemetry** — total requests/errors/rate-limited/auth-failures by sender

### Finance
- **Budget policy** — hard cap (block), soft cap (warn), single-tx approval cap
- **Budget forecast** — remaining at each cap level

### Operator Visibility
- **Memory inspection API** — overview, provenance filter, stale report, conflict report
- **Agent status wiring** — brain.py transitions IDLE → THINKING → IDLE per message
- **get_agent_status()** — state + history + usage in one call
- **Operator handbook** — practical guide: daily ops, security, troubleshooting

## [1.1.0] — 2026-03-26

Breakthrough architecture release. 19 PR, 974+ tests, ~8000 lines added.

### Epistemic Memory
- **Provenance model** — observed/user_asserted/inferred/verified/stale status per memory
- **MemoryKind** — fact/belief/claim/procedure distinction
- **Memory expiry** — entries with expires_at, auto-mark stale
- **FTS5 retrieval** — full-text search replaces LIKE in conversation memory
- **Conflict detection** — finds contradicting memories about same topic
- **Audit report** — epistemic health of knowledge base
- **Consolidation pipeline** — inferred → verified promotion, stale detection

### Tool Governance
- **Capability manifest** — risk_level, side_effect_class, owner_only, approval, audit_label per tool
- **ActionEnvelope** — 4-step pipeline: request → policy → execute → result
- **Structured denial codes** — SAFE_MODE, OWNER_ONLY, UNKNOWN_TOOL, APPROVAL_REQUIRED
- **Policy simulation** — simulate() shows what WOULD happen without logging
- **Policy audit trail** — ring buffer with denial codes

### Approval & Controls
- **Approval queue** — propose → approve/deny → execute for risk-sensitive actions
- **Finance integration** — propose_expense() auto-creates approval request
- **Operator controls** — runtime disable/enable tools, lockdown/unlock
- **Agent status model** — IDLE/THINKING/EXECUTING/WAITING_APPROVAL/BLOCKED/DEGRADED

### Security
- **Host access blocked by default** — AGENT_SANDBOX_ONLY=1 is default
- **Security model document** — docs/SECURITY_MODEL.md as code artefact
- **Security invariant tests** — CI-enforced safety checks
- **Red-team test suite** — privilege escalation, rapid-fire, channel context
- **Channel policy** — per-channel trust levels, response classification (SAFE/PRIVATE/INTERNAL)
- **Replay protection** — nonce tracking, timestamp freshness, HMAC signing for API

### Intelligence
- **Explainable routing** — classify_task_detailed with signal breakdown
- **Adversarial routing tests** — false positive prevention, accuracy benchmark ≥80%
- **3 classification bug fixes** — "fix" keyword too generic, simple vs programming, backtick detection
- **Explanation layer** — DecisionExplanation captures routing/policy/learning/memory context
- **Learning model document** — docs/LEARNING_MODEL.md defining 4 learning types
- **Learning audit trail** — LearningAuditLog for all learning decisions

### Infrastructure
- **Workspace limits** — max_active (default 3), TTL auto-cleanup
- **Workspace persistence** — SQLite-backed lifecycle, audit trail, recovery
- **Centralized persona** — single source of truth for prompts
- **Smoke tests** — all modules import without errors
- **CI** — mypy, architecture invariants, DeprecationWarning as error
- **974+ tests** — from 708 to 974+, zero regressions

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
