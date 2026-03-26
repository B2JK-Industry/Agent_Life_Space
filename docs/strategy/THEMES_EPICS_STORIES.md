# Themes, Epics, Stories

This document is the human planning decomposition derived from
`MASTER_SOURCE_OF_TRUTH.md`.

Use it for:
- roadmap planning
- backlog creation
- Claude Code task generation
- architecture conversations

## Current Progress Snapshot

Assessment basis: after Builder Foundation + Control-Plane Convergence.

Important:
- this is a strategy progress snapshot, not a merge-state indicator
- `main` may still lag behind this assessed implementation state

### Theme Snapshot

| Theme | Status | Approx Progress | Current State | Gap |
|-------|--------|-----------------|---------------|-----|
| T1 Platform Foundation | `in_progress` | 60% | Shared control-plane primitives (JobKind, JobStatus, JobTiming, ExecutionStep, ArtifactKind, UsageSummary). Review service, workspace discipline, artifact persistence. | ReviewJob not yet on shared primitives. |
| T2 Reviewer Product | `in_progress` | — | — | — |
| T3 Builder Product | `in_progress` | 35% | Builder bounded context with models, service, storage, verification loop, acceptance criteria. Workspace-first. | Build step is placeholder. No LLM implementation. |
| T4 Operator Product | `not_started` | — | — | — |
| T5 Security, Governance, And Policy | `in_progress` | — | — | — |
| T6 Cost, Usage, And Observability | `started` | — | — | — |
| T7 External Capability Gateway | `not_started` | — | — | — |
| T8 Enterprise Hardening | `started` | 35% | — | — |

## Theme T1: Platform Foundation

- approx_progress: 60%

Goal: unify the core job, state, artifact, and execution foundation so the
system can support reviewer, builder, and operator modes without fragmenting.

### Epic T1-E1: Canonical Job Model

- approx_progress: 45%
- remaining_gap: Shared primitives exist (JobKind, JobStatus, etc). BuildJob uses them. ReviewJob still uses own equivalents.

Stories:
- T1-E1-S1: Define canonical Job schema for review, build, operate, and delivery
  flows.
  - status: `in_progress`
  - current_state: Shared JobKind, JobStatus, JobTiming, ExecutionStep, ArtifactKind, ArtifactRef, UsageSummary exist in agent/control/models.py. BuildJob uses them directly.
  - missing: ReviewJob still has own equivalents. Migration pending.
- T1-E1-S2: Add Job lifecycle states and recovery rules.
  - status: `in_progress`
  - current_state: Shared JobStatus with CREATED/VALIDATING/RUNNING/VERIFYING/COMPLETED/FAILED/CANCELLED/BLOCKED. JobTiming with mark_started/mark_completed.
  - missing: ReviewJob uses own ReviewJobStatus.
- T1-E1-S3: Persist job metadata, execution history, artifacts, and cost data.
  - status: `in_progress`
  - current_state: BuildStorage persists job metadata, execution history, artifacts. UsageSummary tracks cost.
  - missing: No unified cross-system persistence.
- T1-E1-S4: Add job queries for operator inspection and automation.
- T1-E1-S5: Reconcile coexistence rules between `ReviewJob`, `JobRunner`,
  `Task`, and `AgentLoop`.
  - current_state: BuildJob uses shared primitives. ReviewJob does not yet.

### Epic T1-E2: Artifact-First Execution

Stories:
- T1-E2-S1: Define artifact types and storage layout.
- T1-E2-S2: Link artifacts to jobs and execution traces.
- T1-E2-S3: Add export support for Markdown, JSON, and delivery bundles.
- T1-E2-S4: Add artifact retention and recovery rules.
- T1-E2-S5: Persist full intake, report payloads, and artifact payloads for
  recovery-safe reload.

### Epic T1-E3: Workspace And Execution Discipline

- approx_progress: 55%

Stories:
- T1-E3-S1: Ensure all mutable engineering work happens in isolated workspaces.
  - status: `in_progress`
  - current_state: Builder is workspace-first (WORKSPACE_BOUND default, requires WorkspaceManager).
  - missing: Builder workspace integration is foundation-grade.
- T1-E3-S2: Make workspace lifecycle, audit, and recovery robust.
- T1-E3-S3: Link workspace records to jobs, artifacts, and approvals.
  - status: `in_progress`
  - current_state: BuildJob carries workspace_id. Workspace linked to job lifecycle.
  - missing: No shared workspace/artifact/approval cross-linkage.
- T1-E3-S4: Add environment profiles for safe execution modes.
- T1-E3-S5: Bind reviewer jobs to workspace discipline or define explicit
  read-only review execution mode.

## Theme T2: Reviewer Product

Goal: make review a first-class client-ready workflow.

### Epic T2-E1: Review Job Types

Stories:
- T2-E1-S1: Implement repo audit job type.
- T2-E1-S2: Implement PR review job type.
- T2-E1-S3: Implement release readiness review job type.
- T2-E1-S4: Add shared review job interface and input validation.

### Epic T2-E2: Review Output Standardization

Stories:
- T2-E2-S1: Define canonical report structure with executive summary and findings.
- T2-E2-S2: Standardize severity, file references, and recommended fixes.
- T2-E2-S3: Add explicit assumptions, open questions, and low-confidence language.
- T2-E2-S4: Export reports to Markdown and JSON.

### Epic T2-E3: Review Verification

Stories:
- T2-E3-S1: Add verifier pass for review output.
- T2-E3-S2: Add false-positive reduction strategy.
- T2-E3-S3: Add consistency checks for severity and evidence.
- T2-E3-S4: Record review confidence and verification result.

### Epic T2-E4: Review Delivery

Stories:
- T2-E4-S1: Prepare copy-paste-ready PR comments and summary review artifacts.
- T2-E4-S2: Add delivery approval before external send.
- T2-E4-S3: Add client-safe output mode with redaction.
- T2-E4-S4: Add report packaging for operator handoff.
- T2-E4-S5: Route Telegram and API review entrypoints through `ReviewService`
  instead of legacy reviewer paths.

## Theme T3: Builder Product

- status: `in_progress`
- approx_progress: 35%

Goal: make implementation work first-class, controlled, and acceptance-driven.

### Epic T3-E1: Capability-Based Build Execution

- status: `in_progress`
- approx_progress: 35%
- remaining_gap: Build flow exists but build step is placeholder. No capability catalog.

Stories:
- T3-E1-S1: Define implementation capability catalog for backend, frontend,
  integration, and devops work.
  - status: `started`
  - current_state: BuildJobType enum (IMPLEMENTATION, INTEGRATION, DEVOPS, TESTING) exists.
  - missing: No capability catalog or routing.
- T3-E1-S2: Route implementation jobs to explicit capabilities.
  - status: `started`
  - current_state: BuildService routes by build_type.
  - missing: No explicit capability routing.
- T3-E1-S3: Capture patch sets, diffs, and execution traces as artifacts.
  - status: `in_progress`
  - current_state: BuildArtifact with PATCH, DIFF, VERIFICATION_REPORT, ACCEPTANCE_REPORT kinds. Artifacts persisted via BuildStorage.
- T3-E1-S4: Make execution resumable after interruption.
  - status: `in_progress`
  - current_state: BuildJob carries workspace_id, timing, execution_trace. From_dict() recovery exists.
  - missing: No resume-from-step capability.

### Epic T3-E2: Build Verification Loop

- status: `in_progress`
- approx_progress: 40%

Stories:
- T3-E2-S1: Add test, lint, and type-check loop for implementation jobs.
  - status: `in_progress`
  - current_state: run_verification_suite() runs test + lint in workspace. Custom commands supported.
  - missing: No typecheck by default. No build-specific test discovery.
- T3-E2-S2: Add review-after-build pass before completion.
- T3-E2-S3: Fail jobs clearly when acceptance criteria are not met.
  - status: `in_progress`
  - current_state: BuildService fails job when acceptance criteria unmet. AcceptanceVerdict.evaluate() checks all non-skipped criteria.
  - missing: Evaluation is simple (all-met-if-verification-passed).
- T3-E2-S4: Capture all verification artifacts and verdicts.
  - status: `in_progress`
  - current_state: Verification results are first-class BuildArtifact (VERIFICATION_REPORT kind). Persisted via BuildStorage.

### Epic T3-E3: Acceptance Criteria Engine

- status: `in_progress`
- approx_progress: 40%

Stories:
- T3-E3-S1: Define acceptance criteria object model.
  - status: `in_progress`
  - current_state: AcceptanceCriterion with CriterionKind (FUNCTIONAL, QUALITY, SECURITY, PERFORMANCE), CriterionStatus (PENDING, MET, UNMET, SKIPPED). meet()/fail()/skip() methods.
  - missing: No semantic evaluation.
- T3-E3-S2: Attach acceptance criteria to jobs.
  - status: `in_progress`
  - current_state: BuildIntake carries acceptance_criteria list. BuildJob inherits them. AcceptanceVerdict evaluates at completion.
  - missing: Criteria are simple — no automated requirement checking.
- T3-E3-S3: Validate criteria at completion time.
- T3-E3-S4: Produce acceptance reports for delivery.

## Theme T4: Operator Product

Goal: coordinate work end-to-end and support repeatable client delivery.

### Epic T4-E1: Intake And Qualification

Stories:
- T4-E1-S1: Create intake model for repo paths, git URLs, diff ranges, and work
  type.
- T4-E1-S2: Add qualification logic for scope, risk, and budget.
- T4-E1-S3: Add operator-facing intake summary and recommended plan.
- T4-E1-S4: Reject unsupported work cleanly and honestly.

### Epic T4-E2: Job Planning And Routing

Stories:
- T4-E2-S1: Create JobPlan model and planner outputs.
- T4-E2-S2: Split work into review, build, verify, deliver phases.
- T4-E2-S3: Assign capabilities and budget envelopes.
- T4-E2-S4: Record execution traces for planning decisions.

### Epic T4-E3: Delivery Workflow

Stories:
- T4-E3-S1: Define delivery package model.
- T4-E3-S2: Assemble artifacts, reports, and acceptance results into delivery
  bundles.
- T4-E3-S3: Gate delivery through approvals.
- T4-E3-S4: Record delivery status and handoff audit events.

## Theme T5: Security, Governance, And Policy

Goal: ensure all valuable workflows remain controlled and auditable.

### Epic T5-E1: Policy Control Plane

Stories:
- T5-E1-S1: Extend policy model to job, artifact, delivery, and external gateway
  decisions.
- T5-E1-S2: Keep policy deny-by-default across execution modes.
- T5-E1-S3: Add structured denial reasons everywhere.
- T5-E1-S4: Ensure policy is deterministic and separately testable.
- T5-E1-S5: Bring repository and diff analysis under the shared execution and
  policy boundary.

### Epic T5-E2: Approval Model

Stories:
- T5-E2-S1: Make approval requests persistent and queryable.
- T5-E2-S2: Support approvals for risky execution and external delivery.
- T5-E2-S3: Support multi-step approvals where needed.
- T5-E2-S4: Link approvals to jobs, artifacts, and delivery bundles.

### Epic T5-E3: Client-Safe And Secret-Safe Output

Stories:
- T5-E3-S1: Enforce redaction on reports and logs.
- T5-E3-S2: Prevent sensitive internal data in client output.
- T5-E3-S3: Add client-safe review mode.
- T5-E3-S4: Add security regression tests around redaction and delivery.

## Theme T6: Cost, Usage, And Observability

Goal: make the system operable, measurable, and economically sane.

### Epic T6-E1: Cost Ledger

Stories:
- T6-E1-S1: Record per-job model usage and token cost.
- T6-E1-S2: Add hard budget, soft budget, and stop-loss behavior.
- T6-E1-S3: Make escalation budget-aware.
- T6-E1-S4: Surface cost and margin hints to the operator.

### Epic T6-E2: Runtime Observability

Stories:
- T6-E2-S1: Track job status, failures, retries, and durations.
- T6-E2-S2: Track worker execution and workspace health.
- T6-E2-S3: Track approval backlog and blocked reasons.
- T6-E2-S4: Add operator-facing reporting surface or inbox.

### Epic T6-E3: Quality Evals

Stories:
- T6-E3-S1: Build golden review cases.
- T6-E3-S2: Measure finding precision and false positives.
- T6-E3-S3: Add review eval smoke checks to CI or local gating.
- T6-E3-S4: Track latency and quality regression across versions.

## Theme T7: External Capability Gateway

Goal: integrate external capability providers cleanly and safely.

### Epic T7-E1: Gateway Foundation

Stories:
- T7-E1-S1: Define gateway contract for external capabilities.
- T7-E1-S2: Add auth, timeout, retry, and rate-limit policy.
- T7-E1-S3: Add audit and cost tracking for external calls.
- T7-E1-S4: Add policy gating for when external capability use is allowed.

### Epic T7-E2: obolos.tech Integration

Stories:
- T7-E2-S1: Represent obolos.tech capabilities through the gateway model.
- T7-E2-S2: Add capability catalog and routing logic.
- T7-E2-S3: Add fallback and failure handling.
- T7-E2-S4: Add tests for gateway policy and error modes.

## Theme T8: Enterprise Hardening

Goal: prepare the system to evolve into enterprise-grade architecture without
premature fragmentation.

### Epic T8-E1: Contract-First Boundaries

- approx_progress: 45%
- remaining_gap: Shared control-plane primitives. Review and build as separate bounded contexts. ReviewJob not yet on shared primitives.

Stories:
- T8-E1-S1: Define contracts between control plane, execution plane, verification,
  and delivery.
  - status: `in_progress`
  - current_state: agent/control/models.py defines shared contracts: JobKind, JobStatus, ExecutionMode, JobTiming, ExecutionStep, ArtifactKind, ArtifactRef, UsageSummary.
- T8-E1-S2: Remove hidden coupling and implicit shared state.
- T8-E1-S3: Add architecture invariants for contracts and boundaries.
- T8-E1-S4: Make future service extraction obvious from module boundaries.
  - status: `in_progress`
  - current_state: agent/build/ is a clean bounded context: models.py, service.py, storage.py, verification.py. Parallel to agent/review/.
- T8-E1-S5: Remove duplicated reviewer flows and hidden channel-to-product
  coupling.

### Epic T8-E2: Deployment And Environment Profiles

Stories:
- T8-E2-S1: Define local, operator, and enterprise environment profiles.
- T8-E2-S2: Make environment-sensitive behavior explicit and testable.
- T8-E2-S3: Add configuration discipline for project roots, secrets, and storage.
- T8-E2-S4: Add deployment documentation for controlled environments.

### Epic T8-E3: Compliance-Friendly Foundations

Stories:
- T8-E3-S1: Improve audit export and artifact traceability.
- T8-E3-S2: Improve redaction and retention strategy.
- T8-E3-S3: Support client-safe evidence packaging.
- T8-E3-S4: Prepare data-handling rules for future enterprise requirements.

---

## Bottom Line

Builder exists as a real bounded context with models, service, storage,
verification loop, and acceptance criteria engine. Shared control-plane
primitives (JobKind, JobStatus, JobTiming, ExecutionStep, ArtifactKind,
UsageSummary) live in agent/control/models.py and BuildJob consumes them
directly. ReviewJob has not yet migrated to shared primitives — that is the
next convergence step. The build step itself is a placeholder (no LLM
implementation), but the surrounding structure (intake, workspace binding,
verification, acceptance, artifact persistence) is foundation-grade.
