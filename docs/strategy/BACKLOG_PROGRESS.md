# Backlog Progress

This file tracks current strategic execution progress against
`MASTER_SOURCE_OF_TRUTH.md` and `THEMES_EPICS_STORIES.md`.

Important:
- This snapshot is based on reviewer v1 closure pass (post PR #45)
- Use this file to understand delivery progress, not merge state

Status legend:
- `not_started`: no meaningful implementation yet
- `started`: foundations or isolated pieces exist
- `in_progress`: meaningful implementation exists, but key gaps remain
- `mostly_complete`: usable slice exists, but not yet fully productized
- `complete`: strategy intent is materially closed for the current phase

## Overall Snapshot

- Reviewer v1 is mostly_complete: recovery-safe storage, explicit execution mode,
  channel adapter wired, policy audit trace
- ReviewJob has from_dict() for full recovery (intake, report, findings, trace)
- Execution mode is explicit (READ_ONLY_HOST vs WORKSPACE_BOUND)
- Telegram /review routes through ReviewService, not legacy Programmer
- Platform foundations improved, but canonical system-wide job convergence is
  still open
- Builder, Operator, External Gateway, and Enterprise Hardening remain future work

## Theme Status

| Theme | Status | Notes |
|------|--------|-------|
| T1 Platform Foundation | in_progress | ReviewJob with recovery, artifacts with full payloads, execution mode explicit. Not yet system-canonical. |
| T2 Reviewer Product | complete_for_phase | Reviewer v1 closed: runtime adapter, recovery, delivery bundle, approval gating, client-safe redaction, verifier, execution mode. LLM analysis is v2 scope. |
| T3 Builder Product | not_started | No first-class builder slice yet |
| T4 Operator Product | not_started | No first-class intake/planning/delivery control plane yet |
| T5 Security, Governance, And Policy | mostly_complete | Delivery approval strict (no bypass), policy-driven redaction, client-safe export, secret redaction in analyzers |
| T6 Cost, Usage, And Observability | started | Existing foundations exist; review-specific cost and quality ledgers remain open |
| T7 External Capability Gateway | not_started | No gateway implementation yet |
| T8 Enterprise Hardening | in_progress | ADR-001 execution sidecar design. TS operator contracts. Policy-driven redaction. Contract-first boundaries improving. |

## Epic Snapshot

### T1 Platform Foundation

| Epic | Status | Notes |
|------|--------|-------|
| T1-E1 Canonical Job Model | in_progress | `ReviewJob` with from_dict() recovery exists, but system still also has `JobRunner`, `Task`, and `AgentLoop` models |
| T1-E2 Artifact-First Execution | mostly_complete | Full payload persistence + recovery. Artifacts contain content, not just metadata. T1-E2-S5 closed. |
| T1-E3 Workspace And Execution Discipline | in_progress | ReviewService accepts WorkspaceManager, execution mode is explicit (READ_ONLY_HOST/WORKSPACE_BOUND). T1-E3-S5 partially closed. |

### T2 Reviewer Product

| Epic | Status | Notes |
|------|--------|-------|
| T2-E1 Review Job Types | mostly_complete | `repo_audit`, `pr_review`, and `release_review` exist in reviewer context |
| T2-E2 Review Output Standardization | mostly_complete | Canonical report, severity, Markdown and JSON exports exist |
| T2-E3 Review Verification | mostly_complete | Verifier pass exists and is tested |
| T2-E4 Review Delivery | complete_for_phase | Delivery bundle, approval gating (request_delivery_approval), client-safe redaction (get_client_safe_bundle). External send workflow is Operator scope. |

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
| T5-E1 Policy Control Plane | in_progress | Reviewer execution trace now includes execution_policy step with mode/source/access info (T5-E1-S5). Still not unified with tool policy engine. |
| T5-E2 Approval Model | in_progress | Approval foundations exist, but review delivery approval is not yet wired |
| T5-E3 Client-Safe And Secret-Safe Output | mostly_complete | get_client_safe_bundle() redacts paths and strips trace. Secret evidence redacted in analyzers. |

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
| T8-E1 Contract-First Boundaries | mostly_complete | ReviewService is runtime path. Execution sidecar boundary defined (ADR-001). TS operator contracts defined. Legacy review deprecated. |
| T8-E2 Deployment And Environment Profiles | in_progress | Centralized project-root. TS operator surface foundation. ADR-001 defines sidecar deployment model. |
| T8-E3 Compliance-Friendly Foundations | in_progress | Policy-driven redaction module. Client-safe export mode. Delivery approval gating (strict, no bypass without DEV_MODE). |

## Current Strategic Interpretation

Reviewer v1 is now `mostly_complete`:
- recovery-safe job storage with full from_dict() reconstruction
- artifacts with full content payloads (not just metadata)
- explicit execution mode (READ_ONLY_HOST / WORKSPACE_BOUND)
- Telegram /review routes through ReviewService
- execution policy audit trace on every review job

Reviewer v1 is now `complete_for_phase`:
- delivery approval gating: request_delivery_approval() creates approval request
- client-safe export: get_client_safe_bundle() redacts paths, strips trace
- PR review: tested with real git repo fixture (init + commits + diff)
- legacy Programmer.review_file() deprecated with DeprecationWarning

Remaining for Reviewer v2:
- LLM-augmented analysis (deterministic-only in v1)
- external delivery send workflow (Operator scope)
- Programmer.review_file() full removal
