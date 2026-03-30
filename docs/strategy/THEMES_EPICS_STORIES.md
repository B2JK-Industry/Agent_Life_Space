# Themes, Epics, Stories

This document is the human planning decomposition derived from
`MASTER_SOURCE_OF_TRUTH.md`.

Use it for:
- roadmap planning
- backlog creation
- Claude Code task generation
- architecture conversations

## Current Progress Snapshot

Assessment basis: after the documented buyer-side Obolos API-call slice.

Important:
- this is a strategy progress snapshot, not a merge-state indicator
- this snapshot now reflects the current state of `main` as of `2026-03-28`

### Theme Snapshot

| Theme | Status | Approx Progress | Current State | Gap |
|-------|--------|-----------------|---------------|-----|
| T1 Platform Foundation | `in_progress` | 96% | Shared control-plane primitives now back build and review directly, with explicit runtime coexistence rules plus shared job/artifact queries, persisted job/plan/trace/delivery records, retention-aware artifact records with prune flow, first-class workspace joins, and explicit environment profiles for review/build/acquisition/export flows. | No unified cross-domain action layer yet. |
| T2 Reviewer Product | `complete_for_phase` | 96% | Reviewer bounded context, verifier, strict delivery gating, full client-safe redaction, converged Telegram and structured API review entrypoints, shared delivery lifecycle state, and reusable handoff summary artifacts now flow through the shared runtime. | LLM analysis and richer external delivery automation are v2. |
| T3 Builder Product | `complete_for_phase` | 99% | Builder now has a declared capability catalog, resumable checkpoints, runtime/CLI entrypoints, workspace sync, repo-aware verification discovery across Python/Node/Make/CI signals, source-aware execution policy traces and blocking, deterministic patch/diff, richer verification/acceptance delivery evidence, a bounded local implementation engine with copy/move-aware capability guardrails, implementation-backed acceptance criteria, release-readiness gating, and an explicit gateway send path for approved build bundles. | No general code generation yet and no semantic requirement engine. |
| T4 Operator Product | `in_progress` | 98% | Unified intake routing, phase-aware `JobPlan` preview/submit output, persisted plan handoff records, planning traces, runtime budget blocking, managed repo acquisition/import, pre-execution approval gating, shared review/build delivery lifecycle state, evidence export, richer operator report/CLI surfaces, operation-count-aware builder planning, acceptance-summary-aware planning, explicit gateway handoff actions, provider-outcome-aware delivery reporting, release-readiness traces, and Telegram operator surface with /report, /intake, and /build commands now exist. | No live backend/UI and no richer active provider-specific operator workflow yet. |
| T5 Security, Governance, And Policy | `in_progress` | 99% | Tool policy deny-by-default, approval gating, redaction pipeline, persistent/queryable approvals with job/artifact/workspace/bundle linkage, deterministic review-gate/delivery/review-execution/build-execution policy profiles, capability-scoped builder guardrails, explicit gateway defaults, provider-aware gateway routing decisions, provider receipt validation, provider-outcome classification, deterministic release-readiness thresholds, and structured denial payloads now exist across build/review/tool/web/social/finance-facing blocked flows. | Build execution and broader runtime action flow still sit outside one unified policy enforcement boundary. |
| T6 Cost, Usage, And Observability | `in_progress` | 99% | UsageSummary, a durable per-job cost ledger, persisted job duration/retry/failure telemetry, runtime hard/soft/stop-loss budget posture, budget-aware escalation controls, durable plan/trace/delivery/gateway/release telemetry, shared runtime job/artifact/workspace queries, richer operator reporting, approval backlog plus retention posture summaries, review-eval smoke coverage, golden review cases, runtime quality telemetry with release labels, duration, and previous-baseline trend deltas, plus a CLI/CI release-readiness gate now exist. | No live operator UI or broader longitudinal dashboards yet. |
| T7 External Capability Gateway | `in_progress` | 95% | Runtime model and policy layer now expose explicit gateway defaults, separate handoff and API-call contracts, a concrete `obolos.tech` provider catalog, readiness-aware capability routes, env/vault-backed auth resolution, documented buyer-side marketplace catalog or wallet or slug-call access, provider-specific request payload shaping, parsed provider receipts, persisted request/response artifacts, provider-outcome classification, and fallback-capable delivery sends with gateway traces and cost entries. | Only one provider exists so far, and seller publishing, x402 payment flow, file-upload calls, and broader downstream provider workflow still remain future scope. |
| T8 Enterprise Hardening | `in_progress` | 97% | Shared control-plane layer, explicit runtime coexistence rules, direct review/build primitive reuse, persisted product-job/plan/delivery state, retention-aware artifact records with prune flow, lower-level execution environment profiles plus higher-level local/operator/enterprise operating profiles, managed acquisition, client-safe evidence export, shared job/artifact/query/reporting surfaces, deterministic review/build execution policy boundaries, explicit gateway runtime boundaries, provider configuration posture, and data-handling rules. | Runtime boundaries are clearer, but not yet enforced as extraction-grade invariants across the whole execution stack. |

## Theme T1: Platform Foundation

- approx_progress: 96%

Goal: unify the core job, state, artifact, and execution foundation so the
system can support reviewer, builder, and operator modes without fragmenting.

### Epic T1-E1: Canonical Job Model

- approx_progress: 93%
- remaining_gap: Shared primitives, shared list/get query models, and persisted product-job records now exist across build/review/operate-adjacent surfaces, but action-layer convergence is still incomplete.

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
  - status: `mostly_complete`
  - current_state: Build and review jobs now sync into shared `ProductJobRecord` persistence with status, timing, artifact refs, usage summary, and a durable per-job cost-ledger entry surfaced through the orchestrator and CLI.
  - missing: Task/job-runner/agent-loop history is still separate, and there is no unified action layer on top of the shared records.
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

- approx_progress: 95%
- remaining_gap: Shared artifact query/recovery now spans build and review, builder exports deterministic patch/diff artifacts, and retention records now exist, but there is still no pruning scheduler or deletion workflow.

Stories:
- T1-E2-S1: Define artifact types and storage layout.
- T1-E2-S2: Link artifacts to jobs and execution traces.
- T1-E2-S3: Add export support for Markdown, JSON, and delivery bundles.
- T1-E2-S4: Add artifact retention and recovery rules.
  - status: `mostly_complete`
  - current_state: Control-plane retention records now track build, review, trace, and delivery-bundle outputs with policy ids, expiry timestamps, recoverability, derived active/expired state, and an explicit prune workflow surfaced through the orchestrator and CLI.
  - missing: No automated pruning scheduler, archival workflow, or broader compaction policy yet.
- T1-E2-S5: Persist full intake, report payloads, and artifact payloads for
  recovery-safe reload.
  - status: `complete_for_phase`
  - current_state: `ArtifactQueryService` now exposes shared build/review artifact list/get recovery, and build/review storage both persist artifact `format` alongside content/content_json payloads.
  - missing: Artifact retention policy and richer builder patch export remain open.

### Epic T1-E3: Workspace And Execution Discipline

- approx_progress: 87%

Stories:
- T1-E3-S1: Ensure all mutable engineering work happens in isolated workspaces.
  - status: `in_progress`
  - current_state: Builder is workspace-first, requires WorkspaceManager, and now syncs the requested repo into the managed workspace before verification.
  - missing: Mutable execution is still builder-only; reviewer remains read-only in v1.
- T1-E3-S2: Make workspace lifecycle, audit, and recovery robust.
- T1-E3-S3: Link workspace records to jobs, artifacts, and approvals.
  - status: `complete_for_phase`
  - current_state: `WorkspaceQueryService` now exposes workspace records as shared control-plane joins over jobs, artifacts, approvals, and delivery bundles, and those records flow through the orchestrator, CLI, and operator report.
  - missing: Reviewer still runs in `READ_ONLY_HOST`, so cross-domain workspace policy is not fully unified yet.
- T1-E3-S4: Add environment profiles for safe execution modes.
  - status: `complete_for_phase`
  - current_state: Runtime now exposes explicit environment profiles for `review_host_read_only`, `build_workspace_local`, `repo_import_mirror`, and `delivery_export_only`, and planner/runtime metadata binds those profiles to qualification, execution, and export flows.
  - missing: A broader local/operator/enterprise environment matrix still belongs to later enterprise hardening.
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
  - status: `complete_for_phase`
  - current_state: Review delivery now emits dedicated operator-summary and
    copy-paste-ready PR comment artifacts, persists them as first-class review
    artifacts, and includes them in both shared delivery bundles and
    client-safe evidence export.
  - missing: Direct GitHub posting and richer external delivery automation
    remain future scope.
- T2-E4-S2: Add delivery approval before external send.
- T2-E4-S3: Add client-safe output mode with redaction.
- T2-E4-S4: Add report packaging for operator handoff.
- T2-E4-S5: Route Telegram and API review entrypoints through `ReviewService`
  instead of legacy reviewer paths.
  - status: `complete_for_phase`
  - current_state: Telegram `/review` and `POST /api/review` now converge
    through the shared review runtime, and review intake preserves channel
    source through recovery-safe persistence.
  - missing: Richer external delivery automation remains future scope.

## Theme T3: Builder Product

- status: `complete_for_phase`
- approx_progress: 99%

Goal: make implementation work first-class, controlled, and acceptance-driven.

### Epic T3-E1: Capability-Based Build Execution

- status: `in_progress`
- approx_progress: 99%
- remaining_gap: Build flow now has runtime entrypoints, declared capabilities, resumable checkpoints, a bounded local implementation engine, release-readiness gating, and delivery-package preview artifacts; general code generation and broader execution planning now sit beyond the current Phase 2 scope.

Stories:
- T3-E1-S1: Define implementation capability catalog for backend, frontend,
  integration, and devops work.
  - status: `complete_for_phase`
  - current_state: `agent/build/capabilities.py` now declares explicit implementation, integration, devops, and testing capabilities with verification defaults, target patterns, resume support, and review-after-build defaults.
  - missing: External provider routing remains future scope.
- T3-E1-S2: Expose builder through a real product entrypoint.
  - status: `mostly_complete`
  - current_state: `AgentOrchestrator.run_build_job()` now exposes builder through the shared runtime, `python -m agent --build-repo ...` provides a thin CLI adapter, and that CLI can now load a structured implementation plan from JSON for bounded local execution.
  - missing: No operator/chat/API entrypoint yet, and capability catalog remains separate scope.
- T3-E1-S3: Capture patch sets, diffs, and execution traces as artifacts.
  - status: `complete_for_phase`
  - current_state: Builder now captures deterministic PATCH + DIFF artifacts by comparing source repo and workspace, alongside verification, acceptance, review, findings, and execution trace artifacts persisted via BuildStorage.
  - missing: Patch export is honest, but audit-only builds still have no structured workspace mutations to capture.
- T3-E1-S4: Make execution resumable after interruption.
  - status: `mostly_complete`
  - current_state: BuildJob now records checkpoints, resume lineage, and can resume through `BuildService.resume_build()` and `python -m agent --build-resume ...`, reusing successful phases and rerunning failed ones.
  - missing: No planner-driven or distributed-worker resume policy yet.
- T3-E1-S5: Replace placeholder build step with a bounded local implementation engine.
  - status: `complete_for_phase`
  - current_state: BuildService now executes structured workspace mutations (`write_file`, `append_text`, `replace_text`, `insert_before_text`, `insert_after_text`, `delete_text`, `delete_file`, `copy_file`, `move_file`, `json_set`) through a bounded local engine, enforces deterministic operation-count plus source/target-scope guardrails, persists per-operation results, and exposes implementation mode plus execution summaries through job metadata, delivery bundles, CLI, and planning surfaces.
  - missing: Execution still depends on an explicit structured plan; there is no freeform or LLM-backed code generation yet.

### Epic T3-E2: Build Verification Loop

- status: `in_progress`
- approx_progress: 93%

Stories:
- T3-E2-S1: Add test, lint, and type-check loop for implementation jobs.
  - status: `mostly_complete`
  - current_state: Builder now performs repo-aware verification discovery for test, lint, and typecheck surfaces across Python config, package scripts, Makefile targets, CI workflow hints, and repo-local toolchains before running the discovered suite in the workspace. Custom commands still remain available underneath the verification layer.
  - missing: Discovery is deeper and more honest, but still deterministic rather than a full language-specific execution planner.
- T3-E2-S2: Add review-after-build pass before completion.
  - status: `mostly_complete`
  - current_state: Successful build jobs can now request a deterministic post-build reviewer pass through `ReviewService`, and completion is now governed by explicit deterministic review-gate policies (`critical_findings`, `high_or_critical`, `advisory`).
  - missing: Reviewer still runs in `READ_ONLY_HOST` mode over the built workspace path, and policy is not yet unified with the wider execution boundary.
- T3-E2-S3: Fail jobs clearly when acceptance criteria are not met.
  - status: `complete_for_phase`
  - current_state: BuildService now fails builds with structured denial payloads and detailed unmet-required-criterion summaries instead of generic count-only rejection strings, while delivery summaries expose the same blocking-vs-optional acceptance state.
  - missing: The acceptance engine is still deterministic and rule-based; there is no semantic requirement model yet.
- T3-E2-S4: Capture all verification artifacts and verdicts.
  - status: `complete_for_phase`
  - current_state: Verification now persists one suite-level report plus per-step verification artifacts, and build delivery bundles expose those artifact ids and summaries for operator handoff.
  - missing: Discovery remains heuristic and evidence export does not yet add richer language-specific interpretation.

### Epic T3-E3: Acceptance Criteria Engine

- status: `complete_for_phase`
- approx_progress: 96%

Stories:
- T3-E3-S1: Define acceptance criteria object model.
  - status: `complete_for_phase`
  - current_state: AcceptanceCriterion now has explicit kind, required-vs-optional semantics, evaluator hints, typed status transitions, and lightweight parsing from operator/CLI strings into a more structured builder-facing object model.
  - missing: The object model is stronger, but semantic requirement matching is still outside the current deterministic evaluator set.
- T3-E3-S2: Attach acceptance criteria to jobs.
  - status: `complete_for_phase`
  - current_state: BuildIntake carries structured `AcceptanceCriterion` objects, BuildJob inherits them, CLI can now load acceptance criteria from JSON, unified operator intake accepts both lightweight strings and richer criterion objects, `JobPlan` now exposes acceptance summaries with required/optional/structured/evaluator breakdown before execution, and delivery-ready reports now include implementation-backed acceptance summaries over changed operations, changed paths, operation types, and implementation mode.
  - missing: Acceptance criteria are now first-class in intake/planning, but semantic requirement matching is still missing.
- T3-E3-S3: Validate criteria at completion time.
  - status: `complete_for_phase`
  - current_state: Completion-time validation now supports keyword-bound verification checks, explicit `verify:` commands executed in the workspace, metadata-driven verification kind targeting, structured workspace file/text/JSON checks, structured change-set path/count/docs checks, and structured review-threshold checks.
  - missing: No semantic acceptance engine beyond deterministic rule-based evaluators yet.
- T3-E3-S4: Produce acceptance reports for delivery.
  - status: `complete_for_phase`
  - current_state: Acceptance reports are emitted as typed build artifacts and now include delivery-usable summaries, criteria grouped by status, verification outcome, and review verdict metadata that flow into build delivery bundles.
  - missing: Acceptance semantics are still deterministic and rule-based; there is no richer semantic requirement model yet.

## Theme T4: Operator Product

- status: `in_progress`
- approx_progress: 98%

Goal: coordinate work end-to-end and support repeatable client delivery.

### Epic T4-E1: Intake And Qualification

- status: `in_progress`
- approx_progress: 95%

Stories:
- T4-E1-S1: Create intake model for repo paths, git URLs, diff ranges, and work
  type.
  - status: `complete_for_phase`
  - current_state: `agent/control/intake.py` now defines a unified operator intake envelope for repo paths, git URLs, diff ranges, work type, build type, structured acceptance criteria, structured builder implementation plans, routing metadata, and managed acquisition hints for supported git sources.
  - missing: Live operator UI and broader remote-provider workflows remain outside the current phase.
- T4-E1-S2: Add qualification logic for scope, risk, and budget.
  - status: `mostly_complete`
  - current_state: `OperatorIntakeService.qualify()` plus `JobPlan` creation now resolve scope size, scope signals, risk factors, and a policy-backed budget envelope using `BudgetPolicy` plus live finance budget state when available.
  - missing: Cost estimates are still deterministic heuristics, and there is no live operator UI.
- T4-E1-S3: Add operator-facing intake summary and recommended plan.
  - status: `complete_for_phase`
  - current_state: `python -m agent --intake-* --intake-preview` now returns qualification plus a structured `JobPlan`, including acceptance-summary metadata, and preview/submit both persist a first-class plan record for later operator handoff through orchestrator and CLI list/get surfaces.
  - missing: No live operator UI yet.
- T4-E1-S4: Reject unsupported work cleanly and honestly.
  - status: `complete_for_phase`
  - current_state: Unified intake now rejects unsupported git inputs honestly, returns explicit `blocked` or `awaiting_approval` runtime states, and can acquire supported sources into a managed local mirror before routing runtime work.
  - missing: Broader remote acquisition behavior still depends on host/network policy and there is no live operator UI.

### Epic T4-E2: Job Planning And Routing

- status: `in_progress`
- approx_progress: 87%

Stories:
- T4-E2-S1: Create JobPlan model and planner outputs.
  - status: `complete_for_phase`
  - current_state: `JobPlan`, `JobPlanStep`, `JobPlanPhase`, `JobPlanCapability`, and `JobPlanBudgetEnvelope` now exist in `agent/control/intake.py`, planner output is surfaced through preview/submit flows plus persisted `JobPlanRecord` handoff state, and builder planning now carries operation-count-aware execution metadata plus acceptance-summary detail.
  - missing: Planner output is durable, but not yet a distributed execution history.
- T4-E2-S2: Split work into review, build, verify, deliver phases.
  - status: `complete_for_phase`
  - current_state: Planner output now exposes explicit qualify, review, build, verify, and deliver phases, with phase-aware steps for both review and build routes, including verification/acceptance detail earlier in the build plan.
  - missing: Planner phases are still preview/submit constructs rather than persisted execution history.
- T4-E2-S3: Assign capabilities and budget envelopes.
  - status: `complete_for_phase`
  - current_state: Planner output now assigns concrete build catalog capabilities plus execution policies and delivery policies for review, verify, and deliver phases, alongside structured budget envelope metadata. Review/verify/deliver phases now bind to execution policies and delivery policies, not just planner profiles.
  - missing: Only the build phase currently binds to a full runtime capability catalog; the remaining phase bindings are policy-level rather than capability-catalog-level.
- T4-E2-S4: Record execution traces for planning decisions.
  - status: `complete_for_phase`
  - current_state: Qualification, budget, capability, and delivery decisions now emit durable `ExecutionTraceRecord` entries that can be listed and filtered through the shared control-plane surface.
  - missing: Trace coverage is still strongest for planning/build delivery, not yet the whole runtime.

### Epic T4-E3: Delivery Workflow

- status: `in_progress`
- approx_progress: 95%
- remaining_gap: Shared delivery lifecycle now covers both build and review and includes a real gateway send path, but there is still no live operator UI or provider-specific delivery automation.

Stories:
- T4-E3-S1: Define delivery package model.
  - status: `complete_for_phase`
  - current_state: Shared `DeliveryPackage` model now exists in `agent/control/models.py` and is used by both builder and reviewer delivery preview flows.
  - missing: Provider-specific delivery targets and richer operator workflow remain later scope.
- T4-E3-S2: Assemble artifacts, reports, and acceptance results into delivery
  bundles.
  - status: `complete_for_phase`
  - current_state: Builder assembles verification, acceptance, patch, diff, review, findings, and workspace metadata into delivery package previews, reviewer assembles report, findings, trace, approval linkage, and bundle metadata into the same shared lifecycle envelope, and both bundles can now flow through the explicit gateway boundary after approval.
  - missing: There is still no live operator workflow or provider-specific send routing behind the bundle.
- T4-E3-S3: Gate delivery through approvals.
  - status: `complete_for_phase`
  - current_state: Review and build delivery both request approval with explicit job, artifact, workspace, and bundle linkage while refreshing lifecycle status after approval changes.
  - missing: Richer approval chains and real external send remain future scope.
- T4-E3-S4: Record delivery status and handoff audit events.
  - status: `complete_for_phase`
  - current_state: Build and review delivery now persist lifecycle state (`prepared`, `awaiting_approval`, `approved`, `rejected`, `handed_off`) plus explicit audit events for gateway request, success, and failure, and provider-specific outcomes now flow into delivery summaries, operator report summaries, inbox attention, and recent provider-delivery views.
  - missing: Broader active operator workflow and live UI remain future scope.

### Epic T4-E4: Operator Telegram Surface

- status: `complete_for_phase`
- approx_progress: 100%
- remaining_gap: Telegram surface delegates to existing operator services; richer interactive flows and live UI remain future scope.

Stories:
- T4-E4-S1: /report Telegram command
  - status: `complete_for_phase`
  - current_state: Operator report accessible from Telegram with overview/inbox/budget views.
  - missing: Live interactive drill-down and richer formatting remain future scope.
- T4-E4-S2: /intake Telegram command
  - status: `complete_for_phase`
  - current_state: Unified intake qualify→plan→execute flow accessible from Telegram.
  - missing: Richer conversational intake and multi-step Telegram approval remain future scope.
- T4-E4-S3: /build Telegram shortcut
  - status: `complete_for_phase`
  - current_state: Build intake shortcut delegates to unified /intake.
  - missing: Direct build-specific Telegram options remain future scope.
- T4-E4-S4: /jobs Telegram command
  - status: `complete_for_phase`
  - current_state: Product job listing and detail accessible from Telegram.
  - missing: Richer filtering and interactive job drill-down remain future scope.
- T4-E4-S5: /deliver Telegram command
  - status: `complete_for_phase`
  - current_state: Delivery listing, detail, and gateway send accessible from Telegram.
  - missing: Richer delivery approval flows and interactive send confirmation remain future scope.

## Theme T5: Security, Governance, And Policy

- status: `in_progress`
- approx_progress: 99%

Goal: ensure all valuable workflows remain controlled and auditable.

### Epic T5-E1: Policy Control Plane

Stories:
- T5-E1-S1: Extend policy model to job, artifact, delivery, and external gateway
  decisions.
  - status: `mostly_complete`
  - current_state: Shared policy now covers deterministic job-persistence, artifact-retention, delivery, review-gate, and external-gateway defaults, and those policies surface through control-plane persistence, artifact queries, reporting, and delivery metadata.
  - missing: Repository/diff execution and broader runtime actions are still not enforced through one shared policy engine.
- T5-E1-S2: Keep policy deny-by-default across execution modes.
  - status: `mostly_complete`
  - current_state: Deny-by-default is now explicit across review execution,
    tool execution, operator intake blockers, evidence export, build/review
    delivery approval plus handoff flows, and source-aware build execution
    policies, all with stable operator-visible denial payloads.
  - missing: Build implementation execution and some broader runtime actions
    still do not run under one shared enforcement boundary.
- T5-E1-S3: Add structured denial reasons everywhere.
  - status: `complete_for_phase`
  - current_state: Structured denial payloads now cover tool policy, operator
    intake, build/review validation and delivery blockers, evidence export,
    operator reporting attention detail, finance budget blockers, web access
    failures, tool-execution failures, and social/API request errors.
  - missing: One unified runtime enforcement engine is still separate scope.
- T5-E1-S4: Ensure policy is deterministic and separately testable.
  - status: `complete_for_phase`
  - current_state: Gateway provider catalogs, route selection, config
    readiness, provider receipt validation, provider-outcome classification,
    builder capability guardrails, release-readiness thresholds, and fallback
    behavior now sit on deterministic policy + catalog surfaces with targeted
    tests instead of living only in ad-hoc runtime branches.
  - missing: Build/review/tool/runtime action flow is still not governed by
    one shared enforcement engine.
- T5-E1-S5: Bring repository and diff analysis under the shared execution and
  policy boundary.
  - status: `complete_for_phase`
  - current_state: Review-side repository and diff access now uses explicit
    deterministic review execution policies, and those decisions are recorded as
    durable control-plane review-policy traces and product-job metadata.
  - missing: Builder execution is still not governed by the same policy engine.

### Epic T5-E2: Approval Model

- status: `in_progress`
- approx_progress: 86%

Stories:
- T5-E2-S1: Make approval requests persistent and queryable.
  - status: `complete_for_phase`
  - current_state: `ApprovalStorage` persists approval lifecycle state, `ApprovalQueue` recovers it, and approvals can now be queried by status/category/job/artifact/workspace/bundle linkage.
  - missing: Shared delivery/workspace policy queries remain separate scope.
- T5-E2-S2: Support approvals for risky execution and external delivery.
  - status: `mostly_complete`
  - current_state: Review/build delivery remained approval-gated, and unified
    intake can now request finance approval for budget-sensitive work plus tool
    approval for high-risk execution before a job starts.
  - missing: Multi-step approval workflows and broader policy/action unification
    remain open.
- T5-E2-S3: Support multi-step approvals where needed.
  - status: `mostly_complete`
  - current_state: Unified intake and build/review delivery approvals can now require more than one approval deterministically based on budget posture, risk level, build type, review severity, and delivery scope.
  - missing: Richer approval chains, delegated approver roles, and broader policy/action unification remain future scope.
- T5-E2-S4: Link approvals to jobs, artifacts, and delivery bundles.
  - status: `mostly_complete`
  - current_state: Review delivery approvals already carried `job_id`, `job_kind`, and `artifact_ids`; builder delivery approvals now add `workspace_id` and `bundle_id`, and approval queries can filter on all of those linked records.
  - missing: Delivery status and broader policy/action unification remain future scope.

### Epic T5-E3: Client-Safe And Secret-Safe Output

Stories:
- T5-E3-S1: Enforce redaction on reports and logs.
- T5-E3-S2: Prevent sensitive internal data in client output.
- T5-E3-S3: Add client-safe review mode.
- T5-E3-S4: Add security regression tests around redaction and delivery.

## Theme T6: Cost, Usage, And Observability

- status: `in_progress`
- approx_progress: 91%

Goal: make the system operable, measurable, and economically sane.

### Epic T6-E1: Cost Ledger

Stories:
- T6-E1-S1: Record per-job model usage and token cost.
  - status: `mostly_complete`
  - current_state: Build and review jobs now persist per-job usage, token, and cost data into the shared control-plane ledger, and the orchestrator/CLI/operator report expose recent ledger entries and total recorded cost.
  - missing: Budgets, escalation controls, and operator margin hints still sit outside the ledger.
- T6-E1-S2: Add hard budget, soft budget, and stop-loss behavior.
  - status: `complete_for_phase`
  - current_state: `BudgetPolicy` now drives hard-cap, soft-cap, stop-loss, and
    approval-cap decisions; unified intake enforces those decisions at runtime
    and records budget-block traces.
  - missing: Budget-aware escalation remains separate scope.
- T6-E1-S3: Make escalation budget-aware.
  - status: `mostly_complete`
  - current_state: Brain-side learning overrides and post-routing quality escalation now check runtime budget posture before escalating models, and blocked escalation decisions stay inside the deterministic finance policy envelope.
  - missing: Escalation still uses simple fixed-cost assumptions and has no live operator override UI.
- T6-E1-S4: Surface cost and margin hints to the operator.
  - status: `complete_for_phase`
  - current_state: Finance budget state now exposes warnings, hard/soft/stop-loss posture, single-transaction approval cap, and persisted product-job attention signals through the operator report and CLI.
  - missing: There is still no live operator UI.

### Epic T6-E2: Runtime Observability

- status: `in_progress`
- approx_progress: 89%

Stories:
- T6-E2-S1: Track job status, failures, retries, and durations.
  - status: `mostly_complete`
  - current_state: Persisted `ProductJobRecord` entries now track duration, retry count, and failure count, and the operator report summarizes failed jobs, retried jobs, and max observed duration across persisted product jobs.
  - missing: Telemetry is strongest for persisted build/review jobs; deeper operate-runtime history remains separate.
- T6-E2-S2: Track worker execution and workspace health.
  - status: `mostly_complete`
  - current_state: `AgentOrchestrator.get_status()` now exposes workspace stats plus worker-execution snapshots, and `OperatorReportService` surfaces both in the operator report/inbox summary.
  - missing: No live UI, push updates, or deeper per-worker telemetry yet.
- T6-E2-S3: Track approval backlog and blocked reasons.
  - status: `complete_for_phase`
  - current_state: `OperatorReportService` now exposes approval backlog counts by status/category plus blocked approval reasons, and partial approvals surface as actionable inbox detail instead of disappearing into storage.
  - missing: No live UI or push-driven approval inbox yet.
- T6-E2-S4: Add operator-facing reporting surface or inbox.
  - status: `complete_for_phase`
  - current_state: `OperatorReportService`, `AgentOrchestrator.get_operator_report()`, and `python -m agent --report` now expose an operator-facing inbox/report over shared jobs, approvals, and recent artifacts. The TS operator skeleton now mirrors this with a mock reporting view.
  - missing: No live web UI or push-based updates yet.

### Epic T6-E3: Quality Evals

- approx_progress: 96%

Stories:
- T6-E3-S1: Build golden review cases.
  - status: `mostly_complete`
  - current_state: Dedicated `tests/test_review_eval_golden.py` now pins clean,
    secret, and unsafe-pattern repo verdicts, CI runs that suite explicitly
    next to the earlier smoke coverage, and the runtime now reuses the same
    shared golden fixtures for quality evaluation.
  - missing: Cross-version trend telemetry remains future scope.
- T6-E3-S2: Measure finding precision and false positives.
  - status: `mostly_complete`
  - current_state: `ReviewQualityService` now runs the deterministic golden
    cases at runtime, records verdict/count/title precision plus
    false-positive and false-negative counts as control-plane quality traces,
    and the operator report exposes the latest review-quality snapshot.
  - missing: Quality precision telemetry now exists, but broader release
    gating and non-review trend surfaces remain future scope.
- T6-E3-S3: Add review eval smoke checks to CI or local gating.
  - status: `complete_for_phase`
  - current_state: Dedicated `tests/test_review_eval_smoke.py` coverage now
    validates reviewer handoff artifacts and client-safe redaction, and CI
    runs that suite explicitly.
  - missing: No version-over-version quality or latency telemetry yet.
- T6-E3-S4: Track latency and quality regression across versions.
  - status: `complete_for_phase`
  - current_state: Review quality evaluation now records release labels,
    runtime duration, quality deltas versus the previous baseline, and
    regression posture into the control plane and operator report, and the same
    telemetry now drives a deterministic CLI/CI release-readiness gate.
  - missing: Trend telemetry exists, but it is not yet promoted into broader
    longitudinal dashboards.

## Theme T7: External Capability Gateway

Goal: integrate external capability providers cleanly and safely.

### Epic T7-E1: Gateway Foundation

- approx_progress: 95%

Stories:
- T7-E1-S1: Define gateway contract for external capabilities.
  - status: `mostly_complete`
  - current_state: Runtime model and policy now expose both
    `external_capability_gateway_v1` for handoff-style delivery and
    `external_api_call_v1` for documented provider-backed API invocation, with
    explicit request/response, denial, approval, and cost-record expectations.
  - missing: The contracts are stronger, but they are not yet multi-provider
    or tied to richer operator workflow.
- T7-E1-S2: Add auth, timeout, retry, and rate-limit policy.
  - status: `complete_for_phase`
  - current_state: External gateway policy now enforces auth, timeout, retry,
    rate-limit, target-kind, and URL-scheme checks at runtime instead of
    leaving them only in planning metadata.
  - missing: Provider-specific routing and fallback policy remain future scope.
- T7-E1-S3: Add audit and cost tracking for external calls.
  - status: `complete_for_phase`
  - current_state: Gateway sends now record durable gateway traces, delivery
    lifecycle events, distinct cost-ledger entries for each gateway run, and
    provider/route metadata for provider-backed sends.
  - missing: Broader provider-specific usage accounting still remains future
    scope.
- T7-E1-S4: Add policy gating for when external capability use is allowed.
  - status: `mostly_complete`
  - current_state: Gateway sends now flow through one explicit policy decision
    path checking approval state, auth presence, target kind, URL scheme, and
    rate limits before any outbound request is attempted.
  - missing: Gating is now provider-aware, but it still does not represent one
    unified runtime enforcement engine.

### Epic T7-E2: obolos.tech Integration

Stories:
- T7-E2-S1: Represent obolos.tech capabilities through the gateway model.
  - status: `complete_for_phase`
  - current_state: `obolos.tech` now exists as an explicit provider inside the
    gateway model with both the older delivery-handoff compatibility routes and
    the first documented buyer-side marketplace capabilities.
  - missing: Only one provider is modeled so far, and seller-side publishing
    remains future scope.
- T7-E2-S2: Add capability catalog and routing logic.
  - status: `complete_for_phase`
  - current_state: The gateway now exposes a provider/capability/route
    catalog, surfaces route readiness through runtime/report/CLI, and resolves
    ordered candidate routes by provider, capability, job kind, and export
    mode, now carrying provider-specific request and receipt modes for both
    delivery handoff and buyer-side API-call routes.
  - missing: Additional providers and broader operator workflow remain future
    scope.
- T7-E2-S3: Add fallback and failure handling.
  - status: `mostly_complete`
  - current_state: Provider-backed sends now fall back across configured
    routes when one route is unavailable, returns retryable downstream
    failures, or returns an incomplete provider receipt, and those attempts
    stay traceable through provider-aware gateway metadata. Buyer-side API
    calls now also return structured denials for HTTP 402 payment-required
    responses instead of surfacing only raw HTTP failures.
  - missing: Additional providers, seller-side workflow, file uploads, and
    richer downstream workflow still remain future scope.
- T7-E2-S4: Add tests for gateway policy and error modes.
  - status: `complete_for_phase`
  - current_state: Targeted gateway tests now cover provider route readiness,
    direct provider sends, fallback to backup routes, documented Obolos catalog
    and wallet-backed API calls, and structured payment-required failures
    instead of stopping at the earlier generic boundary tests.
  - missing: Additional providers and richer downstream error semantics remain
    future scope.

## Theme T8: Enterprise Hardening

Goal: prepare the system to evolve into enterprise-grade architecture without
premature fragmentation.

### Epic T8-E1: Contract-First Boundaries

- approx_progress: 79%
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

- approx_progress: 90%

Stories:
- T8-E2-S1: Define local, operator, and enterprise environment profiles.
  - status: `complete_for_phase`
  - current_state: Runtime model now exposes local-owner, operator-controlled, and enterprise-hardened operating profiles with default execution, delivery, and gateway posture layered on top of the lower-level execution environment profiles.
  - missing: The profile matrix exists, but it is not yet enforced as a deployment-grade contract across the whole execution stack.
- T8-E2-S2: Make environment-sensitive behavior explicit and testable.
  - status: `mostly_complete`
  - current_state: Runtime model and planner metadata now expose explicit environment profiles for review, build, acquisition/import, and export-only flows, making execution boundaries visible and testable in the shared control plane.
  - missing: The higher-level profile matrix now exists, but broader deployment enforcement is still not formalized.
- T8-E2-S3: Add configuration discipline for project roots, secrets, and storage.
  - status: `mostly_complete`
  - current_state: Project-root inference now prefers the checked-out repo,
    gateway route readiness reports env/vault-backed configuration posture, and
    provider sends can resolve auth from either environment variables or the
    encrypted local vault.
  - missing: Broader deployment/storage documentation and stricter runtime
    enforcement still remain open.
- T8-E2-S4: Add deployment documentation for controlled environments.
  - status: `complete_for_phase`
  - current_state: Deployment documentation now covers controlled local-owner,
    operator-controlled, and enterprise-hardened postures, release-readiness
    checks, gateway env/vault configuration, and runtime execution boundaries
    for practical Phase 2 deployment and handoff.
  - missing: The docs are explicit, but deployment enforcement is still not an
    extraction-grade runtime contract across the whole stack.

### Epic T8-E3: Compliance-Friendly Foundations

- approx_progress: 90%

Stories:
- T8-E3-S1: Improve audit export and artifact traceability.
  - status: `mostly_complete`
  - current_state: `EvidenceExportService` plus `python -m agent --export-evidence-job ...` now assemble persisted jobs, retained artifacts, traces, approvals, workspaces, cost entries, runtime model metadata, and artifact traceability into one compliance-friendly package.
  - missing: Client-safe evidence packaging and stronger enterprise data-handling rules still remain open.
- T8-E3-S2: Improve redaction and retention strategy.
  - status: `mostly_complete`
  - current_state: Shared artifact-retention policies now define expiry, recoverability, retention metadata, and explicit prune outcomes for build, review, trace, and delivery outputs.
  - missing: No automated pruning scheduler, archival workflow, or enterprise retention policy matrix yet.
- T8-E3-S3: Support client-safe evidence packaging.
  - status: `complete_for_phase`
  - current_state: `EvidenceExportService` plus `python -m agent --export-evidence-job ... --export-evidence-mode client_safe` now package review evidence through the review redaction pipeline while preserving safe approval and delivery summaries.
  - missing: Non-review client-safe evidence packaging and deeper enterprise data-handling rules remain future scope.
- T8-E3-S4: Prepare data-handling rules for future enterprise requirements.
  - status: `mostly_complete`
  - current_state: Runtime model now carries explicit rules for internal
    operator evidence, client-safe review handoff, and retained operational
    traces, including export mode, redaction expectation, handoff targets, and
    retention policy linkage.
  - missing: The rules are explicit, but broader non-review client-safe
    packaging and stronger enforcement remain future scope.

---

## Bottom Line

Builder and reviewer now both sit on shared control-plane primitives, and the
runtime has explicit coexistence rules for product jobs, planning tasks,
infrastructure jobs, and conversational loop items. Shared artifact query and
recovery now span build and review, operator intake can acquire supported git
sources into a managed mirror before runtime routing, and preview/submit flows
surface a phase-aware `JobPlan` with scope, risk, budget, and capability
decisions instead of only a route decision. Persisted product-job records,
retention-aware artifact state, environment profiles, evidence export, and
per-job cost ledger entries now make the shared control plane materially more
auditable than the earlier foundation-only snapshot, and builder execution now
has a bounded local mutation engine, even though there is still no general
code-generation path.
