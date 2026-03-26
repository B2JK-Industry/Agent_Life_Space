# Themes, Epics, Stories

This document is the human planning decomposition derived from
`MASTER_SOURCE_OF_TRUTH.md`.

Use it for:
- roadmap planning
- backlog creation
- Claude Code task generation
- architecture conversations

## Current Main Snapshot

This file is now assessed against current `main`, not an older PR snapshot.

Assessment basis:
- branch: `main`
- commit: `ccfd969`
- interpretation date: 2026-03-26

Important:
- this is a product-and-architecture progress view, not a merge history log
- percentages below are directional, not exact velocity math
- `complete_for_phase` means "good enough for the current bounded-context phase",
  not "strategically finished forever"

Status legend:
- `not_started`: no meaningful implementation yet
- `started`: foundations or isolated pieces exist
- `in_progress`: meaningful implementation exists, but important gaps remain
- `mostly_complete`: usable bounded-context slice exists, but still leaks or drifts
- `complete_for_phase`: the current phase goal is sufficiently closed

## Theme Snapshot

| Theme | Status | Approx Progress | What Exists On `main` | Main Remaining Gap |
|------|--------|-----------------|------------------------|--------------------|
| T1 Platform Foundation | in_progress | 45% | ReviewJob, artifact storage, workspace subsystem, execution trace | No canonical cross-system job model yet |
| T2 Reviewer Product | mostly_complete | 80% | Reviewer bounded context, verifier, delivery bundle, approval hook, Telegram path | Delivery is not fully operator/API complete and reviewer execution truth still leaks |
| T3 Builder Product | not_started | 5% | Generic workspace/test foundations only | No first-class builder slice |
| T4 Operator Product | not_started | 5% | Partial approval and delivery foundations only | No intake, planning, or delivery control plane |
| T5 Security, Governance, And Policy | in_progress | 40% | Tool policy, reviewer execution trace, approval queue, client-safe export foundation | Review repo/diff execution is still outside a unified policy boundary |
| T6 Cost, Usage, And Observability | started | 20% | Status, usage fields, broad test discipline | No real per-job cost ledger or operator-facing observability surface |
| T7 External Capability Gateway | not_started | 0% | Nothing first-class yet | No gateway contract or provider integration |
| T8 Enterprise Hardening | started | 25% | Review bounded context, some invariants, project-root discipline | No true control-plane contract layer or extraction-ready boundaries yet |

## Theme T1: Platform Foundation

Goal: unify the core job, state, artifact, and execution foundation so the
system can support reviewer, builder, and operator modes without fragmenting.

Status: `in_progress`
Approx progress: `45%`
Main gap: `the system still has multiple parallel work models instead of one canonical Job layer`

### Epic T1-E1: Canonical Job Model

Status: `in_progress`
Approx progress: `30%`
Remaining gap: `ReviewJob is strong locally, but JobRunner, Task, and AgentLoop still coexist without convergence`

Stories:
- T1-E1-S1: Define canonical Job schema for review, build, operate, and delivery flows.
  Status: `started`
  Current state: `ReviewJob` exists for reviewer only.
  Missing: system-wide shared Job schema.
- T1-E1-S2: Add Job lifecycle states and recovery rules.
  Status: `started`
  Current state: reviewer lifecycle exists and reload works.
  Missing: one lifecycle model across review, build, operate, and delivery.
- T1-E1-S3: Persist job metadata, execution history, artifacts, and cost data.
  Status: `started`
  Current state: reviewer jobs persist metadata, report, and traces.
  Missing: unified cost/execution persistence across all job types.
- T1-E1-S4: Add job queries for operator inspection and automation.
  Status: `not_started`
  Current state: ad-hoc listing exists only inside reviewer storage.
  Missing: cross-job query layer and operator inspection surface.
- T1-E1-S5: Reconcile coexistence rules between `ReviewJob`, `JobRunner`, `Task`, and `AgentLoop`.
  Status: `started`
  Current state: coexistence is visible and documented.
  Missing: explicit convergence rules or phased deprecation plan.

### Epic T1-E2: Artifact-First Execution

Status: `mostly_complete`
Approx progress: `70%`
Remaining gap: `artifact persistence is strong, but artifact hydration is not yet the single source of truth`

Stories:
- T1-E2-S1: Define artifact types and storage layout.
  Status: `mostly_complete`
  Current state: reviewer artifact types and SQLite storage exist.
  Missing: broader artifact taxonomy beyond reviewer flow.
- T1-E2-S2: Link artifacts to jobs and execution traces.
  Status: `mostly_complete`
  Current state: reviewer artifacts link to job id and trace export.
  Missing: shared artifact model across future build/operator flows.
- T1-E2-S3: Add export support for Markdown, JSON, and delivery bundles.
  Status: `complete_for_phase`
  Current state: reviewer exports Markdown, JSON, findings, and delivery bundle.
  Missing: none for Reviewer v1.
- T1-E2-S4: Add artifact retention and recovery rules.
  Status: `started`
  Current state: basic cleanup exists.
  Missing: explicit retention policy, export policy, and replay policy.
- T1-E2-S5: Persist full intake, report payloads, and artifact payloads for recovery-safe reload.
  Status: `mostly_complete`
  Current state: intake/report payloads persist; artifact payloads are retrievable.
  Missing: fully hydrated artifact graph on `ReviewJob` reload.

### Epic T1-E3: Workspace And Execution Discipline

Status: `in_progress`
Approx progress: `40%`
Remaining gap: `reviewer execution mode is explicit, but not fully truthful`

Stories:
- T1-E3-S1: Ensure all mutable engineering work happens in isolated workspaces.
  Status: `started`
  Current state: workspace subsystem exists.
  Missing: builder execution is not using it yet.
- T1-E3-S2: Make workspace lifecycle, audit, and recovery robust.
  Status: `mostly_complete`
  Current state: workspace persistence and recovery tests exist.
  Missing: stronger linkage into all work modes.
- T1-E3-S3: Link workspace records to jobs, artifacts, and approvals.
  Status: `started`
  Current state: reviewer records `workspace_id`.
  Missing: consistent job/workspace/artifact/approval linkage.
- T1-E3-S4: Add environment profiles for safe execution modes.
  Status: `started`
  Current state: sandbox and project-root discipline exist.
  Missing: explicit profile matrix for local/operator/enterprise modes.
- T1-E3-S5: Bind reviewer jobs to workspace discipline or define explicit read-only review execution mode.
  Status: `in_progress`
  Current state: `ExecutionMode` exists.
  Missing: `WORKSPACE_BOUND` review is still not fully truthful because analyzers use host repo paths.

## Theme T2: Reviewer Product

Goal: make review a first-class client-ready workflow.

Status: `mostly_complete`
Approx progress: `80%`
Main gap: `Reviewer v1 is strong, but delivery, entrypoints, and execution truth are not fully closed at system level`

### Epic T2-E1: Review Job Types

Status: `mostly_complete`
Approx progress: `80%`
Remaining gap: `job types exist, but PR review and release review are still v1-grade, not deeply productized`

Stories:
- T2-E1-S1: Implement repo audit job type.
  Status: `complete_for_phase`
  Current state: implemented and tested.
  Missing: LLM-augmented depth in Reviewer v2.
- T2-E1-S2: Implement PR review job type.
  Status: `in_progress`
  Current state: implemented with fixtures.
  Missing: stronger delivery/report specialization and broader fixture coverage.
- T2-E1-S3: Implement release readiness review job type.
  Status: `mostly_complete`
  Current state: implemented as review overlay.
  Missing: richer release-specific checks and packaging.
- T2-E1-S4: Add shared review job interface and input validation.
  Status: `mostly_complete`
  Current state: intake model and validation exist.
  Missing: unified intake beyond local reviewer flow.

### Epic T2-E2: Review Output Standardization

Status: `mostly_complete`
Approx progress: `85%`
Remaining gap: `output is standardized, but not yet specialized for all delivery channels`

Stories:
- T2-E2-S1: Define canonical report structure with executive summary and findings.
  Status: `complete_for_phase`
  Current state: implemented.
  Missing: none for Reviewer v1.
- T2-E2-S2: Standardize severity, file references, and recommended fixes.
  Status: `complete_for_phase`
  Current state: implemented in review model and Markdown export.
  Missing: none for Reviewer v1.
- T2-E2-S3: Add explicit assumptions, open questions, and low-confidence language.
  Status: `mostly_complete`
  Current state: assumptions/open questions/confidence exist.
  Missing: stronger low-confidence policy language in more review paths.
- T2-E2-S4: Export reports to Markdown and JSON.
  Status: `complete_for_phase`
  Current state: implemented.
  Missing: none for Reviewer v1.

### Epic T2-E3: Review Verification

Status: `mostly_complete`
Approx progress: `75%`
Remaining gap: `verifier exists, but no golden-eval-backed quality regime yet`

Stories:
- T2-E3-S1: Add verifier pass for review output.
  Status: `complete_for_phase`
  Current state: implemented.
  Missing: none for Reviewer v1.
- T2-E3-S2: Add false-positive reduction strategy.
  Status: `mostly_complete`
  Current state: verifier reduces weak findings.
  Missing: benchmarked precision/recall discipline.
- T2-E3-S3: Add consistency checks for severity and evidence.
  Status: `mostly_complete`
  Current state: some consistency filtering exists.
  Missing: stronger severity calibration and evals.
- T2-E3-S4: Record review confidence and verification result.
  Status: `mostly_complete`
  Current state: confidence is modeled.
  Missing: more explicit verification result artifact.

### Epic T2-E4: Review Delivery

Status: `in_progress`
Approx progress: `70%`
Remaining gap: `delivery foundations exist, but API path, PR comment packs, and harder delivery governance are still open`

Stories:
- T2-E4-S1: Prepare copy-paste-ready PR comments and summary review artifacts.
  Status: `not_started`
  Current state: no dedicated PR comment pack artifact.
  Missing: PR-comment-ready delivery format.
- T2-E4-S2: Add delivery approval before external send.
  Status: `in_progress`
  Current state: approval request hook exists.
  Missing: no-bypass production-safe enforcement and external send workflow.
- T2-E4-S3: Add client-safe output mode with redaction.
  Status: `mostly_complete`
  Current state: client-safe bundle exists.
  Missing: stronger redaction policy and metadata minimization.
- T2-E4-S4: Add report packaging for operator handoff.
  Status: `mostly_complete`
  Current state: delivery bundle exists.
  Missing: operator-facing handoff surface and richer package semantics.
- T2-E4-S5: Route Telegram and API review entrypoints through `ReviewService` instead of legacy reviewer paths.
  Status: `in_progress`
  Current state: Telegram `/review` routes through `ReviewService`.
  Missing: API review entrypoint is not first-class yet.

## Theme T3: Builder Product

Goal: make implementation work first-class, controlled, and acceptance-driven.

Status: `not_started`
Approx progress: `5%`
Main gap: `there is no builder product slice yet`

### Epic T3-E1: Capability-Based Build Execution

Status: `not_started`
Approx progress: `5%`
Remaining gap: `generic execution primitives exist, but no builder capability model`

Stories:
- T3-E1-S1: Define implementation capability catalog for backend, frontend, integration, and devops work.
  Status: `not_started`
- T3-E1-S2: Route implementation jobs to explicit capabilities.
  Status: `not_started`
- T3-E1-S3: Capture patch sets, diffs, and execution traces as artifacts.
  Status: `started`
  Current state: reviewer artifacts exist, but not builder patch artifacts.
- T3-E1-S4: Make execution resumable after interruption.
  Status: `started`
  Current state: some workspace recovery primitives exist.
  Missing: builder-specific resumable flow.

### Epic T3-E2: Build Verification Loop

Status: `started`
Approx progress: `10%`
Remaining gap: `tests and lint exist globally, not as a builder job loop`

Stories:
- T3-E2-S1: Add test, lint, and type-check loop for implementation jobs.
  Status: `started`
  Current state: project-wide checks exist.
  Missing: builder-job execution loop.
- T3-E2-S2: Add review-after-build pass before completion.
  Status: `not_started`
- T3-E2-S3: Fail jobs clearly when acceptance criteria are not met.
  Status: `not_started`
- T3-E2-S4: Capture all verification artifacts and verdicts.
  Status: `started`
  Current state: reviewer verification artifacts exist.
  Missing: builder verification artifacts.

### Epic T3-E3: Acceptance Criteria Engine

Status: `not_started`
Approx progress: `0%`
Remaining gap: `acceptance criteria is still a strategic concept, not a first-class model`

Stories:
- T3-E3-S1: Define acceptance criteria object model.
  Status: `not_started`
- T3-E3-S2: Attach acceptance criteria to jobs.
  Status: `not_started`
- T3-E3-S3: Validate criteria at completion time.
  Status: `not_started`
- T3-E3-S4: Produce acceptance reports for delivery.
  Status: `not_started`

## Theme T4: Operator Product

Goal: coordinate work end-to-end and support repeatable client delivery.

Status: `not_started`
Approx progress: `5%`
Main gap: `there is no first-class operator control plane yet`

### Epic T4-E1: Intake And Qualification

Status: `not_started`
Approx progress: `10%`
Remaining gap: `review intake exists locally, but there is no operator intake model`

Stories:
- T4-E1-S1: Create intake model for repo paths, git URLs, diff ranges, and work type.
  Status: `started`
  Current state: reviewer intake exists.
  Missing: operator-wide intake model.
- T4-E1-S2: Add qualification logic for scope, risk, and budget.
  Status: `not_started`
- T4-E1-S3: Add operator-facing intake summary and recommended plan.
  Status: `not_started`
- T4-E1-S4: Reject unsupported work cleanly and honestly.
  Status: `started`
  Current state: reviewer validation rejects bad input.
  Missing: operator-level qualification failure handling.

### Epic T4-E2: Job Planning And Routing

Status: `not_started`
Approx progress: `5%`
Remaining gap: `chat routing exists, but no JobPlan layer exists`

Stories:
- T4-E2-S1: Create JobPlan model and planner outputs.
  Status: `not_started`
- T4-E2-S2: Split work into review, build, verify, deliver phases.
  Status: `not_started`
- T4-E2-S3: Assign capabilities and budget envelopes.
  Status: `not_started`
- T4-E2-S4: Record execution traces for planning decisions.
  Status: `started`
  Current state: execution traces exist inside reviewer.
  Missing: planning-specific trace model.

### Epic T4-E3: Delivery Workflow

Status: `not_started`
Approx progress: `10%`
Remaining gap: `delivery bundle exists, but no operator delivery workflow exists`

Stories:
- T4-E3-S1: Define delivery package model.
  Status: `started`
  Current state: reviewer delivery bundle is a local precursor.
  Missing: system-level delivery package.
- T4-E3-S2: Assemble artifacts, reports, and acceptance results into delivery bundles.
  Status: `started`
  Current state: reviewer delivery bundle exists.
  Missing: build/operator inputs and acceptance results.
- T4-E3-S3: Gate delivery through approvals.
  Status: `started`
  Current state: reviewer approval hook exists.
  Missing: full operator delivery gating.
- T4-E3-S4: Record delivery status and handoff audit events.
  Status: `not_started`

## Theme T5: Security, Governance, And Policy

Goal: ensure all valuable workflows remain controlled and auditable.

Status: `in_progress`
Approx progress: `40%`
Main gap: `policy is strong for tools, but reviewer host execution still sits outside one unified control boundary`

### Epic T5-E1: Policy Control Plane

Status: `in_progress`
Approx progress: `45%`
Remaining gap: `review repo/diff execution is still not fully inside shared execution policy`

Stories:
- T5-E1-S1: Extend policy model to job, artifact, delivery, and external gateway decisions.
  Status: `started`
  Current state: some delivery and tool decisions are explicit.
  Missing: one shared policy surface for jobs, artifacts, and gateway use.
- T5-E1-S2: Keep policy deny-by-default across execution modes.
  Status: `in_progress`
  Current state: tool policy is deny-by-default.
  Missing: reviewer execution modes are not governed by the same engine.
- T5-E1-S3: Add structured denial reasons everywhere.
  Status: `started`
  Current state: tool policy has denial codes.
  Missing: review and delivery paths are not uniformly coded.
- T5-E1-S4: Ensure policy is deterministic and separately testable.
  Status: `mostly_complete`
  Current state: tool policy testing is strong.
  Missing: broader policy coverage for review execution and delivery.
- T5-E1-S5: Bring repository and diff analysis under the shared execution and policy boundary.
  Status: `started`
  Current state: execution trace records mode/source.
  Missing: actual shared execution boundary.

### Epic T5-E2: Approval Model

Status: `started`
Approx progress: `30%`
Remaining gap: `approval exists, but persistence and linkage are still shallow`

Stories:
- T5-E2-S1: Make approval requests persistent and queryable.
  Status: `started`
  Current state: in-memory queue exists.
  Missing: durable persistent approval store.
- T5-E2-S2: Support approvals for risky execution and external delivery.
  Status: `in_progress`
  Current state: tools and reviewer delivery can request approvals.
  Missing: wider execution coverage.
- T5-E2-S3: Support multi-step approvals where needed.
  Status: `not_started`
- T5-E2-S4: Link approvals to jobs, artifacts, and delivery bundles.
  Status: `not_started`

### Epic T5-E3: Client-Safe And Secret-Safe Output

Status: `in_progress`
Approx progress: `55%`
Remaining gap: `redaction exists, but it is still narrow and reviewer-specific`

Stories:
- T5-E3-S1: Enforce redaction on reports and logs.
  Status: `started`
  Current state: reviewer bundle redaction exists.
  Missing: logs and wider system outputs.
- T5-E3-S2: Prevent sensitive internal data in client output.
  Status: `started`
  Current state: paths and some evidence are redacted.
  Missing: stronger metadata minimization and stricter policy.
- T5-E3-S3: Add client-safe review mode.
  Status: `mostly_complete`
  Current state: client-safe bundle exists.
  Missing: broader contract and stronger enforcement.
- T5-E3-S4: Add security regression tests around redaction and delivery.
  Status: `started`
  Current state: reviewer redaction tests exist.
  Missing: deeper regression coverage.

## Theme T6: Cost, Usage, And Observability

Goal: make the system operable, measurable, and economically sane.

Status: `started`
Approx progress: `20%`
Main gap: `reviewer has placeholders, but no real operator-grade cost and observability layer`

### Epic T6-E1: Cost Ledger

Status: `started`
Approx progress: `15%`
Remaining gap: `fields exist, but the ledger does not drive decisions yet`

Stories:
- T6-E1-S1: Record per-job model usage and token cost.
  Status: `started`
  Current state: fields exist on review job.
  Missing: real recording path.
- T6-E1-S2: Add hard budget, soft budget, and stop-loss behavior.
  Status: `not_started`
- T6-E1-S3: Make escalation budget-aware.
  Status: `not_started`
- T6-E1-S4: Surface cost and margin hints to the operator.
  Status: `not_started`

### Epic T6-E2: Runtime Observability

Status: `started`
Approx progress: `20%`
Remaining gap: `status and traces exist, but no operator-facing observability surface`

Stories:
- T6-E2-S1: Track job status, failures, retries, and durations.
  Status: `started`
  Current state: reviewer jobs and traces track this locally.
  Missing: cross-system job observability.
- T6-E2-S2: Track worker execution and workspace health.
  Status: `started`
  Current state: workspace subsystem has lifecycle and tests.
  Missing: operator-facing aggregation.
- T6-E2-S3: Track approval backlog and blocked reasons.
  Status: `started`
  Current state: approval queue exists.
  Missing: persistent/operator-facing backlog reporting.
- T6-E2-S4: Add operator-facing reporting surface or inbox.
  Status: `not_started`

### Epic T6-E3: Quality Evals

Status: `started`
Approx progress: `20%`
Remaining gap: `tests are strong, but evals are not yet a product-quality discipline`

Stories:
- T6-E3-S1: Build golden review cases.
  Status: `not_started`
- T6-E3-S2: Measure finding precision and false positives.
  Status: `not_started`
- T6-E3-S3: Add review eval smoke checks to CI or local gating.
  Status: `not_started`
- T6-E3-S4: Track latency and quality regression across versions.
  Status: `started`
  Current state: performance budget and test count checks exist.
  Missing: reviewer-quality regression metrics.

## Theme T7: External Capability Gateway

Goal: integrate external capability providers cleanly and safely.

Status: `not_started`
Approx progress: `0%`
Main gap: `the gateway does not exist yet`

### Epic T7-E1: Gateway Foundation

Status: `not_started`
Approx progress: `0%`

Stories:
- T7-E1-S1: Define gateway contract for external capabilities.
  Status: `not_started`
- T7-E1-S2: Add auth, timeout, retry, and rate-limit policy.
  Status: `not_started`
- T7-E1-S3: Add audit and cost tracking for external calls.
  Status: `not_started`
- T7-E1-S4: Add policy gating for when external capability use is allowed.
  Status: `not_started`

### Epic T7-E2: obolos.tech Integration

Status: `not_started`
Approx progress: `0%`

Stories:
- T7-E2-S1: Represent obolos.tech capabilities through the gateway model.
  Status: `not_started`
- T7-E2-S2: Add capability catalog and routing logic.
  Status: `not_started`
- T7-E2-S3: Add fallback and failure handling.
  Status: `not_started`
- T7-E2-S4: Add tests for gateway policy and error modes.
  Status: `not_started`

## Theme T8: Enterprise Hardening

Goal: prepare the system to evolve into enterprise-grade architecture without
premature fragmentation.

Status: `started`
Approx progress: `25%`
Main gap: `bounded contexts are emerging, but there is still no shared control-plane contract layer`

### Epic T8-E1: Contract-First Boundaries

Status: `in_progress`
Approx progress: `35%`
Remaining gap: `review boundaries improved, but broader contracts are still implicit`

Stories:
- T8-E1-S1: Define contracts between control plane, execution plane, verification, and delivery.
  Status: `started`
  Current state: strategy defines them.
  Missing: runtime contracts and code-level interfaces.
- T8-E1-S2: Remove hidden coupling and implicit shared state.
  Status: `started`
  Current state: some reviewer coupling was removed.
  Missing: shared-state cleanup across the rest of the system.
- T8-E1-S3: Add architecture invariants for contracts and boundaries.
  Status: `started`
  Current state: CI invariants exist.
  Missing: stronger coverage for new bounded contexts.
- T8-E1-S4: Make future service extraction obvious from module boundaries.
  Status: `started`
  Current state: reviewer bounded context helps.
  Missing: builder/operator bounded contexts.
- T8-E1-S5: Remove duplicated reviewer flows and hidden channel-to-product coupling.
  Status: `mostly_complete`
  Current state: Telegram `/review` now uses `ReviewService`.
  Missing: API parity and final legacy cleanup.

### Epic T8-E2: Deployment And Environment Profiles

Status: `started`
Approx progress: `20%`
Remaining gap: `config discipline is improving, but environment profiles are not defined as a real matrix`

Stories:
- T8-E2-S1: Define local, operator, and enterprise environment profiles.
  Status: `not_started`
- T8-E2-S2: Make environment-sensitive behavior explicit and testable.
  Status: `started`
  Current state: some env-driven behavior exists.
  Missing: explicit profile tests.
- T8-E2-S3: Add configuration discipline for project roots, secrets, and storage.
  Status: `started`
  Current state: project-root discipline improved.
  Missing: broader config contract.
- T8-E2-S4: Add deployment documentation for controlled environments.
  Status: `started`
  Current state: partial docs exist.
  Missing: operator/enterprise deployment playbooks.

### Epic T8-E3: Compliance-Friendly Foundations

Status: `started`
Approx progress: `20%`
Remaining gap: `traceability exists, but compliance-friendly packaging and retention are still early`

Stories:
- T8-E3-S1: Improve audit export and artifact traceability.
  Status: `started`
  Current state: reviewer artifacts and traces exist.
  Missing: broader export discipline.
- T8-E3-S2: Improve redaction and retention strategy.
  Status: `started`
  Current state: basic redaction and cleanup exist.
  Missing: policy-driven retention/redaction.
- T8-E3-S3: Support client-safe evidence packaging.
  Status: `started`
  Current state: client-safe bundle exists.
  Missing: stronger evidence packaging rules.
- T8-E3-S4: Prepare data-handling rules for future enterprise requirements.
  Status: `not_started`

## Bottom Line

Current `main` is best described as:
- a strong reviewer-first product slice
- plus good governance and workspace foundations
- plus a still-unfinished platform convergence story

It is not yet:
- a full builder product
- a full operator control plane
- a gateway-enabled capability fabric
- an enterprise-ready system in operational reality

The next most leveraged strategic move is:
- stop polishing Reviewer v1 semantics in isolation
- refresh strategy/progress docs everywhere to match `main`
- then start the first real Builder foundation slice
