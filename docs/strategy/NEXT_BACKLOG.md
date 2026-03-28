# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 1 Closure Hardening slice

## Ready Now

### P0

1. `T4-E3-S2` Assemble artifacts, reports, and acceptance results into delivery bundles.
   Why now: builder delivery and evidence export are stronger, but review
   delivery still has not migrated onto the shared lifecycle/bundle path.

2. `T1-E2-S4` Add artifact retention and recovery rules.
   Why now: retention metadata and evidence export now exist, but pruning,
   archival, and policy-driven deletion workflows are still missing.

3. `T5-E1-S2` Keep policy deny-by-default across execution modes.
   Why now: review execution, intake gating, approvals, and evidence export now
   have clearer boundaries, but build/runtime actions are still not governed by
   one shared deny-by-default enforcement layer.

### P1

4. `T8-E3-S3` Support client-safe evidence packaging.
   Why now: evidence export now exists, so the next gap is packaging it for
   safer external/operator-facing consumption without leaking internal detail.

5. `T8-E2-S1` Define local, operator, and enterprise environment profiles.
   Why now: flow-level environment profiles now exist, so the next step is a
   higher-level deployment/operating profile matrix.

6. `T6-E2-S3` Track approval backlog and blocked reasons.
   Why now: approvals are persistent and multi-step, but approval observability
   is still thinner than the rest of the operator report.

7. `T3-E2-S4` Capture all verification artifacts and verdicts.
   Why now: builder verification is repo-aware, but delivery/evidence still
   benefits from richer first-class verification artifacts.

### P2

8. `T7-E1-S1` Define gateway contract for external capabilities.
   Why now: policy, retention, approval, environment profile, and cost
   foundations are stronger now, making gateway definition the next clean
   boundary rather than premature plumbing.

## What Closed In This Cycle

- `T8-E3-S1` Evidence export now assembles persisted jobs, artifacts, retained
  records, traces, costs, runtime model metadata, and artifact traceability
  through a dedicated CLI/runtime surface.
- `T6-E2-S1` Persisted product-job records now track duration, retry count, and
  failure count, and the operator report summarizes those signals directly.
- `T6-E1-S3` Brain-side learning overrides and post-routing quality escalation
  are now budget-aware instead of living outside runtime budget posture.
- `T4-E1-S4` Unified intake can now acquire supported git sources into a
  managed mirror before review/build routing while still rejecting unsupported
  inputs honestly.
- `T5-E2-S3` Intake and delivery approval requests can now require multi-step
  approval where deterministic thresholds demand it.
- `T1-E3-S4` Runtime model now exposes explicit environment profiles for
  review, build, acquisition/import, and export-only flows.
- `T6-E1-S4` Operator report now surfaces single-transaction approval caps and
  richer persisted product-job attention signals alongside budget posture.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- review delivery starts converging on the same shared delivery lifecycle as
  builder delivery
- retained artifacts move beyond inspectable metadata into actual pruning or
  archival workflows
- shared deny-by-default policy reaches deeper into build/runtime execution
- evidence export gains a safer client/operator-facing packaging story
