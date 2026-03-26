# Themes, Epics, Stories

This document is the human planning decomposition derived from
`MASTER_SOURCE_OF_TRUTH.md`.

Use it for:
- roadmap planning
- backlog creation
- Claude Code task generation
- architecture conversations

## Current Progress Snapshot

This backlog is currently assessed against PR `#44` at commit `8537f26`.

Important:
- this is a strategy progress snapshot, not a merge-state indicator
- `main` may still lag behind this assessed implementation state

Status summary:
- T1 Platform Foundation: `in_progress`
- T2 Reviewer Product: `in_progress`
- T3 Builder Product: `not_started`
- T4 Operator Product: `not_started`
- T5 Security, Governance, And Policy: `in_progress`
- T6 Cost, Usage, And Observability: `started`
- T7 External Capability Gateway: `not_started`
- T8 Enterprise Hardening: `started`

## Theme T1: Platform Foundation

Goal: unify the core job, state, artifact, and execution foundation so the
system can support reviewer, builder, and operator modes without fragmenting.

### Epic T1-E1: Canonical Job Model

Stories:
- T1-E1-S1: Define canonical Job schema for review, build, operate, and delivery
  flows.
- T1-E1-S2: Add Job lifecycle states and recovery rules.
- T1-E1-S3: Persist job metadata, execution history, artifacts, and cost data.
- T1-E1-S4: Add job queries for operator inspection and automation.
- T1-E1-S5: Reconcile coexistence rules between `ReviewJob`, `JobRunner`,
  `Task`, and `AgentLoop`.

### Epic T1-E2: Artifact-First Execution

Stories:
- T1-E2-S1: Define artifact types and storage layout.
- T1-E2-S2: Link artifacts to jobs and execution traces.
- T1-E2-S3: Add export support for Markdown, JSON, and delivery bundles.
- T1-E2-S4: Add artifact retention and recovery rules.
- T1-E2-S5: Persist full intake, report payloads, and artifact payloads for
  recovery-safe reload.

### Epic T1-E3: Workspace And Execution Discipline

Stories:
- T1-E3-S1: Ensure all mutable engineering work happens in isolated workspaces.
- T1-E3-S2: Make workspace lifecycle, audit, and recovery robust.
- T1-E3-S3: Link workspace records to jobs, artifacts, and approvals.
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

Goal: make implementation work first-class, controlled, and acceptance-driven.

### Epic T3-E1: Capability-Based Build Execution

Stories:
- T3-E1-S1: Define implementation capability catalog for backend, frontend,
  integration, and devops work.
- T3-E1-S2: Route implementation jobs to explicit capabilities.
- T3-E1-S3: Capture patch sets, diffs, and execution traces as artifacts.
- T3-E1-S4: Make execution resumable after interruption.

### Epic T3-E2: Build Verification Loop

Stories:
- T3-E2-S1: Add test, lint, and type-check loop for implementation jobs.
- T3-E2-S2: Add review-after-build pass before completion.
- T3-E2-S3: Fail jobs clearly when acceptance criteria are not met.
- T3-E2-S4: Capture all verification artifacts and verdicts.

### Epic T3-E3: Acceptance Criteria Engine

Stories:
- T3-E3-S1: Define acceptance criteria object model.
- T3-E3-S2: Attach acceptance criteria to jobs.
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

Stories:
- T8-E1-S1: Define contracts between control plane, execution plane, verification,
  and delivery.
- T8-E1-S2: Remove hidden coupling and implicit shared state.
- T8-E1-S3: Add architecture invariants for contracts and boundaries.
- T8-E1-S4: Make future service extraction obvious from module boundaries.
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
