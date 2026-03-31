# Backlog Progress

This file tracks current strategic execution progress against:
- `MASTER_SOURCE_OF_TRUTH.md`
- `THEMES_EPICS_STORIES.md`
- the actual state of `main`

Assessment basis:
- branch: `main` (after the documented buyer-side Obolos API-call slice)
- interpretation date: `2026-03-31`

Important:
- this is a product-and-architecture progress snapshot, not a merge history log
- percentages are directional, not exact velocity math

Status legend:
- `not_started`: no meaningful implementation yet
- `started`: foundations or isolated pieces exist
- `in_progress`: meaningful implementation exists, but key gaps remain
- `mostly_complete`: usable slice exists, but still leaks or drifts
- `complete_for_phase`: sufficiently closed for the current phase

## Overall Snapshot

- Reviewer v1 is `complete_for_phase`: honest execution mode, strict delivery
  gating, full client-safe redaction, artifact metadata recovery, operator
  summary packs, and copy-paste-ready PR comment artifacts.
- **Shared control-plane foundation exists** (`agent/control/models.py`):
  JobKind, JobStatus, ExecutionMode, JobTiming, ExecutionStep, ArtifactKind,
  ArtifactRef, UsageSummary — shared across review and build.
- **Builder bounded context exists** (`agent/build/`): BuildJob, BuildIntake,
  AcceptanceCriteria, AcceptanceVerdict, VerificationResult, BuildArtifact,
  BuildService, BuildStorage, verification loop.
- Builder is workspace-first, syncs repos into managed workspaces, runs
  test/lint/conditional typecheck verification, and fails unknown acceptance
  criteria closed. Builder is now also initialized by the main orchestrator.
- Builder can now be started through the shared runtime via
  `AgentOrchestrator.run_build_job()` and the CLI adapter
  `python -m agent --build-repo ...`.
- Builder can now execute a bounded local implementation engine inside the
  workspace through an explicit structured implementation plan, supporting
  deterministic `write_file`, `append_text`, `replace_text`, and `json_set`
  mutations instead of staying only on an audit-marker placeholder.
- Successful build jobs can now run an optional deterministic post-build review
  pass through `ReviewService` and block completion on review failure.
- Build jobs now persist implementation mode plus per-operation results, and
  those execution details flow into persisted product-job metadata, delivery
  bundles, and operator-facing query surfaces.
- ReviewJob now uses the shared control-plane primitives directly, instead of a
  parallel local lifecycle model.
- Review and build both expose service-level status counters through the
  orchestrator, improving local observability.
- Build, review, task, job-runner, and agent-loop records are now queryable
  through one shared control-plane layer (`JobQueryService` + orchestrator
  list/get methods).
- Build and review artifacts are now queryable and recoverable through one
  shared control-plane layer (`ArtifactQueryService` + orchestrator list/get
  methods), including persisted artifact formats.
- Runtime coexistence rules are now explicit through `RuntimeModelService` and
  the CLI surface `python -m agent --runtime-model`.
- Approval requests are now persisted in SQLite and queryable by
  status/category/job/artifact/workspace/bundle linkage.
- Operator now has a real report/inbox service and CLI surface
  (`python -m agent --report`), plus unified intake qualification/routing for
  build/review requests.
- Operator intake preview/submit now emit a phase-aware `JobPlan` with scope
  signals, risk factors, policy-backed budget envelope, capability
  assignments, planned artifacts, recommended next action, and step-by-step
  planner output.
- Planner output is now persisted as a first-class handoff record, and the
  control plane can list/get plan records through the orchestrator and CLI.
- Planning decisions now emit durable qualification, budget, capability,
  delivery, verification-discovery, and review-policy traces.
- Build and review jobs now sync into shared persisted `ProductJobRecord`
  entries with status, usage, artifact ids, persistence policy metadata, and
  CLI/orchestrator query surfaces.
- Control-plane retention records now track build, review, execution-trace, and
  delivery-bundle outputs with policy ids, expiry timestamps, recoverability,
  and derived active/expired state.
- Retained artifacts now support an explicit prune workflow through the
  control-plane service, orchestrator, and CLI, clearing expired recovery
  snapshots instead of leaving retention as metadata-only state.
- Shared policy primitives now cover job-persistence, artifact-retention, and
  external-gateway defaults in addition to delivery and review-gate policy
  profiles.
- Structured denial payloads now also cover remaining social/API, web, tool
  execution, and finance-budget edges that previously leaked plain strings.
- Per-job usage, token, and cost data now land in a durable control-plane
  ledger for build and review jobs.
- Builder now has a declared capability catalog, resumable checkpoint-based
  execution, deterministic patch + diff capture, and a shared build delivery
  package preview.
- Builder verification now performs repo-aware discovery for test, lint, and
  typecheck surfaces instead of relying only on static defaults.
- Builder verification discovery now also reads package scripts, Makefile
  targets, CI workflow hints, and repo-local Node toolchains before resolving
  the final suite commands.
- Builder execution now resolves explicit build execution policies, records
  policy traces, and blocks unsupported mutable execution sources with stable
  deny-by-default payloads.
- Acceptance criteria now understand richer domain signals, including
  post-build review verdicts plus documentation, target-file, and patch-change
  requirements.
- Acceptance criteria now also support required-vs-optional semantics and
  explicit evaluator hints parsed from operator/CLI input instead of staying
  only implicit inside free-text descriptions.
- CLI build entrypoints and unified operator intake can now load structured
  acceptance criteria from JSON or richer in-memory objects instead of forcing
  the builder surface back through plain strings.
- Planner output now exposes acceptance summary metadata with
  required/optional/structured counts plus evaluator and kind breakdown before
  execution starts, and that richer acceptance structure survives the
  operator-to-builder handoff.
- Deterministic acceptance evaluation now supports structured workspace
  existence/text/JSON checks, structured change-set path/count/docs checks,
  explicit verification-kind targeting, and structured post-build review
  thresholds in addition to the earlier keyword-bound rules.
- Build delivery bundles now carry suite-level plus per-step verification
  artifacts and richer acceptance handoff summaries instead of flattening that
  evidence into one generic report.
- Acceptance failures now emit structured denial payloads with unmet required
  criterion detail instead of only count-based rejection strings.
- Post-build review thresholds are now controlled through explicit deterministic
  review-gate policies instead of a single hard-coded block rule.
- Build delivery now records durable lifecycle state and handoff audit events
  for prepared, awaiting_approval, approved, rejected, and handed_off phases.
- Runtime model now exposes higher-level local, operator-controlled, and
  enterprise-hardened operating environment profiles on top of the lower-level
  execution environment boundaries.
- Runtime model now also exposes a first explicit external gateway contract
  plus internal, client-safe, and retained-trace data-handling rules for
  future enterprise packaging and provider integration work.
- Build and review delivery can now be sent through an explicit external
  gateway boundary via the orchestrator and CLI, with approval-aware gateway
  request, success, and failure events recorded in the shared delivery
  lifecycle.
- External gateway policy now enforces auth, timeout, retry, target-kind,
  scheme, and rate-limit rules at runtime instead of staying only as planning
  metadata.
- External gateway sends now record durable gateway traces plus distinct
  cost-ledger entries per gateway run, and those signals surface in the
  operator report.
- Runtime now also exposes a concrete `obolos.tech` provider catalog with
  explicit capability routes, readiness-aware target/auth resolution from env
  or vault, provider-aware route metadata, and CLI/operator query surfaces for
  gateway configuration posture.
- Build and review delivery can now be sent through provider-backed gateway
  capabilities instead of only direct raw target URLs, and the gateway can now
  fall back between provider routes when one configured endpoint is unavailable
  or returns retryable downstream failures.
- Review quality now has a runtime `ReviewQualityService` that executes the
  deterministic golden cases, records precision/false-positive telemetry into
  the control plane, and surfaces the latest quality snapshot through the
  operator report and CLI.
- Review quality traces now also carry release labels, runtime duration, and
  trend deltas against the previous quality baseline, and the operator report
  surfaces regression posture instead of only a one-shot quality snapshot.
- Project-root fallback no longer assumes `~/agent-life-space` when the checked
  out repository root is available locally, reducing deployment/config drift in
  local and controlled runtime setups.
- Review delivery now converges on the shared `DeliveryPackage` /
  `DeliveryRecord` lifecycle, including prepared/awaiting_approval/approved/
  handed_off state, approval linkage, and explicit handoff after approval.
- Workspace records are now queryable as shared control-plane joins over jobs,
  artifacts, approvals, and delivery bundles.
- Telegram `/review` and the new structured `POST /api/review` endpoint now
  converge through the shared review runtime instead of channel-local review
  paths.
- Review-side repository and diff access now runs under explicit deterministic
  review execution policies, and those policy decisions emit durable
  control-plane traces.
- Unified operator intake now enforces hard-cap and stop-loss budget blocks at
  runtime instead of treating budget only as preview metadata.
- Unified operator intake and CLI build entrypoints can now carry structured
  implementation plans, and planner output now surfaces operation-count-aware
  scope, risk, budget, and build-mode metadata before execution starts.
- Unified operator intake can now request finance or tool approval before
  execution when budget or risk posture requires it.
- Unified operator intake can now acquire a supported `git_url` into a managed
  local mirror before routing build or review execution.
- Runtime approval requests can now require multi-step approval when budget,
  risk, build type, review severity, or delivery scope crosses deterministic
  thresholds.
- Finance budget state now exposes hard-cap, soft-cap, stop-loss, approval,
  warning, and forecast posture, and the operator report now surfaces that
  posture as inbox-visible budget attention.
- Brain-side learning overrides and post-routing escalation now consult budget
  posture before escalating model use.
- Runtime model now exposes explicit environment profiles for review, build,
  acquisition/import, and export-only execution modes.
- Operator report now includes recent plans, traces, deliveries, workspace
  records, persisted product jobs, retained artifacts, and cost-ledger entries
  alongside jobs, approvals, workspace health, and worker execution summaries.
- Control-plane evidence export now assembles persisted jobs, artifacts,
  retained records, traces, approvals, workspaces, costs, runtime model data,
  and artifact traceability into one compliance-friendly package.
- Evidence export now supports a client-safe review mode that reuses review
  redaction while packaging approvals and delivery state for safer operator or
  external handoff.
- Review delivery now emits dedicated operator-summary and copy-paste PR
  comment artifacts, and those artifacts flow into both shared delivery bundles
  and client-safe evidence export.
- Structured denial payloads now cover tool policy blocks, operator intake
  blockers, build/review delivery approval and handoff blockers, and evidence
  export failures, with reporting surfaces reusing those denial details.
- Operator report now surfaces approval backlog status/category counts,
  blocked approval reasons, and retention posture including expired/pruned
  retained-artifact counts.
- Review eval smoke coverage now runs in CI to guard handoff summary artifacts
  and client-safe redaction against regression.
- Review eval golden coverage now also runs in CI to pin clean, secret, and
  unsafe-pattern repo verdicts.
- Operator has a mock-driven TS skeleton with reporting/inbox contracts, but no
  live backend.
- External Gateway now exists as a real runtime boundary with provider-backed
  `obolos.tech` routing, parsed provider receipts, provider-outcome-aware
  delivery reporting, and fallback across configured routes.
- Release readiness now has a deterministic policy gate exposed through the
  runtime, CLI, and CI instead of living only as a planning judgment.
- Controlled-environment deployment guidance now exists for local-owner,
  operator-controlled, and enterprise-hardened Phase 2 setups.
- v1.19.0 (Phase 3 kickoff) adds Telegram operator surface: `/report`,
  `/intake`, and `/build` commands now delegate to unified operator services,
  completing the T4-E4 epic.
- v1.20.0 adds execution policy enrichment for review/verify/deliver planner
  phases, plus `/jobs` and `/deliver` Telegram commands for product job listing
  and delivery management directly from Telegram.
- v1.21.0 adds RuntimeActionRequest and evaluate_runtime_action() for broader
  runtime action policy evaluation (T5-E1-S1), plus a cost accuracy feedback
  loop for validating cost estimates against actual recorded costs (T6-E1-S1).

## Theme Status

| Theme | Status | Approx Progress | Current Truth On `main` | Main Remaining Gap |
|------|--------|-----------------|--------------------------|--------------------|
| T1 Platform Foundation | in_progress | 96% | Shared control-plane primitives now back build and review directly, with explicit runtime coexistence rules plus shared job/artifact queries, persisted plan/trace/delivery records, first-class workspace joins, shared product-job persistence, retention-aware artifact records, explicit retention posture/prune flows, and explicit environment profiles exposed through the orchestrator and CLI. | No unified cross-domain action layer yet. |
| T2 Reviewer Product | complete_for_phase | 96% | Reviewer bounded context, verifier, strict delivery gating, full client-safe redaction, honest execution mode, Telegram `/review`, structured API review entrypoint, shared delivery lifecycle, and reusable handoff summary artifacts now converge through the shared runtime. | LLM analysis and richer external delivery automation are v2 |
| T3 Builder Product | complete_for_phase | 99% | Builder bounded context is tracked on `main`, capability-declared, resumable, orchestrator-wired, CLI-reachable, workspace-synced, repo-aware verification-discovering across Python/Node/Make/CI signals, source-aware execution-policy-gated, includes a bounded local implementation engine with copy/move-aware capability guardrails, implementation-backed acceptance criteria, release-readiness gating, and can hand approved build delivery bundles through the explicit external gateway boundary. | No general code generation and no semantic requirement engine yet |
| T4 Operator Product | in_progress | 99% | Unified intake routing, phase-aware `JobPlan` preview/submit output, persisted planner handoff records, planning traces, runtime budget blocking, managed repo acquisition/import, multi-step approval gating, shared review/build delivery lifecycle state, evidence export, richer operator report service, operation-count-aware builder planning, acceptance-summary-aware planning, explicit gateway handoff actions, provider-outcome-aware delivery reporting with enriched provider detail/retry/filter/report, release-readiness traces, and Telegram operator surface with /report, /intake, /build, /jobs, /deliver, and /telemetry commands now exist. | No live backend/UI yet |
| T5 Security, Governance, And Policy | in_progress | 99% | Tool policy deny-by-default, strict delivery approval, full redaction pipeline, persistent/queryable approval storage with job/artifact/workspace/bundle linkage, deterministic review-gate/delivery/review-execution/build-execution policy profiles, capability-scoped builder guardrails, explicit gateway defaults, provider-aware gateway routing decisions, provider receipt validation, provider-outcome classification, release-readiness thresholds, and structured denial payloads now exist across build/review/tool/web/social/finance-facing blocked flows. | Build and broader runtime execution still do not run under one fully unified enforcement engine |
| T6 Cost, Usage, And Observability | complete_for_phase | 99% | UsageSummary on jobs, a durable per-job control-plane cost ledger, persisted duration/retry/failure telemetry for product jobs, point-in-time `TelemetrySnapshot` records with job throughput/latency/cost/delivery health/system resources, time-window aggregation with trend detection, orchestrator-visible build/review counters, durable planning/delivery/gateway/release/telemetry traces, runtime budget enforcement, budget-aware escalation controls, operator-facing reporting with telemetry summaries, review-eval smoke checks, golden review cases in CI, runtime quality telemetry with release labels, duration, and prior-baseline trend deltas, plus a CLI/CI release-readiness gate and `/telemetry` Telegram command now exist. | No live operator UI or broader longitudinal dashboards yet |
| T7 External Capability Gateway | in_progress | 98% | Runtime model and policy layer now expose explicit gateway defaults, separate delivery and API-call gateway contracts, a concrete `obolos.tech` provider catalog with both buyer-side (catalog, wallet, API call) and seller-side (publish, topup) capability routes, multi-provider resolution (`list_providers_for_capability`, `resolve_capability_across_providers`, `call_api_across_providers` with intelligent fallback), capability-to-providers map in catalog, readiness-aware env/vault config resolution, provider-aware route metadata, provider-specific request payload shaping, parsed provider receipts, and persisted/ledgered external API calls with retained request/response artifacts. | File-upload (multipart) calls, x402 payment flow, and broader downstream provider workflow still remain future scope |
| T8 Enterprise Hardening | in_progress | 99% | Shared control-plane layer, explicit runtime coexistence rules, review + build bounded contexts on shared primitives, persisted job/plan/delivery state, retention-aware artifact records with prune flow, lower-level execution environment profiles plus higher-level local/operator/enterprise operating profiles, managed acquisition, client-safe evidence export, shared job/artifact/query/reporting surfaces, deterministic review/build execution policy boundaries, explicit gateway runtime boundaries, provider configuration posture, enterprise-facing data-handling rules, and 22 architecture invariant enforcement tests (import graph boundaries, execution mode contracts, gateway boundary, cross-domain isolation, shared control-plane contracts, multi-provider contracts) now exist. | Extraction-grade enforcement is now test-backed; automated enforcement in CI remains optional. |

## Epic Snapshot

### T1 Platform Foundation

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T1-E1 Canonical Job Model | in_progress | 93% | Shared JobKind, JobStatus, JobTiming, ExecutionStep, UsageSummary, normalized job queries, explicit runtime coexistence rules, and persisted cross-system `ProductJobRecord` metadata now cover build, review, and operate-adjacent inspection surfaces. | The rules and records are explicit, but not yet enforced by stronger invariants or broader action-layer convergence. |
| T1-E2 Artifact-First Execution | mostly_complete | 98% | ArtifactKind shared. ReviewArtifact and BuildArtifact both produce typed artifacts, shared artifact query/recovery spans build and review, builder emits deterministic patch + diff outputs for delivery packaging, and retention records now track policy/expiry/recoverability across build, review, trace, and delivery outputs with explicit prune support. | No automated retention scheduler or broader archival/compaction workflow yet. |
| T1-E3 Workspace And Execution Discipline | in_progress | 87% | Builder is workspace-first, syncs repos into workspaces, persists workspace audit state, exposes workspace records as shared control-plane joins over jobs/artifacts/approvals/bundles, and now publishes explicit environment profiles for review, build, acquisition, and export flows while reviewer stays explicitly READ_ONLY_HOST. | Shared cross-domain workspace policy and reviewer workspace execution are still partial. |

### T2 Reviewer Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T2-E1 Review Job Types | mostly_complete | 80% | repo_audit, pr_review, release_review | PR/release still v1-grade |
| T2-E2 Review Output Standardization | complete_for_phase | 90% | Canonical report, severity, Markdown, JSON | None for v1 |
| T2-E3 Review Verification | mostly_complete | 90% | Verifier exists and tested, smoke plus golden review-eval suites run in CI for reviewer handoff and verdict regression, and runtime quality evaluation now records verdict/count/title precision plus false-positive and false-negative telemetry into the control plane. | No version-over-version quality trend or latency tracking yet |
| T2-E4 Review Delivery | complete_for_phase | 96% | Strict approval, full redaction, shared Telegram/API review entrypoints, shared delivery lifecycle records, explicit post-approval handoff, and copy-paste-ready handoff summary artifacts now exist | Richer external delivery automation remains v2 |

### T3 Builder Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T3-E1 Capability-Based Build Execution | complete_for_phase | 98% | BuildJob, BuildIntake, BuildService, BuildStorage, workspace sync, capability catalog, orchestrator runtime entrypoint, CLI build entrypoint, resumable checkpoints, deterministic patch/diff capture, a bounded local implementation engine with copy/move-aware capability guardrails, and build delivery package preview now exist on `main`. | No freeform or LLM code generation yet; bounded execution still depends on an explicit structured plan |
| T3-E2 Build Verification Loop | in_progress | 96% | Verification suite now discovers test/lint/typecheck surfaces from repo signals, package scripts, Make targets, CI workflow hints, and repo-local toolchains in the workspace, persists suite-level plus per-step verification artifacts, and exposes that evidence through the build delivery bundle. Successful jobs can invoke deterministic post-build review before completion, and acceptance failures now produce structured operator-facing denial detail. | Discovery is deeper and more honest, but it is still deterministic rather than a full language-specific execution planner, and reviewer still runs in READ_ONLY_HOST mode over the built workspace path. |
| T3-E3 Acceptance Criteria Engine | complete_for_phase | 96% | Acceptance criteria now support typed states, required-vs-optional semantics, explicit evaluator hints, structured metadata, keyword-bound verification checks, explicit `verify:` commands, review-backed security checks, workspace text/JSON checks, change-set path/count/docs checks, delivery-usable acceptance reports with structured handoff summaries, and implementation-backed acceptance summaries over changed operations, paths, and modes. | No semantic requirement engine beyond deterministic rule-based evaluators. |

### T4 Operator Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T4-E1 Intake And Qualification | in_progress | 95% | Unified operator intake now resolves scope size/signals, risk factors, policy-backed budget envelopes, runtime budget blocks, approval-gated execution, managed repo acquisition/import, review/build routing, structured builder implementation plans, and structured acceptance criteria, plus CLI preview/submit surfaces and persisted handoff records. | Cost estimates are still heuristic and there is no live operator UI |
| T4-E2 Job Planning And Routing | in_progress | 87% | `JobPlan` now includes explicit phases, capability assignments, structured budget metadata, persisted planner handoff records, durable planning traces, builder operation-count-aware planning metadata, and acceptance summaries with required/optional/structured breakdown. | Planner output is durable, but not yet a distributed execution history or live backend workflow. |
| T4-E3 Delivery Workflow | complete_for_phase | 99% | Shared `DeliveryPackage`/`DeliveryRecord` now back both build and review delivery previews, approval linkage, lifecycle refresh, explicit handoff audit events, approval-gated gateway send actions, provider-outcome-aware delivery summaries, enriched `/deliver` detail with provider outcome/receipt/attention, `/deliver retry` for failed deliveries, `/deliver pending\|failed\|delivered` outcome filters, and `/report delivery` provider summary through the orchestrator, CLI, Telegram, and operator report. | No live operator workflow or broader delivery automation yet. |
| T4-E4 Operator Telegram Surface | complete_for_phase | 100% | `/report`, `/intake`, `/build`, `/jobs`, and `/deliver` Telegram commands now delegate to unified operator services, providing overview/inbox/budget reporting, qualify-plan-execute intake flow, product job listing/detail, and delivery listing/detail/gateway send directly from Telegram (v1.19.0, v1.20.0). | Richer interactive Telegram flows and live UI remain future scope. |

### T5 Security, Governance, And Policy

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T5-E1 Policy Control Plane | in_progress | 99% | Tool policy deny-by-default now sits alongside deterministic build review-gate, delivery, review-execution, source-aware build-execution policy profiles, capability-scoped builder guardrails, shared job-persistence, artifact-retention, external-gateway policy models, explicit provider receipt validation, provider-outcome classification, deterministic release-readiness thresholds, RuntimeActionRequest and evaluate_runtime_action() for broader runtime action policy, and structured denial payloads across tool, web, social/API, finance-budget, build, review, export, and reporting flows. | Full cross-domain enforcement engine remains separate scope. |
| T5-E2 Approval Model | in_progress | 90% | Approval queue, strict delivery approval, persistent ApprovalStorage, and query filters now cover job/artifact/workspace/bundle linkage across review and build delivery flows, and unified intake/build/review delivery can now require multi-step approval for budget-sensitive, high-risk, or higher-severity work. | Broader policy/action unification and richer approval chains remain partial. |
| T5-E3 Client-Safe Output | mostly_complete | 70% | Full redaction pipeline, requester/source stripped | Wider system outputs beyond reviewer |

### T6 Cost, Usage, And Observability

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T6-E1 Cost Ledger | in_progress | 86% | Per-job usage, token, and cost entries now persist into the shared control-plane ledger for build and review jobs, finance budget state exposes hard/soft/stop-loss posture plus warnings/forecast, unified intake enforces runtime budget blocks, brain-side escalation is budget-aware, cost accuracy feedback loop validates estimates against actuals, and the operator report surfaces budget posture, approval caps, and margin hints. | No live operator UI yet |
| T6-E2 Runtime Observability | mostly_complete | 97% | Status and traces exist locally, including orchestrator-visible build/review counters, persisted plan/trace/delivery telemetry, shared job/artifact/workspace queries, approval backlog visibility with blocked reasons, operator-facing report output, point-in-time `TelemetrySnapshot` records capturing job throughput/latency percentiles/cost/delivery health/system resources, time-window aggregation with trend detection (stable/improving/degrading), `/telemetry [hours]` Telegram command, telemetry in operator report, plus workspace health, worker execution, persisted job, retention posture, cost, and product-job duration/retry/failure summaries. | No live UI or push updates yet |
| T6-E3 Quality Evals | mostly_complete | 99% | Tests are strong, review-eval smoke coverage guards handoff artifacts and client-safe redaction in CI, durable golden review verdict cases pin clean, secret, and unsafe-repo outcomes, runtime quality evaluation now records precision, false-positive/false-negative counts, release labels, duration, and regression deltas against the previous baseline into the control plane and operator report, and the same telemetry now drives a deterministic CLI/CI release-readiness gate. | No live longitudinal dashboard or broader non-review latency trend surface yet |

### T7 External Capability Gateway

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T7-E1 Gateway Foundation | mostly_complete | 96% | Runtime model and policy now expose separate webhook-handoff and external API-call contracts plus dedicated gateway policies, and the runtime now executes approval- or policy-gated external sends with auth, timeout, retry, rate-limit, denial, trace, cost-record handling, retained request/response artifacts, and provider receipt validation through one explicit boundary. | The boundary is stronger, but it is not yet multi-provider or backed by richer operator workflow |
| T7-E2 obolos.tech Integration | in_progress | 96% | `obolos.tech` is now represented through explicit provider and route records for both the earlier handoff-compatible delivery path and the documented buyer-side marketplace catalog, wallet-balance, and slug-based API-call flow, with CLI/runtime access plus targeted routing/error-mode test coverage. | Integration is still one-provider, and seller-side publishing, file-upload calls, x402 payment flow, and broader downstream workflow/telemetry remain future scope |

### T8 Enterprise Hardening

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T8-E1 Contract-First Boundaries | in_progress | 79% | Shared control-plane primitives now back build and review directly. ADR-001 sidecar, unified intake/planning, explicit runtime coexistence rules, cross-system job/artifact/query/report surfaces, and deterministic review execution policy boundaries reinforce the boundary. | Boundaries are documented and queryable, but not yet enforced as extraction-grade invariants. |
| T8-E2 Deployment And Environment Profiles | in_progress | 90% | Explicit lower-level execution environment profiles now define review, build, acquisition/import, and export-only boundaries, the runtime model now also exposes local-owner, operator-controlled, and enterprise-hardened operating profiles with default build/delivery/gateway posture, and controlled-environment deployment documentation now explains how to run those profiles in practice. | The higher-level profile matrix now exists, but it is not yet enforced as a deployment-grade runtime contract across the whole stack |
| T8-E3 Compliance-Friendly Foundations | in_progress | 90% | Redaction module, client-safe review bundle export, client-safe evidence export, delivery gating, retained artifact records with expiry/recoverability/prune state, shared artifact traceability, dedicated evidence export workflow, and explicit internal/client-safe/retained-trace data-handling rules now exist. | Broader non-review client-safe packaging and stronger enforcement of those rules remain partial. |

## Current Strategic Interpretation

`main` is now best described as:
- a reviewer + builder foundation modular monolith
- with shared control-plane primitives
- with meaningful governance and workspace foundations
- with the builder runtime now actually wired into the orchestrator, capability-declared, resumable, reachable through real entrypoints, tracked on `main`, able to emit deterministic patch/diff outputs, and able to execute bounded local workspace mutations from a structured implementation plan
- with shared control-plane query/report layers for build, review, operate, and artifacts
- with unified operator intake qualification, planning, and routing for build and review requests, now including structured builder implementation plans, structured acceptance criteria, and acceptance-summary-aware planning signals
- with a shared delivery-package model plus build and review delivery previews, approval linkage, and handoff audit state
- with managed git-source acquisition/import before runtime routing
- with retention-aware artifact and persisted product-job state now feeding both internal and client-safe evidence export packages
- with explicit execution environment profiles plus a higher-level local/operator/enterprise operating profile matrix and budget-aware escalation controls around runtime execution
- with build delivery bundles that now surface richer verification and acceptance evidence for operator handoff
- with implementation-backed acceptance summaries and a deterministic
  release-readiness gate surfaced through the runtime, CLI, and CI
- with explicit required/optional acceptance semantics, structured acceptance metadata, richer deterministic evaluator coverage, and clearer builder failure payloads for operator-facing rejection states
- with builder verification discovery that now understands package scripts,
  Make targets, CI workflow hints, Python config, and repo-local Node/Python
  toolchains instead of relying on one narrow heuristic path
- with structured denial payloads now reaching finance-budget, social/API, web,
  and tool-execution failure edges as well as product-job blockers
- with durable golden review verdict cases and CI gating around clean, secret,
  and unsafe-pattern repositories
- with an explicit external gateway boundary that now enforces auth, timeout,
  retry, rate-limit, approval, trace, and cost behavior for build/review
  delivery sends
- with a concrete provider-ready gateway catalog for `obolos.tech`, including
  route readiness, env/vault-backed auth resolution, and fallback-capable
  provider sends for build/review delivery handoff
- with provider-outcome-aware delivery reporting and controlled-environment
  deployment guidance for local-owner, operator-controlled, and
  enterprise-hardened setups
- with runtime review-quality telemetry over deterministic golden cases,
  now carrying release labels, duration, and previous-baseline regression
  posture through the control plane and operator report
- but with builder execution still bounded to explicit structured local operations rather than general code generation
- and with acceptance still deterministic/rule-based rather than semantic
- and without a live operator UI or a fully unified build/review execution-policy engine

Reviewer v1: `complete_for_phase` and practically closed for Phase 1
Builder v1: `complete_for_phase` and practically closed for Phase 2

Reviewer v1 closed scope includes:
- recovery-safe job storage with from_dict() reconstruction and artifact metadata hydration
- explicit READ_ONLY_HOST execution mode for v1 reviewer flows
- strict delivery approval before external send paths
- full client-safe redaction for requester, source, paths, hostnames, secrets, and traces
- real git-backed PR review coverage and ReviewService-based Telegram routing
- copy-paste-ready PR comment and operator-summary handoff artifacts
- CI smoke checks for reviewer handoff artifacts and client-safe redaction

Remaining Reviewer v2 gaps:
- LLM-augmented analysis
- workspace-bound reviewer execution
- external delivery send workflow
- stronger cross-runtime policy unification

## Code Review And Fixes Applied On 2026-03-28

Review/audit-driven fixes landed on `main`:
- Builder runtime is no longer hidden behind `.gitignore`; `agent/build/` is tracked on `main`.
- Builder can now execute a bounded local implementation engine over an
  explicit structured implementation plan instead of staying only on the old
  audit-marker placeholder path.
- The CLI and unified operator intake can now carry structured implementation
  plans, and planner output now surfaces operation counts and build mode
  before execution starts.
- Build jobs now persist implementation mode plus per-operation results, and
  delivery bundles include the same implementation summary for operator
  handoff.
- Build jobs now sync the requested repo into the managed workspace before verification.
- Builder verification now includes conditional typecheck when project config is present.
- Acceptance criteria no longer auto-pass blindly; unknown criteria fail closed and `verify:` commands run in the workspace.
- Review job recovery now preserves `include_patterns` and `exclude_patterns`.
- `AgentOrchestrator` now initializes builder storage/service explicitly and exposes build/review counters in status output.
- `AgentOrchestrator` now exposes shared builder/reviewer runtime entrypoints plus cross-system list/get job queries.
- Builder can now run deterministic post-build review and block completion on review failure when that path is requested.
- `python -m agent --build-repo ...` now provides a thin real builder product entrypoint.
- `ReviewJob` now uses shared control-plane primitives directly.
- Shared job queries now cover `Task`, `JobRunner`, and `AgentLoop`.
- Build and review artifacts are now queryable and recoverable through one
  shared control-plane layer (`ArtifactQueryService` + orchestrator list/get
  methods), including persisted artifact formats.
- Runtime coexistence rules are now explicit through `RuntimeModelService` and
  `python -m agent --runtime-model`.
- Approval requests are now persisted and queryable with job/artifact/workspace/bundle linkage.
- Builder now has a declared capability catalog and resumable checkpoints with CLI resume support.
- Unified operator intake and operator-facing report/inbox surfaces now exist in the runtime and CLI.
- Unified operator intake now emits phase-aware `JobPlan` preview/submit
  outputs with scope signals, risk factors, policy-backed budget envelope,
  capability assignments, steps, planned artifacts, and recommended next
  action.
- Builder now emits deterministic patch + diff artifacts instead of only
  placeholder diff metadata.
- Builder delivery now has a shared `DeliveryPackage` preview and approval gate
  with explicit workspace + bundle linkage.
- Planner output is now persisted as durable handoff state, with list/get
  surfaces for plan records and explicit plan IDs.
- Planner decisions now emit durable qualification, budget, capability,
  delivery, verification-discovery, and review-policy traces.
- Build and review jobs now sync into shared persisted product-job records with
  cross-system metadata, artifact ids, and policy context.
- Control-plane retention now covers artifacts and delivery bundles with policy
  ids, recoverability, and expiry status.
- Shared policy primitives now cover job persistence, artifact retention, and
  external gateway defaults in addition to delivery/review gating.
- Per-job usage, token, and cost data now land in a durable control-plane
  ledger and operator report surfaces.
- Workspace records are now queryable as first-class control-plane joins across
  jobs, artifacts, approvals, and delivery bundles.
- Build delivery now records lifecycle status plus handoff audit events after
  prepare, approval request, approval refresh, rejection, and handoff.
- Builder verification now performs repo-aware discovery for test, lint, and
  typecheck surfaces.
- Builder verification discovery now also inspects package scripts, Makefile
  targets, CI workflow hints, and repo-local Node toolchains before resolving
  commands.
- Post-build review thresholds are now configurable through deterministic
  review-gate policy profiles.
- Acceptance evaluation now supports review-backed security checks plus
  change-set-aware docs and target-file requirements.
- Operator report now includes recent plans, traces, deliveries, workspace
  records, artifacts, approvals, workspace health, and worker execution state.
- Telegram `/review` and the new structured `POST /api/review` endpoint now
  both route through the shared review runtime instead of channel-local review
  logic.
- Review-side repository and diff access now uses explicit deterministic
  review-execution policy profiles with durable policy traces.
- Unified operator intake now blocks hard-cap and stop-loss budget cases at
  runtime and can request finance or tool approval before execution starts.
- Finance budget state and operator reporting now surface budget posture,
  warnings, stop-loss state, and budget-attention inbox items.
- Unified operator intake can now acquire supported git sources into a managed
  local mirror before review/build routing.
- `EvidenceExportService` and `python -m agent --export-evidence-job ...` now
  assemble persisted jobs, artifacts, retention records, traces, approvals,
  workspaces, costs, runtime model data, and artifact traceability.
- Persisted product-job records now track duration, retry count, and failure
  count, and the operator report summarizes those signals directly.
- Runtime model now publishes explicit environment profiles for review, build,
  acquisition/import, and export-only flows.
- Unified intake plus build/review delivery approvals can now request
  multi-step approval where deterministic thresholds require it.
- Brain-side learning override and post-routing escalation are now blocked when
  budget posture disallows further model escalation.
- Review delivery now emits dedicated operator-summary and copy-paste-ready PR
  comment artifacts and carries them through shared delivery bundles.
- Structured denial payloads now cover core tool/intake/build/review/export
  blockers, and reporting now reuses those denial details for operator-visible
  job attention.
- Structured denial payloads now also cover remaining finance-budget, social
  API, web-access, and tool-execution failure edges.
- Review eval smoke coverage now validates reviewer handoff artifacts and
  client-safe redaction in CI.
- Review eval golden coverage now pins clean, secret, and unsafe-pattern repo
  verdicts in CI instead of relying only on structural smoke tests.
- Runtime model now exposes the first explicit external gateway contract plus
  concrete data-handling rules for internal evidence, client-safe handoff, and
  retained operational traces.
- External gateway delivery now runs through `ExternalGatewayService`, with
  explicit auth, timeout, retry, scheme, target-kind, approval, trace, and
  rate-limit enforcement instead of leaving those concerns only in policy
  metadata.
- Build and review delivery can now be sent through the gateway from the
  shared orchestrator and CLI surfaces, with gateway request/success/failure
  events appended to the persisted delivery lifecycle.
- External gateway cost records now use unique per-run ids instead of
  overwriting one generic per-job usage snapshot.
- Delivery lifecycle refresh now preserves the terminal `handed_off` state
  instead of degrading it back to `approved` after approval execution.
- Review quality now has a runtime `ReviewQualityService`, shared golden-case
  fixtures, and operator-visible precision telemetry for verdicts, counts, and
  expected finding titles.
- Golden review exact-match scoring now includes expected finding-title matches
  instead of only verdict and count alignment.
- Gateway retry reporting now records the actual number of attempts instead of
  always reporting the configured maximum.
- Gateway policy/runtime now expose concrete `obolos.tech` providers and
  capability routes, with provider-aware readiness surfaced through CLI and
  operator reporting instead of leaving provider integration as a future-only
  contract note.
- Provider-backed gateway sends can now resolve target/auth from env or vault,
  fall back between configured routes, and record provider/route metadata in
  traces and cost entries.
- Review quality telemetry now compares each run to the previous baseline,
  capturing release label, duration, quality deltas, and regression posture.
- Project-root resolution now prefers the checked-out repository root when
  available instead of falling back too eagerly to a home-directory default.

## Highest-Leverage Next Steps

See [NEXT_BACKLOG.md](/Users/danielbabjak/Desktop/Agent_Life_Space/docs/strategy/NEXT_BACKLOG.md) for the prioritized execution queue.

Now that Builder v1 is effectively closed for Phase 2, the next high-leverage
work is Phase 3 operatorization:
1. Bind review, verify, and deliver planner phases to stronger runtime capabilities
2. Finish provider-specific operator delivery workflow beyond report/CLI surfaces
3. Push policy toward one broader runtime action boundary
4. Deepen persisted telemetry, cost feedback, and operator-facing runtime history
