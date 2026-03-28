# Changelog

All notable changes to Agent Life Space are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/):
- PATCH (1.0.x) — bug fixes, small opravy
- MINOR (1.x.0) — nové features, spätne kompatibilné
- MAJOR (x.0.0) — breaking changes (len so schválením)

## [Unreleased]

## [1.9.0] — 2026-03-28

Phase 2 kickoff release.

### Builder / Delivery Evidence
- Build verification now persists one suite-level verification report plus one
  per-step verification artifact for each executed verification step, instead
  of flattening the whole run into one generic payload
- Build delivery bundles now expose verification artifact ids and summaries,
  plus richer acceptance handoff summaries grouped by criterion status for
  operator-facing delivery review

### Governance / Runtime Policy
- Build jobs now resolve explicit source-aware build execution policies before
  mutable workspace execution, record those decisions as control-plane traces,
  and block unsupported execution sources with stable deny-by-default payloads
- Runtime model now exposes higher-level `local_owner`,
  `operator_controlled`, and `enterprise_hardened` operating profiles on top
  of the lower-level review/build/acquisition/export execution profiles

### Strategy / Planning
- Strategy docs now mark this slice as the Phase 2 kickoff and move the next
  backlog toward golden reviewer cases, remaining structured denials,
  enterprise data-handling rules, and richer builder acceptance semantics

### Verification
- Local release verification passed with `1286 passed, 4 skipped`
- Targeted builder/control-plane regression coverage passed with `86 passed`

## [1.8.2] — 2026-03-28

Phase 1 final closure release.

### Reviewer / Delivery
- Review delivery now persists copy-paste-ready PR comment markdown and
  operator-summary artifacts alongside the canonical report, then includes
  those handoff artifacts in the shared delivery bundle
- Client-safe evidence export now reuses those redacted review handoff
  summaries so operators can export cleaner client-facing reviewer packages

### Governance / Denials
- Added a shared structured denial payload model and propagated it through
  tool-policy blocks, operator-intake blockers, build/review delivery approval
  and handoff blockers, and evidence-export denials
- Operator reporting now prefers structured denial summaries/details when
  surfacing blocked job attention instead of flattening those states into
  generic error strings

### Quality / Regression Gating
- Added `tests/test_review_eval_smoke.py` to validate reviewer handoff
  artifacts and client-safe redaction behavior end-to-end
- CI now runs that review-eval smoke suite explicitly as part of the default
  workflow

### Verification
- Local release verification passed with `1285 passed, 4 skipped`
- Targeted closure regression coverage passed with `147 passed`

## [1.8.1] — 2026-03-28

Phase 1 delivery closure release.

### Reviewer / Delivery
- Review delivery now assembles into the shared `DeliveryPackage` /
  `DeliveryRecord` lifecycle instead of staying on an ad-hoc parallel bundle
  path
- Review delivery approval now carries explicit bundle and workspace linkage,
  refreshes persisted lifecycle state after approval changes, and supports
  explicit post-approval handoff

### Control Plane / Compliance
- Retained artifacts now support an explicit prune workflow through the
  control-plane service, orchestrator, and CLI via
  `python -m agent --prune-expired-retained-artifacts`
- Evidence export now supports a client-safe review mode via
  `python -m agent --export-evidence-job ... --export-evidence-mode client_safe`
  so operators can package review evidence without leaking internal detail

### Operator / Observability
- Operator report now surfaces approval backlog counts by status and category,
  blocked approval reasons, and partial-approval detail instead of flattening
  approvals into a simple pending list
- Operator report now also exposes retention posture, including expired and
  pruned retained-artifact counts

### Verification
- Local release verification passed with `1280 passed, 4 skipped`
- Targeted review/control-plane regression coverage passed with `134 passed`

## [1.8.0] — 2026-03-28

Phase 1 closure hardening release.

### Operator / Intake
- Unified operator intake now supports a managed acquisition/import path for
  supported git sources, including local `file://` repositories that are cloned
  into a controlled mirror before review/build routing
- Runtime approval requests can now require multi-step approval when budget,
  risk, review severity, or delivery scope crosses deterministic thresholds

### Control Plane / Compliance
- Added a dedicated evidence export surface via
  `python -m agent --export-evidence-job ...`, assembling persisted product
  jobs, artifacts, retention records, traces, cost entries, runtime model data,
  and artifact traceability links into one package
- Persisted product-job records now carry duration, retry count, and failure
  count telemetry, and the operator report now summarizes those signals
- Runtime model now exposes explicit environment profiles for review, build,
  acquisition/import, and export-only flows

### Budget / Governance
- Brain-side learning overrides and post-routing model escalation are now
  budget-aware and can be blocked by runtime budget posture
- Operator report now exposes richer cost posture, including the
  single-transaction approval cap and product-job attention entries for failed
  persisted jobs

### Verification
- Local release verification passed with `1276 passed, 4 skipped`
- Additional smoke coverage passed for `--intake-git-url`, `--runtime-model`,
  `--report`, `--list-persisted-jobs`, and `--export-evidence-job`

## [1.7.0] — 2026-03-27

Review entrypoint convergence and runtime budget governance release.

### Reviewer / API
- Telegram `/review` and the new structured `POST /api/review` endpoint now
  converge through the shared review runtime instead of bypassing it with
  adapter-only logic
- Review intake now preserves its channel source through recovery-safe
  persistence, and review product-job metadata now carries deterministic review
  execution policy identity
- Repository and diff review access now runs under explicit deterministic
  review execution policies with durable control-plane policy traces

### Operator / Governance
- Unified operator intake now blocks execution on hard-cap and stop-loss budget
  conditions instead of treating budgets as preview-only metadata
- Runtime submission now creates approval requests for approval-cap budget
  cases and high-risk execution before starting build/review jobs
- Planner handoff records now use `awaiting_approval`, `executing`, and
  `blocked` transitions more honestly, with runtime execution traces recording
  budget blocks, approval requests, and job completion

### Cost / Observability
- `FinanceTracker.check_budget()` now exposes soft-cap, hard-cap, stop-loss,
  approval, warning, and forecast posture instead of only simple remaining
  amounts
- Operator report now surfaces budget posture plus inbox-visible budget
  warnings/blocks and cost-margin hints for the operator

### Builder / Local Execution
- Builder verification now prefers repo-local `.venv` toolchains when the
  workspace intentionally excludes copied virtualenv directories
- Build jobs without explicit acceptance criteria now use verification outcome
  as an explicit acceptance proxy instead of failing with a misleading `0 unmet`
  rejection

### Verification
- Local release verification passed with `1273 passed, 4 skipped`

## [1.6.0] — 2026-03-27

Unified control-plane persistence and retention release.

### Platform / Control Plane
- Build and review jobs now sync into shared `ProductJobRecord` persistence,
  making product-job metadata queryable through the control plane instead of
  living only inside bounded-context stores
- Shared retained-artifact records now cover build, review, and delivery-bundle
  outputs with policy ids, expiry timestamps, recoverability, and retention
  status
- CLI and orchestrator now expose persisted-job, retained-artifact, and
  per-job cost-ledger list/get surfaces

### Governance
- Shared policy model now includes deterministic job-persistence,
  artifact-retention, and external-gateway policies alongside the existing
  delivery and review-gate profiles
- Artifact query and reporting surfaces now expose retention metadata instead
  of hiding policy and expiry state

### Observability
- Per-job usage, tokens, and cost now land in a durable control-plane ledger
  for build and review jobs
- Operator report now includes recent persisted product jobs, retained
  artifacts, cost-ledger entries, and recorded cost totals

### Verification
- Local release verification passed with `1262 passed, 4 skipped`

## [1.5.0] — 2026-03-27

Durable planning and delivery lifecycle release.

### Operator / Control Plane
- `JobPlan` preview/submit output is now persisted as a first-class handoff
  record with stable plan IDs and orchestrator/CLI list/get surfaces
- Planning decisions now emit durable control-plane traces for qualification,
  budget, capability, delivery, verification discovery, and review-gate policy
- Workspace records are now queryable as shared joins over jobs, artifacts,
  approvals, and delivery bundles
- Operator report now includes recent plans, traces, deliveries, and workspace
  records in addition to jobs, approvals, and artifacts

### Builder
- Builder verification now performs repo-aware discovery for test, lint, and
  typecheck surfaces before running the workspace verification suite
- Post-build review thresholds are now governed by deterministic review-gate
  policies instead of one hard-coded block rule
- Build delivery now records persisted lifecycle state and audit events across
  prepare, approval request, approval refresh, rejection, and handoff
- CLI now exposes shared control-plane list/get surfaces for plans, traces,
  workspaces, deliveries, and explicit build delivery handoff

### Governance
- Delivery approval context now includes deterministic delivery-policy identity
- Builder planning and delivery metadata now surface explicit policy choices
  instead of hiding them behind service-only defaults

### Verification
- Local release verification passed with `1260 passed, 4 skipped`

## [1.4.5] — 2026-03-27

Builder delivery package and operator health release.

### Builder
- Builder now captures deterministic patch + diff artifacts by comparing the
  source repo and workspace, instead of relying on placeholder workspace diff
  metadata
- Build delivery now exposes a shared `DeliveryPackage` preview with
  verification, acceptance, review, patch, diff, findings, and workspace
  payloads
- Acceptance evaluation now supports richer deterministic checks, including
  post-build review verdicts plus documentation and target-file change rules

### Operator / Control Plane
- Operator report now includes workspace health and worker execution summaries
  in addition to jobs, approvals, and artifacts
- Shared `DeliveryPackage` model added to the control-plane foundation
- Approval queries and build delivery approvals now link job, artifact,
  workspace, and bundle records together

### Verification
- Local release verification passed with `1255 passed, 4 skipped`

## [1.4.4] — 2026-03-27

Planner qualification and phase routing release.

### Operator
- Unified operator intake now resolves scope signals, risk factors, and a
  policy-backed budget envelope using `BudgetPolicy` plus live finance budget
  state when available
- `JobPlan` now exposes explicit qualify/review/build/verify/deliver phases in
  preview and submit flows
- Planner output now assigns concrete build catalog capabilities plus planner
  profiles and structured budget metadata

### Builder
- Planner-selected build catalog capability ids now flow into `BuildIntake`
  instead of remaining preview-only metadata

### Verification
- Local release verification passed with `1251 passed, 4 skipped`

## [1.4.3] — 2026-03-27

Runtime model and artifact planning release.

### Platform / Control Plane
- Explicit runtime coexistence rules added through `RuntimeModelService` and
  `python -m agent --runtime-model`
- Shared artifact query/recovery now spans build and review through
  `ArtifactQueryService`, orchestrator list/get methods, and CLI artifact
  inspection
- Build and review artifact storage now persist artifact `format` alongside
  content recovery payloads

### Operator
- Unified operator intake now emits a real `JobPlan` preview/submit output with
  steps, planned artifacts, heuristic budget envelope, and recommended next
  action
- Operator report now includes recent artifacts alongside jobs and approvals

### Verification
- Local release verification passed with `1248 passed, 4 skipped`

## [1.4.2] — 2026-03-27

Control-plane expansion release.

### Platform / Control Plane
- `ReviewJob` now uses shared control-plane primitives (`JobKind`, `JobStatus`,
  `JobTiming`, `ExecutionStep`, `UsageSummary`)
- Shared job queries now cover build, review, task, job-runner, and agent-loop
  runtime records
- Approval requests are now persistent and queryable with job/artifact linkage
- Operator reporting now has a real runtime surface via `OperatorReportService`
  and `python -m agent --report`

### Builder
- Builder capability catalog added for implementation, integration, devops, and
  testing work
- Build jobs now record resumable checkpoints and can resume through
  `BuildService.resume_build()` and `python -m agent --build-resume ...`
- Build query metadata now surfaces capabilities, checkpoints, and resume state

### Operator
- Unified operator intake model added for repo path, git URL, diff, and work
  type routing
- `AgentOrchestrator` now exposes qualification/submission methods for unified
  build/review intake
- `python -m agent --intake-*` now provides a CLI preview/submit path for the
  shared intake model
- TypeScript operator skeleton now includes a mock reporting/inbox surface

### Verification
- Local release verification passed with `1241 passed, 4 skipped`

## [1.4.1] — 2026-03-27

Bug-fix release for `1.4.0`.

### Release Notes
- Patch release line for the tested post-`1.4.0` main state
- Intended as bug-fix continuation of the `1.4.0` release

## [1.4.0] — 2026-03-26

Backlog zero release. All items from master backlog implemented.

### Governance
- **Multi-step approval** — required_approvals, PARTIALLY_APPROVED status, same-person dedup

### Testing
- **Routing confusion analysis** — systematic confusion detection + fallback hierarchy
- **Workspace recovery** — 4 crash-recovery test scenarios
- **Finance proposal lifecycle** — end-to-end propose → approve → complete

### Documentation
- **Product identity** — decision: personal sovereign operator, not platform
- **Release checklist** — standardized process

### Fixes
- **setup_vault.py** — graceful handling when eth_account/bit not installed

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
