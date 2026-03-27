# Backlog Progress

This file tracks current strategic execution progress against:
- `MASTER_SOURCE_OF_TRUTH.md`
- `THEMES_EPICS_STORIES.md`
- the actual state of `main`

Assessment basis:
- branch: `main` (after Builder Delivery Package + Operator Health slice)
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
- Builder now has a declared capability catalog, resumable checkpoint-based
  execution, deterministic patch + diff capture, and a shared build delivery
  package preview.
- Acceptance criteria now understand richer domain signals, including
  post-build review verdicts plus documentation, target-file, and patch-change
  requirements.
- Operator report now includes recent artifacts alongside jobs, approvals,
  workspace health, and worker execution summaries.
- Operator has a mock-driven TS skeleton with reporting/inbox contracts, but no
  live backend.
- External Gateway and most enterprise-hardening work are still ahead.

## Theme Status

| Theme | Status | Approx Progress | Current Truth On `main` | Main Remaining Gap |
|------|--------|-----------------|--------------------------|--------------------|
| T1 Platform Foundation | in_progress | 86% | Shared control-plane primitives now back build and review directly, with explicit runtime coexistence rules plus shared job/artifact queries, a shared delivery package model, and richer workspace/approval linkage exposed through the orchestrator. | No unified cross-domain persistence/action layer yet. |
| T2 Reviewer Product | complete_for_phase | 85% | Reviewer bounded context, verifier, strict delivery gating, full client-safe redaction, honest execution mode | LLM analysis, API entrypoint, PR comment packs are v2 |
| T3 Builder Product | in_progress | 76% | Builder bounded context is tracked on `main`, capability-declared, resumable, orchestrator-wired, CLI-reachable, workspace-synced, verification-gated, review-aware, and now emits deterministic patch/diff artifacts plus a build delivery package preview. | Build step is still placeholder-grade. No LLM implementation or external delivery send path |
| T4 Operator Product | in_progress | 62% | Unified intake routing, phase-aware `JobPlan` preview/submit output, policy-backed budget qualification, capability assignments, CLI intake preview/submit, operator report service, and shared builder delivery package preview now exist. | No live backend/UI, durable planning state, or handoff audit workflow |
| T5 Security, Governance, And Policy | in_progress | 65% | Tool policy deny-by-default, strict delivery approval, full redaction pipeline, and persistent/queryable approval storage with job/artifact/workspace/bundle linkage | Review/build execution outside unified policy boundary |
| T6 Cost, Usage, And Observability | in_progress | 58% | UsageSummary on jobs, orchestrator-visible build/review counters, traces, shared cross-system job/artifact queries, approval backlog visibility, and operator-facing reporting that now includes workspace and worker execution health. | No real per-job cost ledger or live operator UI |
| T7 External Capability Gateway | not_started | 0% | Nothing | No gateway contract |
| T8 Enterprise Hardening | in_progress | 68% | Shared control-plane layer, explicit runtime coexistence rules, review + build bounded contexts on shared primitives, unified intake/planning, resumable builder runtime, shared job/artifact/query/reporting surfaces, and bundle-aware delivery linkage. | Runtime boundaries are clearer, but not yet enforced as extraction-grade invariants. |

## Epic Snapshot

### T1 Platform Foundation

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T1-E1 Canonical Job Model | in_progress | 88% | Shared JobKind, JobStatus, JobTiming, ExecutionStep, UsageSummary, normalized job queries, and explicit runtime coexistence rules now cover build, review, and operate surfaces. | The rules are explicit, but not yet enforced by stronger invariants or migrations. |
| T1-E2 Artifact-First Execution | mostly_complete | 90% | ArtifactKind shared. ReviewArtifact and BuildArtifact both produce typed artifacts, shared artifact query/recovery spans build and review, and builder now emits deterministic patch + diff outputs for delivery packaging. | No unified artifact retention/policy layer yet. |
| T1-E3 Workspace And Execution Discipline | in_progress | 68% | Builder is workspace-first, syncs repos into workspaces, and now links build delivery approvals to workspace and bundle records while reviewer stays explicitly READ_ONLY_HOST. | Shared cross-domain workspace policy and reviewer workspace execution are still partial. |

### T2 Reviewer Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T2-E1 Review Job Types | mostly_complete | 80% | repo_audit, pr_review, release_review | PR/release still v1-grade |
| T2-E2 Review Output Standardization | complete_for_phase | 90% | Canonical report, severity, Markdown, JSON | None for v1 |
| T2-E3 Review Verification | mostly_complete | 75% | Verifier exists and tested | No golden-eval-backed quality regime |
| T2-E4 Review Delivery | mostly_complete | 80% | Strict approval, full redaction | API entrypoint, PR comment packs are v2 |

### T3 Builder Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T3-E1 Capability-Based Build Execution | in_progress | 84% | BuildJob, BuildIntake, BuildService, BuildStorage, workspace sync, capability catalog, orchestrator runtime entrypoint, CLI build entrypoint, resumable checkpoints, deterministic patch/diff capture, and build delivery package preview now exist on `main`. | Build step is placeholder (no real code generation). |
| T3-E2 Build Verification Loop | in_progress | 74% | Verification suite runs test + lint + conditional typecheck in workspace. Successful jobs can invoke deterministic post-build review before completion, and review findings can now feed acceptance + delivery output. | No build-specific verification discovery or configurable review gating policy. |
| T3-E3 Acceptance Criteria Engine | in_progress | 70% | Acceptance criteria support typed states, keyword-bound verification checks, explicit `verify:` commands, review-backed security checks, and change-set-aware docs/target-file evaluators. | No semantic requirement engine beyond deterministic rule-based evaluators. |

### T4 Operator Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T4-E1 Intake And Qualification | in_progress | 70% | Unified operator intake now resolves scope size/signals, risk factors, policy-backed budget envelopes, and review/build routing, plus CLI preview/submit surfaces. | Cost estimates are still heuristic, and there is no live operator UI |
| T4-E2 Job Planning And Routing | in_progress | 60% | `JobPlan` now includes explicit phases, capability assignments, structured budget metadata, and orchestrator/CLI preview output. | Durable planner state and planning traces are still partial |
| T4-E3 Delivery Workflow | in_progress | 45% | Shared `DeliveryPackage` model now exists, reviewer delivery bundle remains in place, and builder now assembles a delivery package preview plus approval-gated delivery request. | No delivery status/audit trail and no live operator workflow |

### T5 Security, Governance, And Policy

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T5-E1 Policy Control Plane | in_progress | 50% | Tool policy deny-by-default, execution trace | Review/build execution not under shared policy engine |
| T5-E2 Approval Model | in_progress | 78% | Approval queue, strict delivery approval, persistent ApprovalStorage, and query filters now cover job/artifact/workspace/bundle linkage across review and build delivery flows. | Delivery status and broader policy unification remain partial |
| T5-E3 Client-Safe Output | mostly_complete | 70% | Full redaction pipeline, requester/source stripped | Wider system outputs beyond reviewer |

### T6 Cost, Usage, And Observability

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T6-E1 Cost Ledger | started | 20% | UsageSummary in control-plane. Fields on review and build jobs. | No real ledger or budget behavior |
| T6-E2 Runtime Observability | in_progress | 76% | Status and traces exist locally, including orchestrator-visible build/review counters, shared job/artifact queries, approval backlog visibility, operator-facing report output, plus workspace health and worker execution summaries. | No live UI, push updates, or deeper delivery/planning telemetry yet |
| T6-E3 Quality Evals | started | 20% | Tests are strong | No product-quality eval discipline |

### T7 External Capability Gateway

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T7-E1 Gateway Foundation | not_started | 0% | Nothing | No gateway contract |
| T7-E2 obolos.tech Integration | not_started | 0% | Nothing | No provider integration |

### T8 Enterprise Hardening

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T8-E1 Contract-First Boundaries | in_progress | 76% | Shared control-plane primitives now back build and review directly. ADR-001 sidecar, unified intake/planning, explicit runtime coexistence rules, and cross-system job/artifact/query/report surfaces reinforce the boundary. | Boundaries are documented and queryable, but not yet enforced as extraction-grade invariants. |
| T8-E2 Deployment And Environment Profiles | started | 20% | Project-root discipline, sandbox defaults | No explicit environment profile matrix |
| T8-E3 Compliance-Friendly Foundations | in_progress | 30% | Redaction module, client-safe export, delivery gating | Retention policy, evidence packaging |

## Current Strategic Interpretation

`main` is now best described as:
- a reviewer + builder foundation modular monolith
- with shared control-plane primitives
- with meaningful governance and workspace foundations
- with the builder runtime now actually wired into the orchestrator, capability-declared, resumable, reachable through real entrypoints, tracked on `main`, and able to emit deterministic patch/diff outputs
- with shared control-plane query/report layers for build, review, operate, and artifacts
- with unified operator intake qualification, planning, and routing for build and review requests
- with a first shared delivery-package model plus build delivery previews and approval linkage
- but without full builder capability (build step is placeholder)
- and without durable planner execution history, delivery audit events, or a live operator UI

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
- stronger policy/execution boundary integration across the wider runtime

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
- Acceptance evaluation now supports review-backed security checks plus
  change-set-aware docs and target-file requirements.
- Operator report now includes recent artifacts in addition to jobs,
  approvals, workspace health, and worker execution state.

## Highest-Leverage Next Steps

See [NEXT_BACKLOG.md](/Users/danielbabjak/Desktop/Agent_Life_Space/docs/strategy/NEXT_BACKLOG.md) for the prioritized execution queue.

Now that builder delivery packaging and operator health visibility have landed, the next
high-leverage work is:
1. Persist planner outputs for richer operator handoff
2. Record planning decision traces as first-class audit artifacts
3. Record delivery status and handoff audit events
4. Strengthen build-specific verification discovery and policy-driven review thresholds
