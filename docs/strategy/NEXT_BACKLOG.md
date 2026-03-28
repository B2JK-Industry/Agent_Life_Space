# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 2 closure and release-readiness slice

## Ready Now

### P0

1. `T4-E2-S3` Bind review, verify, and deliver planner phases to stronger runtime capabilities.
   Why now: Phase 2 builder closure is now good enough that the next honest gap
   is operatorization, not more builder mechanics.

2. `T4-E3-S4` Finish provider-specific operator delivery workflow.
   Why now: provider receipts, provider outcomes, and release readiness now
   exist, but the operator flow still stops at report/CLI detail instead of a
   richer active workflow.

### P1

3. `T5-E1-S1` Push policy toward one broader runtime action boundary.
   Why now: builder guardrails, review execution policy, and gateway policy are
   all deterministic now, so the next architectural move is unifying them more
   deeply instead of adding another isolated policy branch.

4. `T6-E2-S1` Deepen persisted runtime telemetry beyond current product-job summaries.
   Why now: release readiness, provider outcomes, and product-job telemetry now
   exist, so operatorization needs richer ongoing runtime history.

5. `T6-E1-S1` Improve real cost estimation and operator cost feedback.
   Why now: runtime cost ledger is durable enough that the next useful step is
   improving quality of estimates, not just recording more of the same.

### P2

6. `T7-E1-S1` Expand the gateway contract beyond one-provider Phase 2 semantics.
   Why now: `obolos.tech` is now a concrete provider with route semantics and
   receipts, so Phase 3 can start generalizing the gateway boundary.

7. `T8-E1-S3` Add stronger architecture invariants for cross-domain boundaries.
   Why now: the runtime shape is now concrete enough that enforcement-level
   invariants matter more than additional descriptive docs alone.

## What Closed In This Cycle

- `T3-E1-S5` The bounded local builder engine now supports deterministic
  `copy_file` and `move_file` mutations in addition to the earlier
  insert/delete-safe workspace operations, keeping execution scoped without
  pretending to be freeform generation.
- `T3-E3-S2` Acceptance criteria now reach deeper into execution and delivery:
  implementation-backed criteria can validate changed-operation counts, changed
  paths, operation types, and required implementation mode through the same
  delivery-ready acceptance report.
- `T5-E1-S4` Deterministic policy coverage now includes separately testable
  builder guardrail evaluation, provider outcome classification, and
  release-readiness thresholds.
- `T6-E3-S4` Quality trend telemetry now drives a real release-readiness gate
  surfaced through CLI, CI, control-plane traces, and operator reporting.
- `T4-E3-S4` Delivery workflow now records provider-specific outcomes in the
  shared operator report instead of stopping at generic gateway success/fail
  events.
- `T8-E2-S4` Controlled-environment deployment documentation now exists for the
  vault, gateway, runtime profile, and release-readiness path.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- planner output stops being only informative and starts binding more of the
  operator workflow to real runtime capabilities
- provider-specific delivery outcomes become actionable operator workflow, not
  just report detail
- policy decisions across build, review, and gateway read more like one shared
  control story
- runtime telemetry and cost posture become more useful for active operator
  decisions
- Phase 3 starts with operatorization and runtime cohesion, not with another
  round of builder-core cleanup
