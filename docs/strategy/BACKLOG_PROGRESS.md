# Backlog Progress

This file tracks current strategic execution progress against:
- `MASTER_SOURCE_OF_TRUTH.md`
- `THEMES_EPICS_STORIES.md`
- the actual state of `main`

Assessment basis:
- branch: `main`
- commit: `ccfd969`
- interpretation date: `2026-03-26`

Important:
- this is a product-and-architecture progress snapshot, not a merge history log
- percentages are directional, not exact velocity math
- `complete_for_phase` means "good enough for the current bounded-context phase"

Status legend:
- `not_started`: no meaningful implementation yet
- `started`: foundations or isolated pieces exist
- `in_progress`: meaningful implementation exists, but key gaps remain
- `mostly_complete`: usable slice exists, but still leaks or drifts
- `complete_for_phase`: sufficiently closed for the current phase

## Overall Snapshot

- Reviewer is the only product slice that is genuinely far along.
- Platform foundations are much stronger than before, but there is still no
  canonical cross-system job/control-plane model.
- Security and governance foundations are meaningful, but reviewer execution is
  still not fully under one shared execution boundary.
- Builder, Operator, External Gateway, and most enterprise-hardening work are
  still ahead of us.
- The next highest-leverage step is not another reviewer feature. It is
  Builder foundation plus control-plane convergence.

## Theme Status

| Theme | Status | Approx Progress | Current Truth On `main` | Main Remaining Gap |
|------|--------|-----------------|--------------------------|--------------------|
| T1 Platform Foundation | in_progress | 45% | ReviewJob, artifact storage, workspace subsystem, execution trace | No canonical cross-system job model yet |
| T2 Reviewer Product | mostly_complete | 80% | Reviewer bounded context, verifier, delivery bundle, approval hook, Telegram path | Delivery is not fully operator/API complete and reviewer execution truth still leaks |
| T3 Builder Product | not_started | 5% | Generic workspace and test foundations only | No first-class builder slice |
| T4 Operator Product | not_started | 5% | Partial approval and delivery foundations only | No intake, planning, or delivery control plane |
| T5 Security, Governance, And Policy | in_progress | 40% | Tool policy, reviewer execution trace, approval queue, client-safe export foundation | Review repo/diff execution still sits outside a unified policy boundary |
| T6 Cost, Usage, And Observability | started | 20% | Status, usage fields, broad test discipline | No real per-job cost ledger or operator-facing observability surface |
| T7 External Capability Gateway | not_started | 0% | Nothing first-class yet | No gateway contract or provider integration |
| T8 Enterprise Hardening | started | 25% | Review bounded context, some invariants, project-root discipline | No true control-plane contract layer or extraction-ready boundaries yet |

## Epic Snapshot

### T1 Platform Foundation

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T1-E1 Canonical Job Model | in_progress | 30% | `ReviewJob` exists and is useful | `JobRunner`, `Task`, and `AgentLoop` still coexist without convergence |
| T1-E2 Artifact-First Execution | mostly_complete | 70% | Reviewer artifacts, report payloads, and delivery bundle exist | Artifact hydration is not yet the single shared truth across domains |
| T1-E3 Workspace And Execution Discipline | in_progress | 40% | Workspace subsystem exists; reviewer execution mode is explicit | Reviewer execution truth is still imperfect and builder is not workspace-first yet |

### T2 Reviewer Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T2-E1 Review Job Types | mostly_complete | 80% | `repo_audit`, `pr_review`, and `release_review` exist | PR and release review still need deeper productization |
| T2-E2 Review Output Standardization | mostly_complete | 85% | Canonical report, severity, Markdown and JSON exist | Output is not yet specialized for all delivery channels |
| T2-E3 Review Verification | mostly_complete | 75% | Verifier exists and is tested | No golden-eval-backed quality regime yet |
| T2-E4 Review Delivery | in_progress | 70% | Delivery bundle, approval hook, and client-safe bundle exist | No-bypass delivery governance, PR comment packs, and API path are still open |

### T3 Builder Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T3-E1 Capability-Based Build Execution | not_started | 5% | Generic execution primitives exist | No builder capability catalog or build job slice |
| T3-E2 Build Verification Loop | started | 10% | Tests, lint, and type checks exist globally | No builder-job verification loop |
| T3-E3 Acceptance Criteria Engine | not_started | 0% | Strategy only | No explicit acceptance criteria model |

### T4 Operator Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T4-E1 Intake And Qualification | not_started | 10% | Reviewer intake exists locally | No operator-wide intake model |
| T4-E2 Job Planning And Routing | not_started | 5% | Chat routing exists | No `JobPlan` layer exists |
| T4-E3 Delivery Workflow | not_started | 10% | Reviewer delivery bundle is a local precursor | No operator delivery workflow |

### T5 Security, Governance, And Policy

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T5-E1 Policy Control Plane | in_progress | 45% | Tool policy and reviewer trace foundations exist | Reviewer repo/diff execution is not under the shared execution policy |
| T5-E2 Approval Model | started | 30% | Approval queue exists | Persistence and job/artifact linkage remain shallow |
| T5-E3 Client-Safe And Secret-Safe Output | in_progress | 55% | Reviewer redaction exists | Redaction is still narrow and reviewer-specific |

### T6 Cost, Usage, And Observability

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T6-E1 Cost Ledger | started | 15% | Usage fields exist on reviewer models | No real ledger or budget behavior yet |
| T6-E2 Runtime Observability | started | 20% | Status and traces exist locally | No operator-facing observability surface |
| T6-E3 Quality Evals | started | 20% | Tests are strong | No product-quality eval discipline yet |

### T7 External Capability Gateway

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T7-E1 Gateway Foundation | not_started | 0% | Nothing first-class exists | No gateway contract layer |
| T7-E2 obolos.tech Integration | not_started | 0% | Nothing first-class exists | No provider integration model |

### T8 Enterprise Hardening

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T8-E1 Contract-First Boundaries | in_progress | 35% | Reviewer bounded context improved architecture | Broader runtime contracts are still implicit |
| T8-E2 Deployment And Environment Profiles | started | 20% | Some environment discipline exists | No explicit profile matrix yet |
| T8-E3 Compliance-Friendly Foundations | started | 20% | Artifacts, traces, and basic redaction exist | No strong retention/evidence policy yet |

## Current Strategic Interpretation

`main` is now best described as:
- a strong reviewer-first modular monolith
- with meaningful governance and workspace foundations
- but without a shared control plane
- and without a first-class builder or operator slice

That means the masterplan is still valid, but implementation is concentrated in:
- T1 partial foundations
- T2 reviewer product
- T5 partial governance
- T8 early architectural hardening

The main systemic debt is still:
- no canonical Job model
- no builder bounded context
- no operator control plane
- no gateway layer

## Highest-Leverage Next Chunk

Recommended next chunk:
`Builder Foundation + Control-Plane Convergence`

Why this is next:
- it moves T1, T3, T4, and T8 together
- it turns the project from reviewer-only into a real engineering-system base
- it reduces the risk that builder work gets bolted onto chat/runtime flow

Target backlog movement for the next chunk:
- T1-E1-S1
- T1-E1-S2
- T1-E1-S3
- T1-E1-S5
- T1-E3-S1
- T1-E3-S3
- T3-E1-S1
- T3-E1-S2
- T3-E1-S3
- T3-E1-S4
- T3-E2-S1
- T3-E2-S3
- T3-E2-S4
- T3-E3-S1
- T3-E3-S2
- T8-E1-S1
- T8-E1-S4

Definition of success for the next chunk:
- first shared control-plane job primitives exist
- first builder bounded context exists
- builder jobs are workspace-first
- build verification artifacts exist
- acceptance criteria becomes a first-class model
- reviewer and builder can coexist without further chat-centric drift
