# Changelog

All notable changes to Agent Life Space are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/):
- PATCH (1.0.x) — bug fixes, small opravy
- MINOR (1.x.0) — nové features, spätne kompatibilné
- MAJOR (x.0.0) — breaking changes (len so schválením)

## [Unreleased]

## [1.33.0] — 2026-04-01

Docker-Isolated Build Execution — end-to-end project builds in containers.

### Docker Project Executor
- New `agent/build/docker_executor.py`: runs generated projects entirely
  inside Docker containers (pip install → pytest → ruff)
- Phases: deps install (with network) → tests (no network) → lint
- Safety: 512MB RAM, 1 CPU, 5min timeout, no network during tests
- Files mounted read-only, copied to writable /work inside container

### Auto-Fix Retry Loop
- When tests fail, Opus receives the test output and all source files
- Generates fixed code → re-runs tests in Docker → up to 2 retries
- Each retry is a full cycle: write files → install deps → run tests

### Build Pipeline Integration
- Codegen-produced builds now route through Docker executor instead of
  host workspace verification
- Falls back to host verification if Docker is unavailable
- Docker results stored in job metadata for reporting

### Improved Build Reporting
- Telegram/API shows Docker build details: files, deps, tests, lint, retries
- Failed builds show truncated test output for debugging
- Completed builds show LLM cost and retry count

### Tests
- 9 new tests in `test_docker_executor.py` (file writing, result model)

## [1.32.0] — 2026-04-01

LLM Build Pipeline — description-driven code generation with sandbox execution.

### LLM Code Generation
- New `agent/build/codegen.py`: Opus generates `BuildOperation[]` from natural
  language description — bridges "user describes what to build" to deterministic
  WRITE_FILE execution
- Robust JSON parser handles markdown fences, newlines in content, trailing
  commas, and invalid entries
- Safety: only WRITE_FILE ops, relative paths only, max operation cap enforced

### Build Pipeline Integration
- `BuildService.run_build()` now auto-generates implementation plan via LLM when
  `implementation_plan` is empty but `description` exists
- Previously empty plans triggered AUDIT_MARKER_ONLY (no mutations); now they
  trigger full code generation → workspace execution → verification

### Task Classification (Bilingual)
- All classifier keyword sets now include English equivalents (programming,
  simple, action, capability, implementation intent)
- New `_TECHNICAL_TERMS` signal: 2+ technical terms boost programming score
- New `_IMPLEMENTATION_INTENTS` + tech term combo signal prevents false routing
- Sonnet max_turns increased from 3 to 5

### API & Channel Trust
- Authenticated API callers now get terminal-level trust (full response class)
- API timeout scales with task complexity (300s for programming, 90s for chat)
- Sandbox-first: AGENT_SANDBOX_ONLY=1 downgrades to conversational mode instead
  of hard-blocking

### Bug Fixes
- `PlanRecordStatus.FAILED` added — failed builds no longer crash on enum lookup
- Build/review job status now passes through real status (failed, blocked, etc.)
  instead of mapping all non-completed to "blocked"
- Telegram handler shows explicit failed job details (ID, verification, error)

### Tests
- 12 new tests in `test_build_codegen.py` (parsing, validation, edge cases)
- Updated `test_llm_provider.py` for sandbox downgrade behavior

## [1.31.0] — 2026-04-01

Runtime Contract Closure — auth boundary, public API discipline, extraction readiness.

### Dashboard Authentication
- `/dashboard` now requires API key (header or `?key=` query param)
- Unauthenticated access returns a minimal login page
- Dashboard HTML no longer served to anonymous clients

### Public API Discipline
- `ControlPlaneStateService` now exposes `save_settlement_request()`,
  `list_settlement_requests()`, and `get_storage_for_archival()`
- Settlement service uses public methods (no `._storage` private access)
- Archival API handlers use `get_storage_for_archival()` (no `getattr`)

### Service Extraction Readiness
- `OperatorReportService` now receives `settlement_service` at construction
  (no post-init `._settlement_service` mutation)
- Reporting initialization moved after settlement in orchestrator init order

### Tests
- 4 new contract tests: dashboard auth, no private storage access,
  no post-init mutation, archival public API

## [1.30.0] — 2026-04-01

Deployment Contract Hardening — deny-by-default, explicit config, no hidden coupling.

### Deny-by-Default Enforcement
- Removed AGENT_DEV_MODE approval bypass from review and build services
- Policy enforcement is no longer environment-dependent
- Delivery without approval queue is always denied

### Explicit Configuration
- `paths.py`: raises RuntimeError instead of silent fallback to ~/.agent-life-space
- Pidfile path configurable via AGENT_PIDFILE_PATH env var
- Vault exposes `is_ready` property for startup validation
- Startup config summary logged (project root, API port, vault, docker, sandbox)

### Reduced Hidden Coupling
- Docker availability stored as agent attribute (not env var mutation)
- Gateway `on_payment_required` callback passed at construction time (not post-init)
- Sandbox default uses setdefault instead of bracket assignment

### Tests
- 10 new deployment contract tests
- DEV_MODE bypass test replaced with deny-without-queue test

## [1.29.0] — 2026-04-01

Settlement Workflow Closure — from foundation to operator-ready workflow.

### Settlement Persistence
- `settlement_requests` SQLite table — settlements survive agent restart
- `SettlementRequest.from_dict()` for deserialization
- Load-on-init + persist-on-create/approve/deny/execute

### Settlement API Write Surface
- `POST /api/operator/settlements/{id}/approve` — operator approves topup
- `POST /api/operator/settlements/{id}/deny` — operator denies
- `POST /api/operator/settlements/{id}/execute` — topup + auto-retry
- `GET /api/operator/settlements?status=pending` — list with status filter

### Approved Retry Loop
- Successful topup automatically retries the original API call
- `original_request` context stored with each settlement
- Retry result included in execute response

### Gateway 402 Auto-Creation
- Gateway calls `on_payment_required` callback when HTTP 402 received
- Orchestrator auto-creates settlement request with original request context
- Operator sees pending settlements immediately in API and dashboard

### Dashboard Settlement UI
- Settlements section with Approve/Deny/Execute buttons
- Real-time refresh after actions
- Status badges (pending/approved/executed/denied/failed)

### Reporting Integration
- `settlement_attention` items in operator inbox for pending settlements
- `OperatorReportService` accepts optional `settlement_service` dependency

### Tests
- 9 new tests: from_dict roundtrip, list by status, retry loop,
  dashboard section, API approve/deny/execute/invalid/404

## [1.28.1] — 2026-04-01

Post-merge closure: archive retrieval, settlement Telegram surface, docs truthfulness.

### Archive Retrieval
- `GET /api/operator/archive/download/{filename}` — serve CSV with Content-Disposition
- Path-traversal protection: rejects `..`, `/`, non-`.csv` filenames

### Settlement Telegram Surface
- `/settlement` — list pending payment settlement requests
- `/settlement approve <id> [note]` — operator approves topup
- `/settlement deny <id> [note]` — operator denies

### Docs
- `AS_IS_TO_BE_2026_04_01.md` rewritten as post-merge archival snapshot
- `BACKLOG_PROGRESS.md` updated with complete Phase 4 state

### Tests
- 4 new archive download tests (traversal rejection, valid path, auth, 404)
- 1608 total tests pass

## [1.28.0] — 2026-04-01

Phase 4: Operator dashboard, payment settlement foundation, production regression fixes.

### Operator Dashboard
- Self-contained HTML dashboard served at `GET /dashboard` on port 8420
- No React, no build tools — vanilla HTML + CSS + JS
- Real-time metrics: jobs, cost/margin, telemetry, system status
- Job listing table with status badges
- Retention posture and table sizes
- API audit log stats
- API key auth via localStorage, auto-refresh every 30s
- Dark theme, responsive, monospace design

### Payment Settlement Foundation
- `PaymentSettlementService` in `agent/control/settlement.py` — **service foundation**,
  not yet a full automated payment loop
- Parses 402 Payment Required denials from gateway
- Wallet balance check via `wallet_balance_v1` capability
- Settlement request creation with operator approval requirement
- Approve/deny workflow (human-in-the-loop, no automatic spending)
- Wired into orchestrator (`agent.settlement`) and exposed via
  `GET /api/operator/settlements`
- Pending state is in-memory (not persisted across restarts)
- **Not yet**: automatic 402→retry loop, dashboard approval UI, persisted state

### Production Regression Fixes
- `/api/operator/report` was broken: passed control_plane as job_queries
  to OperatorReportService. Now delegates to `agent.reporting` (correctly wired).
- Memory injection was epistemically unsafe: injected all semantic memories
  as "Known facts" regardless of provenance. Now filters by
  OBSERVED/USER_ASSERTED/VERIFIED provenance and FACT/PROCEDURE kind.
  Framing changed to "Stored memories (may be outdated)".
- Operator API query params returned 500 on bad input. Added `_parse_int_param()`
  helper for structured 400 denial on all endpoints.
- Pipeline job linkage used stale `control_plane_state` name. Fixed to `control_plane`.
- Archival wrote to repo source tree and leaked host filesystem paths. Now writes
  to `data/archive/` and returns filename-only.

### Tests
- 22 tests in `test_dashboard_settlement.py` (dashboard + settlement)
- 12 handler-level tests in `test_operator_api.py` catching broken wiring,
  bad query params, 404 for missing jobs, archive path safety

## [1.27.0] — 2026-04-01

Phase 4 continued: Operator REST API and compliance-grade archival.

### Operator REST API
- 9 new authenticated endpoints under `/api/operator/`
- All endpoints require API key auth, graceful fallback when control plane unavailable

### Archival Service
- `ArchivalService`: CSV export for 5 compliance tables
- `GET /api/operator/archive` endpoint: list archives or trigger export

### Tests
- 17 new tests for operator API + archival

## [1.26.0] — 2026-04-01

Phase 4 enterprise hardening: CI-enforced invariants, automated retention, unified policy boundary.

### CI-enforced Architecture Invariants
- 26 pytest tests as explicit blocking CI gate
- Shell-based checks replaced by pytest

### Automated Retention & Pruning
- Hard-delete methods, retention pruning cron (6h), nightly cleanup cron
- `/report retention` Telegram subcommand

### Unified Policy Boundary Migration
- Gateway, review, build callers migrated to `evaluate_runtime_action()`
- `RuntimePolicyDecision` enriched with `resolved_policy` and `policy_metadata`

## [1.25.1] — 2026-04-01

Production hardening: rate limits, telemetry auto-recording, persistence, cache accuracy.

### Fixed
- API rate limit: 60/min for localhost, 10/min for external
- Semantic cache: threshold 0.90→0.95, length-ratio guard, skip commands

### Added
- Telemetry auto-recording (hourly cron)
- Workflow + Pipeline SQLite persistence

## [1.25.0] — 2026-03-31

Phase 3 operatorization closure: recurring workflows, pipelines, margin tracking.

### Recurring Workflows
- `RecurringWorkflow` model with schedule (daily/weekly/monthly), intake
  template, execution tracking, and auto-pause on consecutive errors
- `RecurringWorkflowManager` with create/pause/activate/get_due_workflows
- `/workflow` Telegram command: create, list, pause, activate workflows

### Multi-Job Pipelines
- `JobPipeline` and `PipelineStage` models with sequential execution,
  condition-based stage gating (on_success/on_failure/always)
- `PipelineOrchestrator` executes stages sequentially, tracks status per stage
- `/pipeline` Telegram command: create, run, list pipelines
- Pipeline stages link back to ProductJobRecord via pipeline_id

### Margin Tracking
- `ProductJobRecord` extended with revenue_usd, margin_usd, revenue_source,
  pipeline_id, workflow_id fields
- `record_job_revenue()` in state service calculates margin automatically
- `get_margin_summary()` aggregates revenue/cost/margin across jobs
- `/report margin` sub-command shows margin summary
- `/report margin set <job_id> <usd>` records revenue for a job

### Bug Fix
- Channel policy: `get_channel_capabilities()` now recognizes "private",
  "group", "supergroup" as Telegram channel types (was only "telegram")

## [1.24.1] — 2026-03-31

Runtime bug fixes for production deployment.

### Fixed
- **Bug #1**: "Zapamätaj si" / "Remember" messages now handled by dispatcher
  instead of LLM — zero token cost, no errormaxturns on CLI backend
- **Bug #2**: Dispatcher catches Slovak queries ("aký je tvoj stav",
  "aké úlohy máš", "koľko máš peňazí") — saves ~$0.05/query
- **Bug #3**: Runtime facts (tasks, memory, health, budget) injected into LLM
  prompt to prevent confabulation about agent state
- **Bug #4**: INTERNAL response class now allowed on FULL trust channels
  (owner in private chat) — no more silent response filtering
- **Bug #5**: `/queue` no longer crashes with KeyError 'total_processed' —
  fixed to use 'total_attempted' from AgentLoop.get_status()
- **Bug #6**: `/jobs` now shows correct job IDs and types instead of "? ?" —
  fixed dict key mapping (job_id/job_kind vs id/kind)
- **Bug #7**: `/report` now shows correct completed/failed job counts —
  added completed_jobs and failed_jobs to report summary

## [1.24.0] — 2026-03-31

Phase 3 completion: file upload support and x402 payment handling.

### File Upload Support (T7-E2-S6)
- `_request_http()` now supports `form_data` parameter for multipart/form-data
  requests — aiohttp handles Content-Type and boundary automatically
- File fields passed as `(filename, content_bytes)` tuples, string/bytes fields
  converted automatically
- `form_data` threaded through the full call chain: `call_api_via_capability()` →
  `_build_api_call_request()` → `_execute_http_request_with_retry()` → `_request_http()`
- New `marketplace_upload_v1` capability with `obolos_marketplace_upload_v1`
  request/response mode for slug-based file upload APIs
- `call_api_across_providers()` also accepts `form_data`

### x402 Payment Metadata (T7-E2-S6)
- `_extract_x402_payment_metadata()` parses structured payment details from
  HTTP 402 responses: Retry-After header, x-payment-*/x-credits-*/x-price-*
  headers, and body fields (credits_required, price, cost, payment_url, etc.)
- 402 denial metadata now includes `payment` dict with parsed headers, body
  fields, error message — enabling downstream payment workflow decisions

## [1.23.0] — 2026-03-31

Phase 3: seller-side Obolos, multi-provider gateway, architecture invariants.

### Seller-Side Obolos Publishing (T7-E2-S5)
- New `seller_publish_v1` capability: POST `/api/seller/apis` for registering
  or updating API listings on the Obolos marketplace
- New `wallet_topup_v1` capability: POST `/api/wallet/topup` for initiating
  credit top-up for the configured wallet address
- Request/response modes in gateway: `obolos_seller_publish_v1` returns
  slug/api_id/status; `obolos_wallet_topup_v1` returns new_balance/transaction_id
- Both routes require wallet auth and owner approval

### Multi-Provider Gateway Contract (T7-E1-S1)
- `list_providers_for_capability(capability_id)` — discover all providers
  supporting a capability, enabling multi-provider resolution
- `resolve_capability_across_providers()` — resolve routes across ALL
  providers for a capability (not just one provider_id)
- `call_api_across_providers()` — try providers in order until one succeeds,
  with intelligent fallback (retryable vs permanent failures)
- Capability-to-providers map now included in gateway catalog output

### Architecture Invariants (T8-E1-S3)
- 22 enforcement tests covering 6 categories:
  1. Import graph boundaries — bounded contexts only import from allowed modules
  2. Execution mode contracts — review/build respect declared modes
  3. Gateway boundary — external HTTP only in approved modules
  4. Cross-domain isolation — review/build don't access each other's storage
  5. Shared control plane — all contexts use shared primitives (no parallel enums)
  6. Multi-provider contract — seller routes, capability resolution, catalog map

## [1.22.0] — 2026-03-31

Phase 3: provider-specific delivery workflow and runtime telemetry.

### Provider Delivery Workflow (T4-E3-S4)
- `/deliver <job_id>` now shows provider outcome, provider status, receipt,
  capability, route, and attention-required flag — data that was already
  recorded but not surfaced to the operator
- `/deliver <job_id> retry` re-sends failed deliveries through the gateway
- `/deliver pending|failed|delivered` filters deliveries by provider outcome
- `/deliver` listing now shows provider outcome badge per delivery
- `/report delivery` sub-command shows provider delivery summary with outcome
  breakdown, per-provider counts, and attention-required items
- Event detail (truncated) now visible in delivery event listing

### Runtime Telemetry (T6-E2-S1)
- `TelemetrySnapshot` model captures point-in-time runtime metrics: job
  throughput, latency (avg/max/p95), cost, delivery health, system resources
- `record_telemetry_snapshot()` builds snapshot from persisted product jobs,
  cost ledger, deliveries, and optional live worker/system stats
- `get_telemetry_summary(window_hours=24)` aggregates snapshots over a time
  window with trend detection (stable/improving/degrading)
- New `TraceRecordKind.TELEMETRY` — telemetry snapshots persisted as trace
  records for time-series querying
- `/telemetry [hours]` Telegram command shows runtime dashboard with
  throughput, latency, cost, delivery health, system resources, and trend
- `/report telemetry` sub-command for operator telemetry visibility
- Telemetry summary included in operator report output

## [1.21.1] — 2026-03-30

Deployment portability and security fix.

### Portability
- **consolidation.py**: Identity and server triggers now derived dynamically from
  `get_agent_identity()` instead of hardcoded "john", "b2jk", "agentlifespace"
- **redaction.py**: Hostname redaction patterns now built dynamically from
  `AGENT_SERVER_NAME` — non-default deployments no longer leak hostnames in
  client-safe bundles
- **Dockerfile**: Fixed build order — source copied before `pip install`
- **docker-compose.yml**: Added 7 missing env vars (identity, API key, sandbox)
- **.env.example**: Added `AGENT_SANDBOX_ONLY`, `LLM_BACKEND`, section headers
- **pyproject.toml**: Removed 4 unused dependencies
- **SECURITY.md**: Supported version updated to v1.21.x

## [1.21.0] — 2026-03-30

Phase 3: cost estimation feedback loop and unified runtime policy boundary.

### Cost Estimation Feedback (T6-E1-S1)
- `ControlPlaneStateService.get_cost_accuracy()` joins plan records with cost
  ledger entries to compare estimated vs actual costs per job
- Operator report now includes `cost_accuracy` section with sample size,
  avg/median ratio, accuracy percentage, and per-job comparisons
- `/report cost` Telegram subcommand for operator cost accuracy visibility
- New `TraceRecordKind.COST_ACCURACY` for recording accuracy snapshots

### Unified Policy Boundary (T5-E1-S1)
- `RuntimeActionRequest` frozen dataclass describes any runtime action
  (review, build, deliver, gateway_send, api_call) in a policy-neutral way
- `evaluate_runtime_action()` dispatches to existing policy functions based on
  action_type and returns a unified `RuntimePolicyDecision` with
  allowed/blocked/warnings/applied_policies
- Existing individual policy evaluate functions remain untouched as internal
  implementation — no callers migrated, no interfaces changed

## [1.20.0] — 2026-03-30

Phase 3: runtime capability binding and operator delivery workflow.

### Operator Commands
- **`/jobs`** — list product jobs (review + build) and view job detail from Telegram
- **`/deliver`** — delivery listing, detail, and gateway send from Telegram
  (`/deliver <job_id> send` triggers actual gateway delivery)

### Runtime Capability Binding (T4-E2-S3)
- Review workflow planner phase now binds to execution policy with
  `execution_policy_id`, `allow_host_read`, and `allow_git_subprocess`
  metadata (not just a planner profile label)
- Review delivery phase now binds to delivery policy with
  `delivery_policy_id` and `approval_required` metadata
- Review verify phase remains a planner profile (internal step, no policy)
- Capability assignment `source` field now reflects the binding type:
  `execution_policy`, `delivery_policy`, or `planner_profile`

### Strategy
- T4-E2-S3 closed: review/verify/deliver phases enriched with runtime binding
- T4-E4-S4 (/jobs) and T4-E4-S5 (/deliver) stories added and completed

## [1.19.0] — 2026-03-30

Phase 3 kickoff: operator Telegram surface. Existing runtime capabilities
(intake, planning, reporting) are now accessible from Telegram chat.

### Operator Commands
- **`/intake`** — unified operator intake: qualify, plan, and execute review or
  build jobs from Telegram with `--type`, `--description`, `--git` parameters
- **`/report`** — operator report with overview, inbox, and budget views
  (`/report inbox`, `/report budget`)
- **`/build`** — shortcut for build intake (delegates to `/intake --type build`)

### Strategy
- Added Theme T4-E4 "Operator Telegram Surface" with 3 stories (all complete)
- Updated backlog progress, next backlog, and backlog seed for Phase 3

### Testing
- Added 14 new tests for operator Telegram commands (test_telegram_operator.py)

## [1.18.0] — 2026-03-30

Security hardening and conservative bug fixes from codebase audit.

### Security
- **sandbox**: Package names now validated against safe regex before shell interpolation
  (prevents command injection via crafted pip package names)
- **learning**: Shell-quoted `_PROJECT_ROOT` in skill test commands via `shlex.quote()`
  (prevents injection if project root contains special characters)
- **finance**: CSV export now properly escapes all fields (RFC 4180 + formula injection
  guard for Excel/Google Sheets)

### Bug Fixes
- **memory/consolidation**: `promote_inferred_to_verified` and `detect_stale_facts` now
  use `MemoryStore.update_entry()` instead of bypassing store abstraction with direct DB access
- **control/state**: `datetime.fromisoformat()` calls wrapped in try/except to prevent
  crashes on malformed retention timestamps
- **control/evidence_export**: Cost sum uses safe `.get()` chain to prevent KeyError on
  incomplete cost entries
- **telegram**: `_cmd_runtime` conversation count now reads per-chat buffers instead of
  deprecated empty list
- **telegram**: Log `original_type` now captures value before reassignment

### Corrections
- **action.py**: Docstring corrected from "Immutable record" to "Mutable lifecycle record"
- **router.py**: Docstring updated — persistence exists (SQLite), not "in-memory only"
- **agent.py**: Comment "Every 6 hours: memory decay" corrected to "Every hour"
- **build/storage.py**: Replaced `assert self._db` with graceful null checks (consistent
  with review/control storage pattern)
- **rag.py**: Docstring corrected — indexes knowledge base .md files, not SQLite memory

### Documentation
- docs/OPERATOR_HANDBOOK.md: "7-layer" corrected to "9-layer"
- docs/DOCS.md: version updated to v1.18.0
- docs/SECURITY_MODEL.md: removed stale TODOs (budget policy and approval inbox are implemented)
- README.md: line count updated from ~17,300 to ~39,000

### Config
- CI performance gate raised from 1000 to 1300 tests
- docker-compose.yml: removed deprecated `version: "3.8"` key

## [1.17.0] — 2026-03-30

Audit-driven quality release: 7 bug fixes, documentation sync, clean Phase 3 foundation.

### Bug Fixes
- **cron**: Fixed month-boundary crash in morning report loop — `replace(day=day+1)`
  replaced with `timedelta(days=1)` to handle month/year rollovers correctly
- **telegram**: Fixed operator precedence bug that made the simple-prompt branch
  unreachable — `not count > 2` corrected to `count <= 2`
- **memory**: `query_facts()` now correctly filters to SEMANTIC+PROCEDURAL types and
  FACT+PROCEDURE kinds as documented (was returning all non-stale memories)
- **telegram**: Removed `unittest.mock.Mock` import from production code — replaced
  with duck typing via `isawaitable` + `hasattr`
- **sandbox**: Fixed `_escape_triple_quotes()` replacement order — backslashes now
  escaped before triple-quotes to prevent double-escaping
- **job_runner**: `cancel()` now actually cancels the running asyncio Task instead of
  only marking the record (prevents zombie tasks)
- **learning**: Moved `_model_failures` from class variable to instance variable to
  prevent shared state across LearningSystem instances

### Documentation
- README: added review/, build/, control/ to module table; updated test count
- SECURITY.md: updated supported versions (v1.17.x), security test count (127)
- CONTRIBUTING.md: updated test count
- docs/BLUEPRINT.md: corrected 7-layer to 9-layer cascade with full layer descriptions
- .gitignore: added `.agent_runtime/` and `agent-test/` exclusions

### Testing
- Added 14 new regression tests covering all 7 bug fixes
- Fixed test_telegram_review.py mock fixture for Mock import removal compatibility

### Verification
- 1371 passed, 4 skipped
- `ruff check` passed
- All security audit tests passed

## [1.16.1] — 2026-03-29

Telegram owner-identity and language-default fix.

### Telegram / Identity / Persona
- Fresh installs no longer inherit a hardcoded owner identity or forced
  Slovak response defaults in runtime prompts and Telegram handling
- Telegram owner messages now keep the real Telegram display name while passing
  explicit owner status through the callback path
- Runtime identity and language behavior now come from deployment config through
  `AGENT_OWNER_NAME`, `AGENT_OWNER_FULL_NAME`, and `AGENT_DEFAULT_LANGUAGE`

### Docs / Deployment
- `.env.example`, `CLAUDE.md`, `JOHN.md`, and bundled owner knowledge now
  describe deployment-specific owner/language configuration instead of shipping
  deployment-specific defaults
- Added regression coverage for runtime persona identity and Telegram owner
  resolution on fresh installs

### Verification
- Full verification passed with `1350 passed, 4 skipped`
- `ruff check .` passed

## [1.16.0] — 2026-03-28

Documented buyer-side Obolos gateway release.

### Gateway / External API
- Gateway now distinguishes handoff-style delivery from documented provider API
  invocation through `external_api_call_v1`
- `obolos.tech` now exposes buyer-side capability routes for marketplace
  catalog listing, wallet balance, and slug-based marketplace API calls behind
  the shared gateway boundary
- CLI now supports generic provider-backed API calls through
  `python -m agent --call-provider-api ...`

### Traces / Artifacts / Denials
- Buyer-side external API calls now retain structured request/response
  artifacts, emit gateway traces, and persist operate-side cost-ledger entries
- HTTP 402 payment-required responses now produce structured denials instead of
  surfacing only raw HTTP failures

### Docs / Strategy
- Strategy docs now describe Obolos more truthfully as a documented buyer-side
  marketplace provider plus legacy handoff compatibility path
- Near-term backlog and progress snapshots now align around the post-Phase-2
  buyer-side gateway slice

## [1.15.0] — 2026-03-28

Phase 2 closure and release-readiness release.

### Builder / Execution
- Builder's bounded local implementation engine now supports deterministic
  `copy_file` and `move_file` operations in addition to the earlier
  write/append/replace/insert/delete/json mutations
- Build capability guardrails now validate both source and target scope for
  file-moving operations instead of only the destination mutation path
- Build delivery and acceptance output now expose implementation-backed
  summaries over changed operations, changed paths, operation types, and
  implementation mode for cleaner operator handoff

### Policy / Gateway / Release Gating
- Builder guardrails, provider receipt handling, provider outcome
  classification, and release-readiness thresholds now live on deterministic
  policy helpers with targeted tests
- Delivery reporting now carries provider outcomes alongside gateway traces and
  receipts instead of flattening everything into raw receipt metadata
- ALS now has a deterministic release-readiness gate through
  `python -m agent --release-readiness ...`, and CI runs the same gate

### Docs / Phase 2 Closure
- Strategy docs now mark Builder v1 as `complete_for_phase` for Phase 2 rather
  than leaving it in a vague near-done state
- Added deployment guidance for controlled local-owner, operator-controlled,
  and enterprise-hardened environments, including gateway/vault config and
  release-readiness workflow
- The next backlog now pivots honestly toward Phase 3 operatorization instead
  of more Phase 2 cleanup slices

### Verification
- Full release verification passed with `1344 passed, 4 skipped`
- Targeted builder/control-plane/gateway/quality regression coverage passed
  with `132 passed`
- `ruff check .` and operator `npm run typecheck` both passed

## [1.14.0] — 2026-03-28

Phase 2 builder-engine and provider-receipt release.

### Builder / Execution
- Builder's bounded local implementation engine now supports richer deterministic
  mutations including insert-before, insert-after, delete-text, and delete-file
  operations instead of stopping at write/append/replace/json_set only
- Build capability guardrails now validate structured operation count,
  operation types, and declared target-file scope before mutable execution
- Build delivery bundles and implementation metadata now surface operation mix
  and stronger execution summaries for operator handoff

### Gateway / Provider Delivery
- `obolos.tech` routes now carry provider-specific request/response semantics,
  including receipt-aware payload shaping and parsed provider receipts on
  successful sends
- Provider fallback now also covers incomplete downstream receipts, not only
  unavailable or retryable-failure endpoints
- Gateway cost and trace records now retain provider receipt metadata for
  later audit and operator inspection

### Strategy / Phase 2
- Strategy docs now mark the bounded local builder engine as materially deeper
  and the external gateway as no longer purely webhook-shaped
- The next backlog now pivots toward semantic acceptance, stronger policy
  unification, and final Phase 2 closure work instead of more gateway basics

### Verification
- Full release verification passed with `1337 passed, 4 skipped`
- Targeted builder/gateway/control-plane suite passed with `122 passed`
- `ruff check .` and operator `npm run typecheck` both passed

## [1.13.0] — 2026-03-28

Phase 2 provider gateway release.

### Gateway / Provider Delivery
- External gateway now models one concrete provider, `obolos.tech`, through
  explicit provider, capability, and route records instead of stopping at a
  generic future-facing contract
- Gateway routing now exposes a provider-ready catalog with route readiness,
  target/auth config posture, and provider-aware metadata through the runtime,
  CLI, and operator report
- Build and review delivery can now send through provider capability routing
  with env/vault-backed auth resolution and fallback to backup routes when the
  primary endpoint is unavailable or returns retryable failures

### Quality / Observability
- Review quality telemetry now records release labels, runtime duration, and
  regression deltas against the previous quality baseline instead of only a
  one-shot golden-case snapshot
- Operator reporting now surfaces gateway catalog readiness and review-quality
  regression posture alongside the existing delivery, approval, and cost views

### Runtime / Configuration
- Runtime model now exposes provider catalogs and route metadata directly,
  making the external gateway story more concrete for Phase 2 planning and
  operator inspection
- Project-root fallback now prefers the checked-out repository root when
  available instead of assuming a home-directory default

### Verification
- Full release verification passed with `1332 passed, 4 skipped`
- `ruff check .` and operator `npm run typecheck` both passed

## [1.12.0] — 2026-03-28

Phase 2 verification hardening release.

### Builder / Verification
- Builder verification discovery now looks deeper into repository signals
  before choosing test, lint, and typecheck commands, including Python config,
  `package.json` scripts, Makefile targets, CI workflow hints, and repo-local
  Node toolchains
- Verification command resolution now prefers repo-native execution surfaces
  instead of falling back too quickly to generic defaults, making builder
  verification more truthful for mixed Python and Node/TypeScript repositories

### Policy / Runtime Boundaries
- Structured denial payloads now cover the remaining major social/API, web,
  tool-execution, and finance-budget edges instead of leaking plain string
  failures
- Runtime policy and model surfaces now expose the first explicit external
  gateway contract plus enterprise-oriented data-handling rules for internal,
  client-safe, and retained-trace packaging

### Reviewer / Quality
- Golden review cases now pin expected clean, secret, and unsafe-pattern repo
  verdicts instead of relying only on smoke-style structure checks
- CI now runs both review-eval smoke and golden suites to catch reviewer
  regressions earlier

### Strategy / Phase 2
- Strategy docs now mark this cycle as a larger Phase 2 verification-hardening
  slice instead of another narrow infra-only increment
- The next backlog is now focused on enforcing the new gateway/runtime
  boundary, measuring review precision, and tightening policy/config
  discipline across the builder path

### Verification
- Full release verification passed with `1318 passed, 4 skipped`
- Local targeted regression coverage passed with `159 passed`

## [1.11.0] — 2026-03-28

Phase 2 structured acceptance release.

### Builder / Acceptance
- Builder acceptance criteria can now carry structured metadata instead of
  relying only on lightweight strings, and CLI/runtime surfaces can load that
  richer shape from JSON
- Deterministic acceptance evaluation now supports structured workspace checks
  for file existence, text presence/absence, JSON-path value matching, and
  required changed paths
- Review-backed and verification-backed acceptance criteria can now use
  explicit metadata such as verification kind or allowed review thresholds

### Operator / Planning
- Unified operator intake now preserves structured acceptance criteria through
  preview, submit, and `to_build_intake()` handoff instead of flattening them
  back to strings
- `JobPlan` now exposes an acceptance summary with required/optional counts,
  structured-criterion counts, and evaluator/kind breakdown before execution
- `python -m agent --build-repo ... --build-acceptance-file acceptance.json`
  and `python -m agent --intake-* --intake-acceptance-file acceptance.json`
  now provide a real CLI path into the richer acceptance slice

### Strategy / Phase 2
- Strategy docs now mark this cycle as a larger Phase 2 structured-acceptance
  slice and move the next backlog toward broader structured denials, golden
  quality cases, and the remaining builder/runtime gaps

### Verification
- Local release verification passed with `1303 passed, 4 skipped`
- Targeted builder/control-plane regression coverage passed with `103 passed`
- `ruff check .` and operator `npm run typecheck` both passed

## [1.10.0] — 2026-03-28

Phase 2 builder execution release.

### Builder / Execution
- Builder now supports a bounded local implementation engine for explicit
  structured workspace mutations, with deterministic `write_file`,
  `append_text`, `replace_text`, and `json_set` operations
- Build jobs now persist implementation mode plus per-operation execution
  results instead of flattening the mutable build step into an audit marker
  only
- Build delivery bundles and persisted product-job metadata now expose the same
  implementation summary for operator handoff and recovery

### Operator / Planning
- Unified operator intake can now carry structured builder implementation
  plans, and planner output now surfaces operation-count-aware scope, risk,
  budget, and build-mode metadata
- `python -m agent --build-repo ... --build-plan-file plan.json` and
  `python -m agent --intake-* --intake-plan-file plan.json` now provide a real
  CLI path into the bounded execution slice

### Strategy / Phase 2
- Strategy docs now mark this slice as a Phase 2 builder-execution step and
  introduce an explicit backlog story for replacing the old placeholder build
  step with a bounded local implementation engine
- The next backlog is now focused back on builder depth: richer acceptance
  structure in planning and stronger deterministic acceptance evaluators

### Verification
- Local release verification passed with `1297 passed, 4 skipped`
- Targeted builder/control-plane regression coverage passed with `97 passed`
- `ruff check .` and operator `npm run typecheck` both passed

## [1.9.1] — 2026-03-28

Phase 2 acceptance clarity release.

### Builder / Acceptance
- Acceptance criteria now support explicit required-vs-optional semantics and
  evaluator hints, with lightweight parsing from operator/CLI strings into a
  richer builder-facing object model
- Builder can now succeed with unmet optional criteria while failing clearly
  on unmet required criteria, instead of flattening all acceptance items into
  the same blocking behavior
- Build acceptance failures now emit structured denial payloads with detailed
  unmet-required-criterion summaries for operator-facing triage

### Builder / Delivery
- Acceptance reports and delivery summaries now expose required/optional
  counts plus blocking-vs-optional unmet criteria alongside the existing
  verification and review evidence

### Strategy / Planning
- Strategy docs now mark this slice as a Phase 2 acceptance-clarity step and
  move the next builder-facing backlog toward richer deterministic evaluators
  and acceptance structure earlier in intake/planning

### Verification
- Local release verification passed with `1290 passed, 4 skipped`
- Targeted builder/control-plane regression coverage passed with `90 passed`

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
