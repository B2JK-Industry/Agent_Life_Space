# Backlog Progress

This file tracks current strategic execution progress against:
- `MASTER_SOURCE_OF_TRUTH.md`
- `THEMES_EPICS_STORIES.md`
- the actual state of `main`

Assessment basis:
- branch: `main` (after Builder Runtime Integration + Review-Driven Hardening)
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
- Review and build both expose service-level status counters through the
  orchestrator, improving local observability.
- Operator has mock-driven TS skeleton with CI typecheck but no live backend.
- External Gateway and most enterprise-hardening work are still ahead.

## Theme Status

| Theme | Status | Approx Progress | Current Truth On `main` | Main Remaining Gap |
|------|--------|-----------------|--------------------------|--------------------|
| T1 Platform Foundation | in_progress | 62% | Shared control-plane primitives, workspace subsystem, and now orchestrator-wired review/build services with local status visibility. | ReviewJob does not yet use shared primitives directly (bridge/adaptation layer pending) |
| T2 Reviewer Product | complete_for_phase | 85% | Reviewer bounded context, verifier, strict delivery gating, full client-safe redaction, honest execution mode | LLM analysis, API entrypoint, PR comment packs are v2 |
| T3 Builder Product | in_progress | 45% | Builder bounded context is tracked on `main`, orchestrator-initialized, workspace-synced, verification-gated, and acceptance-aware. | No LLM implementation, no real code generation, no advanced acceptance evaluation or delivery path |
| T4 Operator Product | started | 10% | Mock-driven TS skeleton. No live backend. | No intake, planning, or delivery control plane |
| T5 Security, Governance, And Policy | in_progress | 50% | Tool policy deny-by-default, strict delivery approval, full redaction pipeline | Review/build execution outside unified policy boundary |
| T6 Cost, Usage, And Observability | started | 25% | UsageSummary on jobs plus orchestrator-visible build/review service counters and traces. | No real per-job cost ledger or operator-facing observability surface |
| T7 External Capability Gateway | not_started | 0% | Nothing | No gateway contract |
| T8 Enterprise Hardening | in_progress | 40% | Shared control-plane layer, review + build bounded contexts, ADR-001 sidecar, TS operator contracts, tracked builder runtime. | ReviewJob not yet on shared primitives. No cross-system job query layer. |

## Epic Snapshot

### T1 Platform Foundation

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T1-E1 Canonical Job Model | in_progress | 45% | Shared JobKind, JobStatus, JobTiming, ExecutionStep exist. BuildJob uses them. ReviewJob still uses own equivalents. | ReviewJob migration to shared primitives. JobRunner/Task/AgentLoop convergence. |
| T1-E2 Artifact-First Execution | mostly_complete | 75% | ArtifactKind shared. ReviewArtifact and BuildArtifact both produce typed artifacts. | Shared artifact query layer not yet cross-domain. |
| T1-E3 Workspace And Execution Discipline | in_progress | 60% | Builder is workspace-first, syncs repos into workspaces, and reviewer stays explicitly READ_ONLY_HOST. | Shared cross-domain workspace, artifact, and approval linkage is still partial. |

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
| T3-E1 Capability-Based Build Execution | in_progress | 45% | BuildJob, BuildIntake, BuildService, BuildStorage, and workspace sync exist on `main`. Builder is initialized by the orchestrator. | No capability catalog. Build step is placeholder (no real code generation). |
| T3-E2 Build Verification Loop | in_progress | 55% | Verification suite runs test + lint + conditional typecheck in workspace. Results are first-class artifacts. | No review-after-build pass. No build-specific verification discovery. |
| T3-E3 Acceptance Criteria Engine | in_progress | 55% | Acceptance criteria support typed states, keyword-bound verification checks, `verify:` commands, and structured acceptance reports. | No semantic requirement engine or richer domain evaluators. |

### T4 Operator Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T4-E1 Intake And Qualification | started | 10% | Reviewer intake and builder intake exist locally | No operator-wide intake model |
| T4-E2 Job Planning And Routing | not_started | 5% | Chat routing exists | No JobPlan layer |
| T4-E3 Delivery Workflow | started | 15% | Mock-driven TS skeleton. Reviewer delivery bundle. | No live backend or operator workflow |

### T5 Security, Governance, And Policy

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T5-E1 Policy Control Plane | in_progress | 50% | Tool policy deny-by-default, execution trace | Review/build execution not under shared policy engine |
| T5-E2 Approval Model | in_progress | 40% | Approval queue, strict delivery approval | Persistent store, artifact/job linkage |
| T5-E3 Client-Safe Output | mostly_complete | 70% | Full redaction pipeline, requester/source stripped | Wider system outputs beyond reviewer |

### T6 Cost, Usage, And Observability

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T6-E1 Cost Ledger | started | 20% | UsageSummary in control-plane. Fields on review and build jobs. | No real ledger or budget behavior |
| T6-E2 Runtime Observability | started | 25% | Status and traces exist locally, including orchestrator-visible build/review counters | No operator-facing observability |
| T6-E3 Quality Evals | started | 20% | Tests are strong | No product-quality eval discipline |

### T7 External Capability Gateway

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T7-E1 Gateway Foundation | not_started | 0% | Nothing | No gateway contract |
| T7-E2 obolos.tech Integration | not_started | 0% | Nothing | No provider integration |

### T8 Enterprise Hardening

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T8-E1 Contract-First Boundaries | in_progress | 45% | Shared control-plane primitives. Review and build as separate bounded contexts. ADR-001 sidecar. | ReviewJob not yet on shared primitives. No cross-system query. |
| T8-E2 Deployment And Environment Profiles | started | 20% | Project-root discipline, sandbox defaults | No explicit environment profile matrix |
| T8-E3 Compliance-Friendly Foundations | in_progress | 30% | Redaction module, client-safe export, delivery gating | Retention policy, evidence packaging |

## Current Strategic Interpretation

`main` is now best described as:
- a reviewer + builder foundation modular monolith
- with shared control-plane primitives
- with meaningful governance and workspace foundations
- with the builder runtime now actually wired into the orchestrator and tracked on `main`
- but without full builder capability (build step is placeholder)
- and without a shared control plane for cross-system job management

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
- full ReviewJob migration to shared control-plane primitives

## Code Review And Fixes Applied On 2026-03-27

Review/audit-driven fixes landed on `main`:
- Builder runtime is no longer hidden behind `.gitignore`; `agent/build/` is tracked on `main`.
- Build jobs now sync the requested repo into the managed workspace before verification.
- Builder verification now includes conditional typecheck when project config is present.
- Acceptance criteria no longer auto-pass blindly; unknown criteria fail closed and `verify:` commands run in the workspace.
- Review job recovery now preserves `include_patterns` and `exclude_patterns`.
- `AgentOrchestrator` now initializes builder storage/service explicitly and exposes build/review counters in status output.

## Highest-Leverage Next Steps

See [NEXT_BACKLOG.md](/Users/danielbabjak/Desktop/Agent_Life_Space/docs/strategy/NEXT_BACKLOG.md) for the prioritized execution queue.

Now that builder runtime is tracked and orchestrator-wired, the next high-leverage work is:
1. Wire real build execution (LLM-powered or tool-powered implementation)
2. Add review-after-build and richer acceptance evaluators
3. Migrate ReviewJob to shared control-plane primitives
4. Add cross-system job query and operator intake routing
