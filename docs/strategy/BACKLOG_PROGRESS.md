# Backlog Progress

This file tracks current strategic execution progress against:
- `MASTER_SOURCE_OF_TRUTH.md`
- `THEMES_EPICS_STORIES.md`
- the actual state of `main`

Assessment basis:
- branch: `main` (after final-polish-strategy-sync pass)
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

- Reviewer v1 is `complete_for_phase`: execution mode is honest (always
  READ_ONLY_HOST), delivery requires explicit approval, client-safe redaction
  covers all text fields, artifact metadata hydrates on recovery.
- Platform foundations are stronger than before, but there is still no
  canonical cross-system job/control-plane model.
- Operator console has a mock-driven TS skeleton with CI typecheck, but no
  live backend or control plane.
- Builder, External Gateway, and most enterprise-hardening work are still
  ahead of us.
- The next highest-leverage step is not another reviewer feature. It is
  **Builder Foundation + Control-Plane Convergence**.

## Theme Status

| Theme | Status | Approx Progress | Current Truth On `main` | Main Remaining Gap |
|------|--------|-----------------|--------------------------|--------------------|
| T1 Platform Foundation | in_progress | 50% | ReviewJob with honest recovery, artifact storage, workspace subsystem, execution trace, _get_analysis_path() | No canonical cross-system job model yet |
| T2 Reviewer Product | complete_for_phase | 85% | Reviewer bounded context, verifier, delivery bundle (always requires approval), client-safe redaction (full pipeline), execution mode honest | LLM-augmented analysis, API entrypoint, PR comment packs are v2 |
| T3 Builder Product | not_started | 5% | Generic workspace and test foundations only | No first-class builder slice |
| T4 Operator Product | started | 10% | Mock-driven TS skeleton: job list, detail, delivery preview, approval queue. Client-safe model surface. No live backend. | No intake, planning, or delivery control plane |
| T5 Security, Governance, And Policy | in_progress | 50% | Tool policy deny-by-default, strict delivery approval (no bypass without DEV_MODE), full redaction pipeline (paths, hostnames, secrets, requester, source stripped), execution trace with analysis_path | Review execution still outside unified policy boundary |
| T6 Cost, Usage, And Observability | started | 20% | Status, usage fields, broad test discipline | No real per-job cost ledger or operator-facing observability |
| T7 External Capability Gateway | not_started | 0% | Nothing first-class yet | No gateway contract or provider integration |
| T8 Enterprise Hardening | in_progress | 30% | Review bounded context, ADR-001 sidecar, TS operator contracts + skeleton with CI typecheck, policy-driven redaction module, project-root discipline | No shared control-plane contract layer yet |

## Epic Snapshot

### T1 Platform Foundation

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T1-E1 Canonical Job Model | in_progress | 30% | `ReviewJob` exists with from_dict() recovery and artifact metadata hydration | `JobRunner`, `Task`, `AgentLoop` still coexist without convergence |
| T1-E2 Artifact-First Execution | mostly_complete | 75% | Artifact persistence via storage. ReviewArtifact.from_dict() hydrates metadata; full content via storage.get_artifacts(). | Artifact model not yet shared across future build/operator flows |
| T1-E3 Workspace And Execution Discipline | in_progress | 45% | Workspace subsystem exists; execution mode always READ_ONLY_HOST (honest); _get_analysis_path() is single source of truth | WORKSPACE_BOUND deferred to v2. Builder not workspace-first yet |

### T2 Reviewer Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T2-E1 Review Job Types | mostly_complete | 80% | repo_audit, pr_review, release_review exist and tested | PR/release review still v1-grade |
| T2-E2 Review Output Standardization | complete_for_phase | 90% | Canonical report, severity, Markdown and JSON, findings export | None for v1 |
| T2-E3 Review Verification | mostly_complete | 75% | Verifier exists and is tested | No golden-eval-backed quality regime |
| T2-E4 Review Delivery | mostly_complete | 80% | Delivery bundle (always delivery_ready=False without approval), strict approval gating, client-safe redaction (strips requester, source, execution_mode, trace; redacts all text fields) | API entrypoint, PR comment packs are v2 |

### T3 Builder Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T3-E1 Capability-Based Build Execution | not_started | 5% | Generic execution primitives exist | No builder capability catalog or build job slice |
| T3-E2 Build Verification Loop | started | 10% | Tests, lint, type checks exist globally | No builder-job verification loop |
| T3-E3 Acceptance Criteria Engine | not_started | 0% | Strategy only | No explicit acceptance criteria model |

### T4 Operator Product

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T4-E1 Intake And Qualification | not_started | 10% | Reviewer intake exists locally | No operator-wide intake model |
| T4-E2 Job Planning And Routing | not_started | 5% | Chat routing exists | No `JobPlan` layer |
| T4-E3 Delivery Workflow | started | 15% | Mock-driven TS skeleton with delivery preview, readiness check, approval queue view. Reviewer delivery bundle is precursor. | No live backend or operator delivery workflow |

### T5 Security, Governance, And Policy

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T5-E1 Policy Control Plane | in_progress | 50% | Tool policy deny-by-default, execution trace with analysis_path and mode | Review execution not under shared policy engine |
| T5-E2 Approval Model | in_progress | 40% | Approval queue, strict delivery approval (blocked without queue), DEV_MODE bypass only | Persistent approval store, artifact/job linkage |
| T5-E3 Client-Safe And Secret-Safe Output | mostly_complete | 70% | Full redaction pipeline (paths, hostnames, secrets) on all finding text fields (description, impact, recommendation, evidence). requester and source stripped. Error field redacted. | Wider system outputs beyond reviewer |

### T6 Cost, Usage, And Observability

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T6-E1 Cost Ledger | started | 15% | Usage fields exist on reviewer models | No real ledger or budget behavior |
| T6-E2 Runtime Observability | started | 20% | Status and traces exist locally | No operator-facing observability surface |
| T6-E3 Quality Evals | started | 20% | Tests are strong | No product-quality eval discipline |

### T7 External Capability Gateway

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T7-E1 Gateway Foundation | not_started | 0% | Nothing | No gateway contract layer |
| T7-E2 obolos.tech Integration | not_started | 0% | Nothing | No provider integration model |

### T8 Enterprise Hardening

| Epic | Status | Approx Progress | Current Truth On `main` | Remaining Gap |
|------|--------|-----------------|--------------------------|---------------|
| T8-E1 Contract-First Boundaries | in_progress | 40% | Reviewer bounded context, ADR-001 execution sidecar contract, TS operator DTOs. Telegram /review uses ReviewService. | Builder/operator bounded contexts. API parity. |
| T8-E2 Deployment And Environment Profiles | started | 20% | Project-root discipline, sandbox defaults, TS operator skeleton with CI typecheck | No explicit environment profile matrix |
| T8-E3 Compliance-Friendly Foundations | in_progress | 30% | Policy-driven redaction module, client-safe export with full pipeline, delivery approval gating (strict, no bypass without DEV_MODE) | Retention policy, evidence packaging rules |

## Current Strategic Interpretation

`main` is now best described as:
- a strong reviewer-first modular monolith
- with meaningful governance and workspace foundations
- with honest execution mode and strict delivery gating
- but without a shared control plane
- and without a first-class builder or operator slice

Reviewer v1 is `complete_for_phase`:
- execution mode is always READ_ONLY_HOST (honest — analyzers read host)
- delivery_ready=False by default (requires explicit approval)
- client-safe export strips requester, source, execution_mode, trace
- all finding text fields through full redaction pipeline
- artifact metadata hydrates on recovery via from_dict()
- _get_analysis_path() is single source of truth for analyzer input
- legacy Programmer.review_file() deprecated

Remaining for Reviewer v2:
- LLM-augmented analysis
- workspace-bound execution (analyzers reading from workspace)
- API review entrypoint
- PR comment pack delivery format
- Programmer.review_file() full removal

## Highest-Leverage Next Chunk

Recommended next chunk:
`Builder Foundation + Control-Plane Convergence`

Why this is next:
- it moves T1, T3, T4, and T8 together
- it turns the project from reviewer-only into a real engineering-system base
- it reduces the risk that builder work gets bolted onto chat/runtime flow

Target backlog movement for the next chunk:
- T1-E1-S1: canonical Job schema shared across review + build
- T1-E1-S2: shared lifecycle states and recovery rules
- T1-E1-S3: unified persistence for metadata, execution, artifacts, cost
- T1-E1-S5: convergence rules for ReviewJob vs JobRunner/Task/AgentLoop
- T1-E3-S1: builder execution in isolated workspaces
- T1-E3-S3: workspace/job/artifact/approval linkage
- T3-E1-S1: implementation capability catalog
- T3-E1-S2: route implementation jobs to capabilities
- T3-E1-S3: builder patch artifacts
- T3-E1-S4: resumable builder execution
- T3-E2-S1: builder-job test/lint/typecheck loop
- T3-E2-S3: fail jobs on unmet acceptance criteria
- T3-E2-S4: builder verification artifacts
- T3-E3-S1: acceptance criteria object model
- T3-E3-S2: attach criteria to jobs
- T8-E1-S1: control-plane/execution-plane/verification contracts
- T8-E1-S4: builder bounded context module boundary

Definition of success for the next chunk:
- first shared control-plane job primitives exist
- first builder bounded context exists
- builder jobs are workspace-first
- build verification artifacts exist
- acceptance criteria becomes a first-class model
- reviewer and builder can coexist without further chat-centric drift
