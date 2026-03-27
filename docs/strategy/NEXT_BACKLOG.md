# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-27`
- baseline: after Control-Plane Expansion Release candidate

## Ready Now

### P0

1. `T1-E1-S5` Reconcile coexistence rules between `ReviewJob`, `Task`,
   `JobRunner`, and `AgentLoop`.
   Why now: shared primitives and shared queries now span build/review/operate,
   but the long-term runtime model is still implicit.

2. `T1-E2-S5` Add a shared artifact query/recovery surface across build and
   review.
   Why now: jobs are queryable across bounded contexts, but artifacts still
   require domain-specific storage lookups and do not share one inspection API.

3. `T4-E2-S1` Create a `JobPlan` model and planner outputs.
   Why now: unified intake and qualification now exist, so the next honest step
   is planning, not another routing shortcut.

### P1

4. `T4-E1-S2` Deepen qualification with scope, risk, and budget envelopes.
   Why now: qualification currently resolves route and blockers, but not yet
   cost-aware or planner-grade tradeoffs.

5. `T4-E1-S3` Add richer operator-facing intake summary and recommended plan.
   Why now: `--intake-preview` now returns a useful qualification result, but it
   still lacks a durable plan summary for operator review.

6. `T3-E1-S3` Capture real patch-set artifacts for builder output.
   Why now: builder already persists diff/trace/verification artifacts, so the
   next artifact gap is a first-class patch export instead of placeholder-only
   mutation markers.

7. `T3-E3-S3` Add richer domain-specific acceptance evaluators.
   Why now: acceptance is now structured, resumable, and review-gated, but it
   is still intentionally rule-based and shallow.

### P2

8. `T5-E2-S4` Extend approval linkage from review delivery to build, workspace,
   and delivery bundle records.
   Why now: approval persistence and query filters exist, so the next step is
   deeper cross-domain linkage instead of more approval mechanics.

9. `T6-E2-S2` Surface workspace health and worker execution in the operator
   report.
   Why now: operator reporting now exists, but it still focuses on jobs and
   approvals rather than runtime workspace health.

## What Closed In This Cycle

- `T1-E1-S1` `ReviewJob` migrated onto shared control-plane primitives.
- `T1-E1-S4` Shared job queries now cover build, review, task, job-runner, and
  agent-loop records.
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

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- the runtime model has explicit coexistence/deprecation rules
- artifacts become queryable across bounded contexts, not just jobs
- operator intake produces a durable recommended plan, not only a route
- builder output becomes more delivery-grade through real patch artifacts and
  richer acceptance evaluators
