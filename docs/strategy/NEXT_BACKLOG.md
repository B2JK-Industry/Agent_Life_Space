# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-27`
- baseline: after Runtime Model + Artifact Planning slice

## Ready Now

### P0

1. `T4-E1-S2` Deepen qualification with scope, risk, and budget envelopes.
   Why now: `JobPlan` now exists, but its budget envelope is still heuristic and
   not yet tied to real policy or cost behavior.

2. `T4-E2-S2` Split work into review, build, verify, and deliver phases.
   Why now: planner output now exists, so the next useful step is a richer phase
   model instead of another one-off preview field.

### P1

3. `T4-E2-S3` Assign capabilities and budget envelopes.
   Why now: planning now names steps and artifacts, but it still does not bind
   concrete capability or budget choices strongly enough.

4. `T3-E1-S3` Capture real patch-set artifacts for builder output.
   Why now: build/review artifacts are now queryable across domains, so the next
   builder artifact gap is a real patch export rather than a placeholder marker.

5. `T3-E3-S3` Add richer domain-specific acceptance evaluators.
   Why now: planning and artifact recovery are stronger, but acceptance is still
   shallow and keyword-bound.

6. `T6-E2-S2` Surface workspace health and worker execution in the operator
   report.
   Why now: operator reporting now includes recent artifacts too, but it still
   under-reports workspace and worker health.

### P2

7. `T5-E2-S4` Extend approval linkage from review delivery to build, workspace,
   and delivery bundle records.
   Why now: approval persistence and query filters exist, so the next step is
   deeper cross-domain linkage instead of more approval mechanics.

8. `T4-E1-S3` Persist or expose planner output for richer operator handoff.
   Why now: intake preview now shows a real `JobPlan`, but it is still a
   transient preview rather than a durable operator handoff object.

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
- `T4-E2-S1` `JobPlan` now exists and is surfaced through intake preview and
  submission outputs.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- qualification/planning becomes budget-aware beyond heuristic envelopes
- planner outputs model richer execution phases and capability choices
- builder artifacts include a real patch export, not only diff/trace metadata
- acceptance gains richer domain-aware evaluators
- operator reporting exposes workspace and worker health alongside jobs,
  approvals, and artifacts
