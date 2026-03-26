# Backlog Progress

This file tracks current strategic execution progress against:
- `MASTER_SOURCE_OF_TRUTH.md`
- `THEMES_EPICS_STORIES.md`
- the actual state of `main`

Assessment basis:
- branch: `main` (after Builder Foundation + Control-Plane Convergence)
- interpretation date: `2026-03-26`

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
- Builder is workspace-first (WORKSPACE_BOUND by default, requires workspace
  manager). Builder produces verification, acceptance, diff, and trace artifacts.
- Operator has mock-driven TS skeleton with CI typecheck but no live backend.
- External Gateway and most enterprise-hardening work are still ahead.

## Theme Status

| Theme | Status | Approx Progress | Current Truth On `main` | Main Remaining Gap |
|------|--------|-----------------|--------------------------|--------------------|
| T1 Platform Foundation | in_progress | 60% | Shared control-plane primitives (JobKind, JobStatus, JobTiming, ExecutionStep, ArtifactKind, UsageSummary). ReviewJob and BuildJob both map to these. | ReviewJob does not yet use shared primitives directly (bridge/adaptation layer pending) |
| T2 Reviewer Product | complete_for_phase | 85% | Reviewer bounded context, verifier, strict delivery gating, full client-safe redaction, honest execution mode | LLM analysis, API entrypoint, PR comment packs are v2 |
| T3 Builder Product | in_progress | 35% | Builder bounded context with models, service, storage, verification loop, acceptance criteria engine. Workspace-first. Foundation-grade. | No LLM implementation, no real code generation, no advanced acceptance evaluation |
| T4 Operator Product | started | 10% | Mock-driven TS skeleton. No live backend. | No intake, planning, or delivery control plane |
| T5 Security, Governance, And Policy | in_progress | 50% | Tool policy deny-by-default, strict delivery approval, full redaction pipeline | Review/build execution outside unified policy boundary |
| T6 Cost, Usage, And Observability | started | 20% | UsageSummary in control-plane. Fields on jobs. | No real per-job cost ledger or operator-facing observability |
| T7 External Capability Gateway | not_started | 0% | Nothing | No gateway contract |
| T8 Enterprise Hardening | in_progress | 35% | Shared control-plane layer, review + build bounded contexts, ADR-001 sidecar, TS operator contracts | ReviewJob not yet on shared primitives. No cross-system job query layer. |

## Epic Snapshot

### T1 Platform Foundation

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T1-E1 Canonical Job Model | in_progress | 45% | Shared JobKind, JobStatus, JobTiming, ExecutionStep exist. BuildJob uses them. ReviewJob still uses own equivalents. | ReviewJob migration to shared primitives. JobRunner/Task/AgentLoop convergence. |
| T1-E2 Artifact-First Execution | mostly_complete | 75% | ArtifactKind shared. ReviewArtifact and BuildArtifact both produce typed artifacts. | Shared artifact query layer not yet cross-domain. |
| T1-E3 Workspace And Execution Discipline | in_progress | 55% | Builder is workspace-first (WORKSPACE_BOUND default). Reviewer is READ_ONLY_HOST. | Builder workspace integration is foundation-grade. |

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
| T3-E1 Capability-Based Build Execution | in_progress | 35% | BuildJob, BuildIntake, BuildService exist. Workspace-first. Foundation build flow works. | No capability catalog. Build step is placeholder (no real code generation). |
| T3-E2 Build Verification Loop | in_progress | 40% | Verification suite runs test + lint in workspace. Results are first-class artifacts. | No review-after-build pass. Acceptance is foundation-grade. |
| T3-E3 Acceptance Criteria Engine | in_progress | 40% | AcceptanceCriterion, AcceptanceVerdict are first-class models. Criteria attached to jobs. Verdict evaluated at completion. | Evaluation is simple (all-met-if-verification-passed). No semantic checks. |

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
| T6-E2 Runtime Observability | started | 20% | Status and traces exist locally | No operator-facing observability |
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
- but without full builder capability (build step is placeholder)
- and without a shared control plane for cross-system job management

Reviewer v1: `complete_for_phase`
Builder v1: `in_progress` (foundation-grade)

## Highest-Leverage Next Steps

Now that builder foundation exists, the next high-leverage work is:
1. Wire real build execution (LLM-powered or tool-powered implementation)
2. Migrate ReviewJob to shared control-plane primitives
3. Cross-system job query layer
4. Operator intake that routes to review OR build
