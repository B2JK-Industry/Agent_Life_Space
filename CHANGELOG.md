# Changelog

All notable changes to Agent Life Space are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [3.0.0] — 2026-03-26

### Added
- **SandboxExecutor** — high-level API: execute_python(), run_tests(), iterate() with auto-fix
- **Tool use** — 10 tool definitions for LLM function calling (store_memory, query_memory, create_task, list_tasks, run_code, run_tests, web_fetch, check_health, get_status, search_knowledge)
- **ToolExecutor** — maps tool calls to agent module methods, run_code ALWAYS through sandbox
- **ToolUseLoop** — multi-turn conversation (LLM calls tools → execute → feed results back)
- **Channel abstraction** — Channel ABC, IncomingMessage/OutgoingMessage, ChannelRegistry for multi-channel support
- 44 new tests for tools, providers, channels

## [2.0.0] — 2026-03-26

### Added
- **Provider-agnostic LLM layer** — ClaudeCliProvider, AnthropicProvider, OpenAiProvider
- **ModelTier system** — FAST/BALANCED/POWERFUL mapped per provider (Anthropic, OpenAI, local)
- **RequestContext** — per-request context prevents race conditions between concurrent messages
- **Per-chat conversation** — buffer + session ID per chat_id (no cross-chat leaking)
- `AGENT_SANDBOX_ONLY` env var — blocks host file access when set
- `AGENT_PROJECT_ROOT` env var — configurable project root (no hardcoded paths)

### Changed
- Inline subprocess calls replaced with provider.generate() in agent_loop and telegram_handler
- LLMRouter uses request.model instead of hardcoded Opus (cost savings)

### Security
- Race conditions fixed — shared instance vars replaced with RequestContext dataclass
- Safe mode check moved before command dispatch (was bypassed on first call)
- SQL injection fix in persistent_conversation (parameterized LIKE queries)
- Vault fail-fast when encrypted secrets exist but key is missing

## [1.3.0] — 2026-03-26

### Added
- Integration tests expanded: 14 → 34 (cross-module, finance lifecycle, consolidation, message priority)
- GitHub Actions CI (lint + tests + security audit on every push/PR)
- GitHub community standards (CoC, Contributing, Security policy, issue/PR templates)

## [1.2.0] — 2026-03-26

### Added
- **test_e2e_effectiveness.py** — 44 tests verifying all modules are wired and used
- **test_security_audit.py** — 50 automated security audit tests (replaces manual reviews)
- Safe mode bug fix verified end-to-end

## [1.1.0] — 2026-03-26

### Added
- Message queue persistence (SQLite) — messages survive crashes, replayed on restart
- PID lockfile — prevents duplicate agent instances

### Fixed
- Prompt injection via work description (sanitization added)
- LLM Router hardcoded model → uses request.model parameter
- Vault refuses unencrypted storage without key

## [1.0.0] — 2026-03-25

### Added
- Anti-confabulation: auto-inject runtime facts when John is asked about his own state
- `/runtime` command — agent can inspect its own running state
- Persistent conversation — SQLite-backed (core memory + rolling summary + recent messages), survives restarts
- Dead man switch + watchdog escalation protocol
- Agent-to-Agent HTTP API (port 8420) with API key authentication
- Group chat support — other bots can interact with John
- Tool pre-routing — auto-fetch weather, crypto prices, datetime before LLM
- Projects + Workspace modules with conversation memory
- Honest README — feature tiers (Stable/Beta/Experimental), maturity matrix, known limitations

### Changed
- Dispatcher detectors tightened: max 4 words, stricter patterns to reduce false positives
- Agent API timeout raised to 90s with partial response (no more connection aborts)
- Agent-aware prompts improve conversation quality and longer context buffer

### Fixed
- Empty results handling in dispatcher
- Weather: normalize Slovak declined city names (prahe -> Praha)
- Tool router timeout increased from 5s to 15s for slow external APIs
- Conversation buffer: skip dispatcher on short follow-ups

### Security
- **AGENT_OWNER_NAME** env var required — owner identification no longer hardcoded
- API now requires `AGENT_API_KEY` for all endpoints
- API binds to `127.0.0.1` by default (was `0.0.0.0`)
- Prompt injection detection now **blocks** malicious input (previously only warned)
- Semantic cache poisoning fix — validated inputs before caching
- Docker sandbox image whitelist + `shlex.quote` on all shell arguments
- Work queue permission gating — only owner can queue tasks
- Group chat safe mode — restricted command set in non-private chats
- Error message sanitization — no internal paths or stack traces leaked to users

## [0.9.0] — 2026-03-20

### Added
- Persistent conversation system (MemGPT-style: core memory + summary + retrieval)
- Knowledge base updates: `runtime_features.md` documents all 7 cron jobs
- `docs/REVIEW_NOTES.md` — 10 verification test instructions

### Changed
- Agent API: structured messages with intent and metadata fields

## [0.8.0] — 2026-03-15

### Added
- Watchdog dead man switch with escalation protocol
- Response quality detector: auto-escalate Haiku -> Sonnet when quality is low

### Changed
- Budget dispatcher: removed overly broad `financ` keyword, max 4 words
- All dispatcher detectors tightened for precision

### Fixed
- Dispatcher false positives resolved
- John's self-knowledge restored after dispatcher changes

## [0.7.0] — 2026-03-10

### Added
- Agent-to-Agent HTTP API for bot communication (port 8420)
- API key authentication for Agent API
- Group chat support: other bots can interact with John

### Fixed
- Weather: Slovak declined city name normalization
- Tool router timeout: 5s -> 15s for wttr.in

## [0.6.0] — 2026-03-05

### Added
- Tool pre-routing: auto-fetch weather, time, crypto prices
- Projects + Workspace modules, conversation memory
- Post-routing quality detector: Haiku -> Sonnet auto-escalation
- Smart classifier, web search, auto URL fetch, token optimization

### Changed
- Token optimization: large docs moved out of CLI context, JOHN.md shrunk
- `/search` renamed to `/hladaj` (Telegram reserves `/search`)

### Fixed
- Command parsing: strip @botname suffix from commands
- Conversation buffer: skip dispatcher on short follow-ups

## [0.5.0] — 2026-02-25

### Added
- Learning system v2: behavioral changes, not just logging
- README.md and MIT LICENSE

### Changed
- Sandbox timeout: kill Docker container directly, not just wrapper
- Architecture review fixes: sandbox mandatory, learning feedback loop, error recovery

## [0.4.0] — 2026-02-18

### Added
- Wallet support (ETH + BTC) with encrypted vault
- Finance module: propose -> approve -> complete workflow
- Knowledge base: 13 knowledge files covering people, systems, projects, skills
- Learning system: skills + knowledge base + memory consolidation
- Skills registry: 20 default skills, learns from experience

### Changed
- Architecture cleanup + complete documentation rewrite

## [0.3.0] — 2026-02-10

### Added
- Semantic cache + Self-RAG (knowledge base retrieval)
- Semantic router (MiniLM, preloaded at startup)
- 5-layer cascade routing (local compute before LLM)
- Internal dispatcher: answer without LLM where possible
- Slovak word normalization for dispatcher search
- Centralized model router (`agent/core/models.py`)
- `get_slovak_time()` utility

### Changed
- Sonnet for chat, Opus reserved for programming tasks only

## [0.2.0] — 2026-02-01

### Added
- Docker sandbox for safe code execution (256MB, no network, read-only)
- Web module + `/web` command for internet access
- Programmer brain: structured coding workflow
- `/review` command for code review
- `/usage` command for token tracking
- Auto skill testing (event-driven)
- Memory consolidation: episodic -> semantic + procedural

### Changed
- Multi-task detection before Claude — straight to work queue
- JSON-first context: structured agent state as JSON

### Fixed
- Sandbox shell escaping: use stdin instead of `-c`
- Work queue re-queuing past summaries
- Empty Claude responses handled gracefully
- Telegram Markdown fallback to prevent parse errors

## [0.1-beta] — 2026-01-20

### Added
- Initial agent scaffold: 11 core modules
- Telegram bot with polling and typing indicator
- Claude Opus integration via Max subscription
- Memory store: 4 types (episodic, semantic, procedural, working)
- Watchdog with heartbeats and auto-restart
- Job runner with circuit breaker and retry
- Server maintenance module with 3h maintenance cron
- JOHN.md identity file
- 219 initial tests
