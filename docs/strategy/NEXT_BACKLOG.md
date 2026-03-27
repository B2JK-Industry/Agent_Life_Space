# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-27`
- baseline: after Planner Qualification + Phase Routing slice

## Ready Now

### P0

1. `T3-E1-S3` Capture real patch-set artifacts for builder output.
   Why now: the planner now models an explicit deliver phase, but builder output
   still lacks a real patch export to satisfy that handoff honestly.

2. `T3-E3-S3` Add richer domain-specific acceptance evaluators.
   Why now: planning is now phase-aware and budget-aware, but acceptance is
   still shallow and keyword-bound.

### P1

3. `T6-E2-S2` Surface workspace health and worker execution in the operator
   report.
   Why now: planner output is richer, but the operator report still
   under-reports workspace and worker execution health.

4. `T4-E1-S3` Persist or expose planner output for richer operator handoff.
   Why now: `JobPlan` now includes phase, capability, and budget decisions, but
   it is still only a transient preview/submit payload.

5. `T4-E2-S4` Record execution traces for planning decisions.
   Why now: scope/risk/budget/capability choices are now explicit, but they are
   not yet recoverable as first-class planning traces.

6. `T5-E2-S4` Extend approval linkage from review delivery to build, workspace,
   and delivery bundle records.
   Why now: budget-aware next actions now exist, but approval linkage still
   stops short of workspace and delivery bundle objects.

### P2

7. `T4-E3-S1` Define delivery package model.
   Why now: the plan now exposes an explicit deliver phase, but there is still
   no first-class delivery package object behind it.

8. `T4-E3-S2` Assemble artifacts, reports, and acceptance results into delivery
   bundles.
   Why now: build/review artifacts and planner phases are ready to converge into
   one operator handoff bundle, but no bundling flow exists yet.

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

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- builder artifacts include a real patch export, not only diff/trace metadata
- acceptance gains richer domain-aware evaluators
- operator reporting exposes workspace and worker health alongside jobs,
  approvals, and artifacts
- planner output becomes durable or traceable enough for operator handoff and
  audit
