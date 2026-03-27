# Themes, Epics, Stories

This document is the human planning decomposition derived from
`MASTER_SOURCE_OF_TRUTH.md`.

Use it for:
- roadmap planning
- backlog creation
- Claude Code task generation
- architecture conversations

## Current Progress Snapshot

Assessment basis: after Runtime Model + Artifact Planning slice.

Important:
- this is a strategy progress snapshot, not a merge-state indicator
- this snapshot now reflects the current state of `main` as of `2026-03-27`

### Theme Snapshot

| Theme | Status | Approx Progress | Current State | Gap |
|-------|--------|-----------------|---------------|-----|
| T1 Platform Foundation | `in_progress` | 84% | Shared control-plane primitives now back build and review directly, with explicit runtime coexistence rules plus shared job and artifact queries. | No unified cross-domain persistence/action layer yet. |
| T2 Reviewer Product | `complete_for_phase` | 85% | Reviewer bounded context, verifier, strict delivery gating, full client-safe redaction. | API entrypoint, PR comment packs, LLM analysis are v2. |
| T3 Builder Product | `in_progress` | 68% | Builder now has a declared capability catalog, resumable checkpoints, runtime/CLI entrypoints, workspace sync, verification, and review-after-build gating. | Build step is still placeholder and there is no full delivery path. |
| T4 Operator Product | `in_progress` | 42% | Unified intake routing, `JobPlan` preview/submit output, CLI intake preview/submit, operator report service, and mock-driven TS reporting surface. | No live backend/UI, durable planning state, or delivery workflow. |
| T5 Security, Governance, And Policy | `in_progress` | 60% | Tool policy deny-by-default, approval gating, redaction pipeline, and persistent/queryable approvals with job/artifact linkage. | Review/build still sit outside a unified policy boundary. |
| T6 Cost, Usage, And Observability | `in_progress` | 50% | UsageSummary, local build/review counters, shared runtime job/artifact queries, approval backlog visibility, and operator report surfaces. | No real cost ledger or live operator UI. |
| T7 External Capability Gateway | `not_started` | 0% | Nothing yet. | No gateway contract. |
| T8 Enterprise Hardening | `in_progress` | 64% | Shared control-plane layer, explicit runtime coexistence rules, direct review/build primitive reuse, unified intake/planning, and shared job/artifact/query/reporting surfaces. | Runtime boundaries are clearer, but not yet enforced as extraction-grade invariants. |

## Theme T1: Platform Foundation

- approx_progress: 84%

Goal: unify the core job, state, artifact, and execution foundation so the
system can support reviewer, builder, and operator modes without fragmenting.

### Epic T1-E1: Canonical Job Model

- approx_progress: 80%
- remaining_gap: Shared primitives and shared list/get query models now exist across build, review, operate, and artifact surfaces, but persistence/action unification is still incomplete.

Stories:
- T1-E1-S1: Define canonical Job schema for review, build, operate, and delivery
  flows.
  - status: `complete_for_phase`
  - current_state: Shared JobKind, JobStatus, JobTiming, ExecutionStep, ArtifactKind, ArtifactRef, and UsageSummary now back both BuildJob and ReviewJob directly.
  - missing: Delivery/operate convergence rules remain separate scope.
- T1-E1-S2: Add Job lifecycle states and recovery rules.
  - status: `complete_for_phase`
  - current_state: Shared JobStatus with CREATED/VALIDATING/RUNNING/VERIFYING/COMPLETED/FAILED/CANCELLED/BLOCKED and JobTiming with mark_started/mark_completed are now used by both build and review flows.
  - missing: Operate/delivery lifecycle harmonization remains.
- T1-E1-S3: Persist job metadata, execution history, artifacts, and cost data.
  - status: `in_progress`
  - current_state: BuildStorage persists job metadata, execution history, artifacts. UsageSummary tracks cost.
  - missing: No unified cross-system persistence.
- T1-E1-S4: Add job queries for operator inspection and automation.
  - status: `complete_for_phase`
  - current_state: JobQuerySummary/JobQueryDetail plus JobQueryService now normalize build, review, task, job-runner, and agent-loop records behind one shared list/get surface. AgentOrchestrator exposes `list_product_jobs()` and `get_product_job()`.
  - missing: Query actions remain read-only; planning/automation layers remain separate scope.
- T1-E1-S5: Reconcile coexistence rules between `ReviewJob`, `JobRunner`,
  `Task`, and `AgentLoop`.
  - status: `complete_for_phase`
  - current_state: `RuntimeModelService` now makes the coexistence rules explicit: `BuildJob`/`ReviewJob` are canonical product jobs, `Task` remains planning state, `JobRunner` remains infrastructure execution, and `AgentLoop` remains an ephemeral conversational queue.
  - missing: Rules are explicit, but not yet enforced through stronger invariants or migrations.

### Epic T1-E2: Artifact-First Execution

Stories:
- T1-E2-S1: Define artifact types and storage layout.
- T1-E2-S2: Link artifacts to jobs and execution traces.
- T1-E2-S3: Add export support for Markdown, JSON, and delivery bundles.
- T1-E2-S4: Add artifact retention and recovery rules.
- T1-E2-S5: Persist full intake, report payloads, and artifact payloads for
  recovery-safe reload.
  - status: `complete_for_phase`
  - current_state: `ArtifactQueryService` now exposes shared build/review artifact list/get recovery, and build/review storage both persist artifact `format` alongside content/content_json payloads.
  - missing: Artifact retention policy and richer builder patch export remain open.

### Epic T1-E3: Workspace And Execution Discipline

- approx_progress: 60%

Stories:
- T1-E3-S1: Ensure all mutable engineering work happens in isolated workspaces.
  - status: `in_progress`
  - current_state: Builder is workspace-first, requires WorkspaceManager, and now syncs the requested repo into the managed workspace before verification.
  - missing: Mutable execution is still builder-only; reviewer remains read-only in v1.
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
- approx_progress: 68%

Goal: make implementation work first-class, controlled, and acceptance-driven.

### Epic T3-E1: Capability-Based Build Execution

- status: `in_progress`
- approx_progress: 78%
- remaining_gap: Build flow now has runtime entrypoints, declared capabilities, and resumable checkpoints, but the build step is still placeholder-grade.

Stories:
- T3-E1-S1: Define implementation capability catalog for backend, frontend,
  integration, and devops work.
  - status: `complete_for_phase`
  - current_state: `agent/build/capabilities.py` now declares explicit implementation, integration, devops, and testing capabilities with verification defaults, target patterns, resume support, and review-after-build defaults.
  - missing: External provider routing remains future scope.
- T3-E1-S2: Expose builder through a real product entrypoint.
  - status: `mostly_complete`
  - current_state: `AgentOrchestrator.run_build_job()` now exposes builder through the shared runtime, and `python -m agent --build-repo ...` provides a thin CLI adapter on top of it.
  - missing: No operator/chat/API entrypoint yet, and capability catalog remains separate scope.
- T3-E1-S3: Capture patch sets, diffs, and execution traces as artifacts.
  - status: `in_progress`
  - current_state: BuildArtifact with PATCH, DIFF, VERIFICATION_REPORT, ACCEPTANCE_REPORT, REVIEW_REPORT, and FINDING_LIST kinds. Artifacts persisted via BuildStorage and surfaced through the tracked builder runtime.
- T3-E1-S4: Make execution resumable after interruption.
  - status: `mostly_complete`
  - current_state: BuildJob now records checkpoints, resume lineage, and can resume through `BuildService.resume_build()` and `python -m agent --build-resume ...`, reusing successful phases and rerunning failed ones.
  - missing: No planner-driven or distributed-worker resume policy yet.

### Epic T3-E2: Build Verification Loop

- status: `in_progress`
- approx_progress: 70%

Stories:
- T3-E2-S1: Add test, lint, and type-check loop for implementation jobs.
  - status: `in_progress`
  - current_state: run_verification_suite() runs test + lint and adds typecheck automatically when project config is present. Custom commands supported.
  - missing: No build-specific test discovery.
- T3-E2-S2: Add review-after-build pass before completion.
  - status: `mostly_complete`
  - current_state: Successful build jobs can now request a deterministic post-build reviewer pass through `ReviewService`. Critical reviewer failures block completion and are persisted as build artifacts/metadata.
  - missing: Review thresholds are not yet policy-configurable and reviewer still runs in READ_ONLY_HOST mode over the built workspace path.
- T3-E2-S3: Fail jobs clearly when acceptance criteria are not met.
  - status: `in_progress`
  - current_state: BuildService fails job when acceptance criteria unmet. AcceptanceVerdict.evaluate() checks all non-skipped criteria.
  - missing: Evaluation is still rule-based and limited; no semantic requirement engine.
- T3-E2-S4: Capture all verification artifacts and verdicts.
  - status: `in_progress`
  - current_state: Verification results are first-class BuildArtifact (VERIFICATION_REPORT kind). Persisted via BuildStorage.

### Epic T3-E3: Acceptance Criteria Engine

- status: `in_progress`
- approx_progress: 55%

Stories:
- T3-E3-S1: Define acceptance criteria object model.
  - status: `in_progress`
  - current_state: AcceptanceCriterion with CriterionKind (FUNCTIONAL, QUALITY, SECURITY, PERFORMANCE), CriterionStatus (PENDING, MET, UNMET, SKIPPED). meet()/fail()/skip() methods.
  - missing: No semantic evaluation beyond rule-based checks.
- T3-E3-S2: Attach acceptance criteria to jobs.
  - status: `in_progress`
  - current_state: BuildIntake carries acceptance_criteria list. BuildJob inherits them. AcceptanceVerdict evaluates at completion.
  - missing: Criteria are attached and checked, but richer requirement matching is still missing.
- T3-E3-S3: Validate criteria at completion time.
  - status: `in_progress`
  - current_state: Completion-time validation now supports keyword-bound verification checks and explicit `verify:` commands executed in the workspace.
  - missing: No domain-specific validators or semantic acceptance engine yet.
- T3-E3-S4: Produce acceptance reports for delivery.
  - status: `in_progress`
  - current_state: Acceptance reports are emitted as typed build artifacts.
  - missing: Delivery packaging is still basic.

## Theme T4: Operator Product

- status: `in_progress`
- approx_progress: 42%

Goal: coordinate work end-to-end and support repeatable client delivery.

### Epic T4-E1: Intake And Qualification

- status: `in_progress`
- approx_progress: 40%

Stories:
- T4-E1-S1: Create intake model for repo paths, git URLs, diff ranges, and work
  type.
  - status: `complete_for_phase`
  - current_state: `agent/control/intake.py` now defines a unified operator intake envelope for repo paths, git URLs, diff ranges, work type, build type, acceptance criteria, and routing metadata.
  - missing: Git URL execution still requires clone support before it becomes runnable.
- T4-E1-S2: Add qualification logic for scope, risk, and budget.
  - status: `in_progress`
  - current_state: `OperatorIntakeService.qualify()` plus `JobPlan` creation now resolve route, source kind, risk level, blockers, warnings, scope summary, and a heuristic budget envelope for review/build requests.
  - missing: No policy-backed budget model or real cost envelope yet.
- T4-E1-S3: Add operator-facing intake summary and recommended plan.
  - status: `mostly_complete`
  - current_state: `python -m agent --intake-* --intake-preview` now returns qualification plus a structured `JobPlan` with steps, planned artifacts, budget envelope, and recommended next action. Submission echoes the same plan shape.
  - missing: No operator UI or durable handoff state for reviewed plans yet.
- T4-E1-S4: Reject unsupported work cleanly and honestly.
  - status: `in_progress`
  - current_state: Unsupported git-only intake is now modeled and explicitly rejected with blockers rather than being silently routed.
  - missing: No alternative acquisition path (clone/import) yet.

### Epic T4-E2: Job Planning And Routing

- status: `in_progress`
- approx_progress: 35%

Stories:
- T4-E2-S1: Create JobPlan model and planner outputs.
  - status: `mostly_complete`
  - current_state: `JobPlan` and `JobPlanStep` now exist in `agent/control/intake.py`, and planner output is surfaced through `OperatorIntakeService.preview()`, `AgentOrchestrator.preview_operator_intake()`, and CLI preview/submit flows.
  - missing: Plan phases, capability assignment, and durable planner state are still partial.
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
  - status: `complete_for_phase`
  - current_state: `ApprovalStorage` persists approval lifecycle state, `ApprovalQueue` recovers it, and approvals can now be queried by status/category/job/artifact linkage.
  - missing: Shared delivery/workspace policy queries remain separate scope.
- T5-E2-S2: Support approvals for risky execution and external delivery.
- T5-E2-S3: Support multi-step approvals where needed.
- T5-E2-S4: Link approvals to jobs, artifacts, and delivery bundles.
  - status: `started`
  - current_state: Review delivery approvals now carry `job_id`, `job_kind`, and `artifact_ids`, and approval queries can filter on linked jobs/artifacts.
  - missing: Delivery bundles and workspace records are not yet first-class approval-linked objects.

### Epic T5-E3: Client-Safe And Secret-Safe Output

Stories:
- T5-E3-S1: Enforce redaction on reports and logs.
- T5-E3-S2: Prevent sensitive internal data in client output.
- T5-E3-S3: Add client-safe review mode.
- T5-E3-S4: Add security regression tests around redaction and delivery.

## Theme T6: Cost, Usage, And Observability

- status: `in_progress`
- approx_progress: 50%

Goal: make the system operable, measurable, and economically sane.

### Epic T6-E1: Cost Ledger

Stories:
- T6-E1-S1: Record per-job model usage and token cost.
- T6-E1-S2: Add hard budget, soft budget, and stop-loss behavior.
- T6-E1-S3: Make escalation budget-aware.
- T6-E1-S4: Surface cost and margin hints to the operator.

### Epic T6-E2: Runtime Observability

- status: `in_progress`
- approx_progress: 60%

Stories:
- T6-E2-S1: Track job status, failures, retries, and durations.
- T6-E2-S2: Track worker execution and workspace health.
- T6-E2-S3: Track approval backlog and blocked reasons.
- T6-E2-S4: Add operator-facing reporting surface or inbox.
  - status: `complete_for_phase`
  - current_state: `OperatorReportService`, `AgentOrchestrator.get_operator_report()`, and `python -m agent --report` now expose an operator-facing inbox/report over shared jobs, approvals, and recent artifacts. The TS operator skeleton now mirrors this with a mock reporting view.
  - missing: No live web UI or push-based updates yet.

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

- approx_progress: 76%
- remaining_gap: Shared control-plane primitives, intake/planning, queries, and reporting now span build/review/operate/artifact surfaces, but extraction-grade invariants are still not enforced strongly enough.

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

Builder and reviewer now both sit on shared control-plane primitives, and the
runtime has explicit coexistence rules for product jobs, planning tasks,
infrastructure jobs, and conversational loop items. Shared artifact query and
recovery now span build and review, while operator intake preview/submit flows
surface a real `JobPlan` instead of only a route decision. The build step
itself still remains placeholder-grade (no real code generation), but the
surrounding structure is now materially stronger than the earlier
foundation-only snapshot.
