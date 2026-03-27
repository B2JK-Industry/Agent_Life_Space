# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-27`
- baseline: after Builder Delivery Package + Operator Health slice

## Ready Now

### P0

1. `T4-E1-S3` Persist or expose planner output for richer operator handoff.
   Why now: `JobPlan` is now detailed enough to matter operationally, but it is
   still only a transient preview/submit payload with no durable handoff state.

2. `T4-E2-S4` Record execution traces for planning decisions.
   Why now: planner decisions now encode scope, budget, capability, and deliver
   phase choices, but those decisions are not yet recoverable as first-class
   traces.

### P1

3. `T4-E3-S4` Record delivery status and handoff audit events.
   Why now: builder delivery packages and approval gates now exist, but there is
   still no durable handoff-status trail once a package is prepared or approved.

4. `T1-E3-S3` Link workspace records to jobs, artifacts, and approvals.
   Why now: builder delivery approvals now carry workspace and bundle linkage,
   but workspace records themselves are still not queryable as first-class
   control-plane joins.

5. `T3-E2-S1` Add test, lint, and type-check loop for implementation jobs.
   Why now: builder now emits delivery packages honestly, so the next builder
   gap is better verification discovery rather than more packaging.

6. `T3-E2-S2` Add review-after-build pass before completion.
   Why now: review-after-build now exists, but thresholds and blocking policy
   remain hard-coded rather than policy-driven.

### P2

7. `T1-E2-S4` Add artifact retention and recovery rules.
   Why now: patch, diff, report, and bundle-oriented artifacts are growing in
   importance, but retention and pruning policy are still undefined.

8. `T5-E1-S1` Extend policy model to job, artifact, delivery, and external
   gateway decisions.
   Why now: delivery/package linkage is deeper now, but policy still does not
   treat delivery surfaces as first-class decisions.

## What Closed In This Cycle

- `T1-E1-S1` `ReviewJob` migrated onto shared control-plane primitives.
- `T1-E1-S4` Shared job queries now cover build, review, task, job-runner, and
  agent-loop records.
- `T1-E1-S5` Runtime coexistence rules are now explicit through
  `RuntimeModelService` and `python -m agent --runtime-model`.
- `T1-E2-S5` Shared artifact query/recovery now spans build and review through
  `ArtifactQueryService`, orchestrator list/get methods, and CLI artifact
  inspection.
- `T5-E2-S1` Approval requests are now persistent and queryable with
  job/artifact filters.
- `T6-E2-S4` Operator-facing report/inbox surface exists in runtime, CLI, and
  TS contracts.
- `T3-E1-S1` Builder capability catalog exists and is surfaced in runtime
  status/query metadata.
- `T3-E1-S4` Builder execution now supports resumable checkpoints and CLI
  resume.
- `T4-E1-S1` Unified operator intake now exists for review/build routing, with
  honest rejection of unsupported git-only execution.
- `T4-E1-S2` Qualification now resolves scope signals, risk factors, and a
  policy-backed budget envelope instead of only a heuristic tier.
- `T4-E2-S1` `JobPlan` now exists and is surfaced through intake preview and
  submission outputs.
- `T4-E2-S2` `JobPlan` now models explicit qualify/review/build/verify/deliver
  phases.
- `T4-E2-S3` Planner output now assigns concrete build catalog capabilities plus
  planner profiles and structured budget metadata.
- `T3-E1-S3` Builder now captures deterministic patch + diff artifacts instead
  of relying on placeholder workspace diff metadata.
- `T3-E3-S3` Acceptance now supports richer domain-aware evaluators for
  post-build review, documentation changes, and target-file changes.
- `T6-E2-S2` Operator report now exposes workspace health and worker execution
  summaries.
- `T5-E2-S4` Approval linkage now covers workspace and delivery bundle records
  in addition to jobs and artifacts.
- `T4-E3-S1` Shared `DeliveryPackage` model now exists in the control-plane
  foundation.
- `T4-E3-S2` Builder now assembles artifacts, acceptance results, and review
  output into a build delivery package preview.
- `T4-E3-S3` Build delivery now uses the shared approval gate instead of
  reviewer-only delivery approval.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- planner output becomes durable enough to hand off or resume cleanly
- planning decisions emit explicit trace/audit records
- delivery package lifecycle gains status + audit events after approval/handoff
- workspace, delivery, and approval joins become richer and more queryable
