# Backlog Progress

This file tracks current strategic execution progress against:
- `MASTER_SOURCE_OF_TRUTH.md`
- `THEMES_EPICS_STORIES.md`
- the actual state of `main`

Assessment basis:
- branch: `main` (after Review Runtime Convergence + Budget Governance slice)
- interpretation date: `2026-03-27`

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
  gating, full client-safe redaction, artifact metadata recovery.
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
- Successful build jobs can now run an optional deterministic post-build review
  pass through `ReviewService` and block completion on review failure.
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
- Shared policy primitives now cover job-persistence, artifact-retention, and
  external-gateway defaults in addition to delivery and review-gate policy
  profiles.
- Per-job usage, token, and cost data now land in a durable control-plane
  ledger for build and review jobs.
- Builder now has a declared capability catalog, resumable checkpoint-based
  execution, deterministic patch + diff capture, and a shared build delivery
  package preview.
- Builder verification now performs repo-aware discovery for test, lint, and
  typecheck surfaces instead of relying only on static defaults.
- Acceptance criteria now understand richer domain signals, including
  post-build review verdicts plus documentation, target-file, and patch-change
  requirements.
- Post-build review thresholds are now controlled through explicit deterministic
  review-gate policies instead of a single hard-coded block rule.
- Build delivery now records durable lifecycle state and handoff audit events
  for prepared, awaiting_approval, approved, rejected, and handed_off phases.
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
- Unified operator intake can now request finance or tool approval before
  execution when budget or risk posture requires it.
- Finance budget state now exposes hard-cap, soft-cap, stop-loss, approval,
  warning, and forecast posture, and the operator report now surfaces that
  posture as inbox-visible budget attention.
- Operator report now includes recent plans, traces, deliveries, workspace
  records, persisted product jobs, retained artifacts, and cost-ledger entries
  alongside jobs, approvals, workspace health, and worker execution summaries.
- Operator has a mock-driven TS skeleton with reporting/inbox contracts, but no
  live backend.
- External Gateway and most enterprise-hardening work are still ahead.

## Theme Status

| Theme | Status | Approx Progress | Current Truth On `main` | Main Remaining Gap |
|------|--------|-----------------|--------------------------|--------------------|
| T1 Platform Foundation | in_progress | 93% | Shared control-plane primitives now back build and review directly, with explicit runtime coexistence rules plus shared job/artifact queries, persisted plan/trace/delivery records, first-class workspace joins, shared product-job persistence, and retention-aware artifact records exposed through the orchestrator and CLI. | No unified cross-domain action layer yet. |
| T2 Reviewer Product | complete_for_phase | 88% | Reviewer bounded context, verifier, strict delivery gating, full client-safe redaction, honest execution mode, Telegram `/review`, and structured API review entrypoint now converge through the shared runtime. | LLM analysis and PR comment packs are v2 |
| T3 Builder Product | in_progress | 80% | Builder bounded context is tracked on `main`, capability-declared, resumable, orchestrator-wired, CLI-reachable, workspace-synced, repo-aware verification-discovering, policy-gated after review, and now emits deterministic patch/diff artifacts plus persisted delivery lifecycle state. | Build step is still placeholder-grade. No LLM implementation or external delivery send path |
| T4 Operator Product | in_progress | 82% | Unified intake routing, phase-aware `JobPlan` preview/submit output, persisted planner handoff records, planning traces, runtime budget blocking, pre-execution approval gating, workspace joins, expanded CLI query surfaces, and richer operator report service now exist, including persisted jobs, retained artifacts, cost-ledger reporting, and budget posture. | No live backend/UI, no review delivery migration onto the shared lifecycle, and no remote acquisition path |
| T5 Security, Governance, And Policy | in_progress | 83% | Tool policy deny-by-default, strict delivery approval, full redaction pipeline, persistent/queryable approval storage with job/artifact/workspace/bundle linkage, deterministic review-gate/delivery/review-execution policy profiles, plus runtime risky-execution approval gating for operator intake. | Build and broader runtime execution still do not run under one fully unified enforcement engine |
| T6 Cost, Usage, And Observability | in_progress | 81% | UsageSummary on jobs, a durable per-job control-plane cost ledger, orchestrator-visible build/review counters, durable planning/delivery traces, shared cross-system job/artifact queries, workspace joins, approval backlog visibility, runtime budget enforcement, and operator-facing reporting over plans/traces/deliveries/workspaces/persisted jobs/retention now exist. | No live operator UI, budget-aware escalation layer, or deeper cross-runtime cost telemetry |
| T7 External Capability Gateway | not_started | 0% | Nothing | No gateway contract |
| T8 Enterprise Hardening | in_progress | 79% | Shared control-plane layer, explicit runtime coexistence rules, review + build bounded contexts on shared primitives, persisted job/plan/delivery state, retention-aware artifact records, workspace joins, unified intake/planning, resumable builder runtime, shared job/artifact/query/reporting surfaces, and deterministic review execution policy boundaries now exist. | Runtime boundaries are clearer, but not yet enforced as extraction-grade invariants across the whole execution stack. |

## Epic Snapshot

### T1 Platform Foundation

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T1-E1 Canonical Job Model | in_progress | 93% | Shared JobKind, JobStatus, JobTiming, ExecutionStep, UsageSummary, normalized job queries, explicit runtime coexistence rules, and persisted cross-system `ProductJobRecord` metadata now cover build, review, and operate-adjacent inspection surfaces. | The rules and records are explicit, but not yet enforced by stronger invariants or broader action-layer convergence. |
| T1-E2 Artifact-First Execution | mostly_complete | 95% | ArtifactKind shared. ReviewArtifact and BuildArtifact both produce typed artifacts, shared artifact query/recovery spans build and review, builder emits deterministic patch + diff outputs for delivery packaging, and retention records now track policy/expiry/recoverability across build, review, trace, and delivery outputs. | Retention rules exist, but no pruning scheduler or policy-driven deletion workflow yet. |
| T1-E3 Workspace And Execution Discipline | in_progress | 82% | Builder is workspace-first, syncs repos into workspaces, persists workspace audit state, and now exposes workspace records as shared control-plane joins over jobs, artifacts, approvals, and delivery bundles while reviewer stays explicitly READ_ONLY_HOST. | Shared cross-domain workspace policy and reviewer workspace execution are still partial. |

### T2 Reviewer Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T2-E1 Review Job Types | mostly_complete | 80% | repo_audit, pr_review, release_review | PR/release still v1-grade |
| T2-E2 Review Output Standardization | complete_for_phase | 90% | Canonical report, severity, Markdown, JSON | None for v1 |
| T2-E3 Review Verification | mostly_complete | 75% | Verifier exists and tested | No golden-eval-backed quality regime |
| T2-E4 Review Delivery | mostly_complete | 88% | Strict approval, full redaction, and shared Telegram/API review entrypoints now exist | PR comment packs and richer external delivery remain v2 |

### T3 Builder Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T3-E1 Capability-Based Build Execution | in_progress | 84% | BuildJob, BuildIntake, BuildService, BuildStorage, workspace sync, capability catalog, orchestrator runtime entrypoint, CLI build entrypoint, resumable checkpoints, deterministic patch/diff capture, and build delivery package preview now exist on `main`. | Build step is placeholder (no real code generation). |
| T3-E2 Build Verification Loop | in_progress | 84% | Verification suite now discovers test/lint/typecheck surfaces from repo signals in the workspace. Successful jobs can invoke deterministic post-build review before completion, and review findings now flow through explicit review-gate policy profiles. | Discovery remains heuristic, and reviewer still runs in READ_ONLY_HOST mode over the built workspace path. |
| T3-E3 Acceptance Criteria Engine | in_progress | 70% | Acceptance criteria support typed states, keyword-bound verification checks, explicit `verify:` commands, review-backed security checks, and change-set-aware docs/target-file evaluators. | No semantic requirement engine beyond deterministic rule-based evaluators. |

### T4 Operator Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T4-E1 Intake And Qualification | in_progress | 86% | Unified operator intake now resolves scope size/signals, risk factors, policy-backed budget envelopes, runtime budget blocks, approval-gated execution, and review/build routing, plus CLI preview/submit surfaces and persisted handoff records. | Cost estimates are still heuristic, git_url remains blocked, and there is no live operator UI |
| T4-E2 Job Planning And Routing | in_progress | 82% | `JobPlan` now includes explicit phases, capability assignments, structured budget metadata, persisted planner handoff records, and durable planning traces through the shared control-plane store. | Planner output is durable, but not yet a distributed execution history or live backend workflow. |
| T4-E3 Delivery Workflow | in_progress | 72% | Shared `DeliveryPackage` model now exists, builder assembles delivery previews, requests approval, persists lifecycle state, records delivery events, and supports explicit handoff after approval. | Review delivery remains outside the shared lifecycle and there is no live operator workflow. |

### T5 Security, Governance, And Policy

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T5-E1 Policy Control Plane | in_progress | 82% | Tool policy deny-by-default now sits alongside deterministic build review-gate, delivery, and review-execution policy profiles, plus shared job-persistence, artifact-retention, and external-gateway policy models surfaced through persistence, artifact, review, and reporting flows. | Build and broader runtime execution are still not governed by one shared enforcement engine. |
| T5-E2 Approval Model | in_progress | 86% | Approval queue, strict delivery approval, persistent ApprovalStorage, and query filters now cover job/artifact/workspace/bundle linkage across review and build delivery flows, and unified intake can now request finance/tool approval for budget-sensitive or high-risk execution before runtime starts. | Broader policy/action unification and multi-step execution workflows remain partial. |
| T5-E3 Client-Safe Output | mostly_complete | 70% | Full redaction pipeline, requester/source stripped | Wider system outputs beyond reviewer |

### T6 Cost, Usage, And Observability

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T6-E1 Cost Ledger | in_progress | 76% | Per-job usage, token, and cost entries now persist into the shared control-plane ledger for build and review jobs, finance budget state now exposes hard/soft/stop-loss posture plus warnings/forecast, unified intake enforces runtime budget blocks, and the operator report now surfaces budget posture and margin hints. | No budget-aware escalation layer or live operator UI yet |
| T6-E2 Runtime Observability | in_progress | 86% | Status and traces exist locally, including orchestrator-visible build/review counters, persisted plan/trace/delivery telemetry, shared job/artifact/workspace queries, approval backlog visibility, operator-facing report output, plus workspace health, worker execution, persisted job, retention, and cost summaries. | No live UI, push updates, or deeper cross-runtime telemetry yet |
| T6-E3 Quality Evals | started | 20% | Tests are strong | No product-quality eval discipline |

### T7 External Capability Gateway

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T7-E1 Gateway Foundation | not_started | 0% | Nothing | No gateway contract |
| T7-E2 obolos.tech Integration | not_started | 0% | Nothing | No provider integration |

### T8 Enterprise Hardening

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T8-E1 Contract-First Boundaries | in_progress | 79% | Shared control-plane primitives now back build and review directly. ADR-001 sidecar, unified intake/planning, explicit runtime coexistence rules, cross-system job/artifact/query/report surfaces, and deterministic review execution policy boundaries reinforce the boundary. | Boundaries are documented and queryable, but not yet enforced as extraction-grade invariants. |
| T8-E2 Deployment And Environment Profiles | started | 20% | Project-root discipline, sandbox defaults | No explicit environment profile matrix |
| T8-E3 Compliance-Friendly Foundations | in_progress | 50% | Redaction module, client-safe export, delivery gating, retained artifact records with expiry/recoverability, and shared artifact traceability through control-plane/reporting surfaces now exist. | Evidence packaging, export workflow, and enterprise data-handling rules remain partial. |

## Current Strategic Interpretation

`main` is now best described as:
- a reviewer + builder foundation modular monolith
- with shared control-plane primitives
- with meaningful governance and workspace foundations
- with the builder runtime now actually wired into the orchestrator, capability-declared, resumable, reachable through real entrypoints, tracked on `main`, and able to emit deterministic patch/diff outputs
- with shared control-plane query/report layers for build, review, operate, and artifacts
- with unified operator intake qualification, planning, and routing for build and review requests
- with a first shared delivery-package model plus build delivery previews and approval linkage
- with retention-aware artifact and persisted product-job state starting to make compliance/audit export realistic
- but without full builder capability (build step is placeholder)
- and without a live operator UI or a fully unified build/review execution-policy engine

Reviewer v1: `complete_for_phase`
Builder v1: `in_progress` (foundation-grade)

Reviewer v1 closed scope includes:
- recovery-safe job storage with from_dict() reconstruction and artifact metadata hydration
- explicit READ_ONLY_HOST execution mode for v1 reviewer flows
- strict delivery approval before external send paths
- full client-safe redaction for requester, source, paths, hostnames, secrets, and traces
- real git-backed PR review coverage and ReviewService-based Telegram routing

Remaining Reviewer v2 gaps:
- LLM-augmented analysis
- workspace-bound reviewer execution
- external delivery send workflow
- PR comment packaging and stronger cross-runtime policy unification

## Code Review And Fixes Applied On 2026-03-27

Review/audit-driven fixes landed on `main`:
- Builder runtime is no longer hidden behind `.gitignore`; `agent/build/` is tracked on `main`.
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

## Highest-Leverage Next Steps

See [NEXT_BACKLOG.md](/Users/danielbabjak/Desktop/Agent_Life_Space/docs/strategy/NEXT_BACKLOG.md) for the prioritized execution queue.

Now that planner handoff, delivery lifecycle, and workspace joins have landed,
the next high-leverage work is:
1. Add artifact retention and recovery rules across build/review/delivery outputs
2. Extend policy beyond isolated build profiles into shared job/artifact/delivery/gateway decisions
3. Converge job metadata, execution history, artifacts, and cost into one shared persisted control-plane store
4. Bind cost and budget signals to durable runtime controls and acquisition paths
