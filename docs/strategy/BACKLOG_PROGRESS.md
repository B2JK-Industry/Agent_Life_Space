# Backlog Progress

This file tracks current strategic execution progress against
`MASTER_SOURCE_OF_TRUTH.md` and `THEMES_EPICS_STORIES.md`.

Important:
- This snapshot is based on the audited state of PR `#44`
- Assessed commit: `8537f26`
- This snapshot is not the same thing as `main`
- Use this file to understand delivery progress, not merge state

Status legend:
- `not_started`: no meaningful implementation yet
- `started`: foundations or isolated pieces exist
- `in_progress`: meaningful implementation exists, but key gaps remain
- `mostly_complete`: usable slice exists, but not yet fully productized
- `complete`: strategy intent is materially closed for the current phase

## Overall Snapshot

- Reviewer bounded context now exists and is real
- Reviewer v1 is a usable architectural slice, not just a placeholder
- Platform foundations improved, but canonical system-wide job convergence is
  still open
- Builder, Operator, External Gateway, and Enterprise Hardening remain mostly
  future work

## Theme Status

| Theme | Status | Notes |
|------|--------|-------|
| T1 Platform Foundation | in_progress | ReviewJob and artifacts exist, but not yet system-canonical |
| T2 Reviewer Product | in_progress | Reviewer v1 exists; delivery wiring and recovery details remain |
| T3 Builder Product | not_started | No first-class builder slice yet |
| T4 Operator Product | not_started | No first-class intake/planning/delivery control plane yet |
| T5 Security, Governance, And Policy | in_progress | Strong foundations exist; reviewer-specific delivery governance remains open |
| T6 Cost, Usage, And Observability | started | Existing foundations exist; review-specific cost and quality ledgers remain open |
| T7 External Capability Gateway | not_started | No gateway implementation yet |
| T8 Enterprise Hardening | started | Boundary cleanup and path centralization improved, but not closed |

## Epic Snapshot

### T1 Platform Foundation

| Epic | Status | Notes |
|------|--------|-------|
| T1-E1 Canonical Job Model | in_progress | `ReviewJob` exists, but system still also has `JobRunner`, `Task`, and `AgentLoop` models |
| T1-E2 Artifact-First Execution | in_progress | Review artifacts exist, but storage/recovery still returns mostly metadata instead of full payloads |
| T1-E3 Workspace And Execution Discipline | in_progress | Hidden sandbox/workspace coupling improved, but reviewer flow is not yet workspace-bound |

### T2 Reviewer Product

| Epic | Status | Notes |
|------|--------|-------|
| T2-E1 Review Job Types | mostly_complete | `repo_audit`, `pr_review`, and `release_review` exist in reviewer context |
| T2-E2 Review Output Standardization | mostly_complete | Canonical report, severity, Markdown and JSON exports exist |
| T2-E3 Review Verification | mostly_complete | Verifier pass exists and is tested |
| T2-E4 Review Delivery | started | Artifacts exist, but approval gating and adapter wiring are still open |

### T3 Builder Product

| Epic | Status | Notes |
|------|--------|-------|
| T3-E1 Capability-Based Build Execution | not_started | No builder job slice yet |
| T3-E2 Build Verification Loop | not_started | Existing testing infra is not yet builder-job-centric |
| T3-E3 Acceptance Criteria Engine | not_started | No explicit acceptance criteria object model yet |

### T4 Operator Product

| Epic | Status | Notes |
|------|--------|-------|
| T4-E1 Intake And Qualification | not_started | Reviewer intake exists locally, not yet system control-plane intake |
| T4-E2 Job Planning And Routing | not_started | No `JobPlan` layer yet |
| T4-E3 Delivery Workflow | not_started | Delivery packaging and approval handoff remain future work |

### T5 Security, Governance, And Policy

| Epic | Status | Notes |
|------|--------|-------|
| T5-E1 Policy Control Plane | in_progress | Tool policy foundations exist; reviewer execution still bypasses a unified execution policy path |
| T5-E2 Approval Model | in_progress | Approval foundations exist, but review delivery approval is not yet wired |
| T5-E3 Client-Safe And Secret-Safe Output | started | Security/reporting foundations exist, but reviewer redaction mode is not yet complete |

### T6 Cost, Usage, And Observability

| Epic | Status | Notes |
|------|--------|-------|
| T6-E1 Cost Ledger | started | Review job fields exist, but deterministic reviewer v1 does not yet fill them materially |
| T6-E2 Runtime Observability | started | Existing runtime observability exists; review-specific operator surface does not |
| T6-E3 Quality Evals | started | Reviewer tests exist, but golden review benchmarking is not yet in place |

### T7 External Capability Gateway

| Epic | Status | Notes |
|------|--------|-------|
| T7-E1 Gateway Foundation | not_started | No gateway contract yet |
| T7-E2 obolos.tech Integration | not_started | No modeled integration yet |

### T8 Enterprise Hardening

| Epic | Status | Notes |
|------|--------|-------|
| T8-E1 Contract-First Boundaries | started | Reviewer bounded context exists, but legacy channel/reviewer drift still remains |
| T8-E2 Deployment And Environment Profiles | started | Centralized project-root resolution exists |
| T8-E3 Compliance-Friendly Foundations | started | Artifact traceability improved, but export/recovery depth remains incomplete |

## Current Strategic Interpretation

The project has now crossed the line from "review idea" to "real reviewer
foundation". The most important next move is not to expand scope into Builder or
Operator yet, but to finish Reviewer v1 properly:

- persist full intake and artifact payloads for recovery and delivery
- bind reviewer execution to workspace and execution policy discipline
- route review entrypoints through `ReviewService` instead of legacy paths
- close delivery approval and client-safe output gaps
