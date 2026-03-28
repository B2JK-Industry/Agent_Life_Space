# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 2 Provider Gateway and Quality Trend slice

## Ready Now

### P0

1. `T3-E1-S5` Deepen the bounded local implementation engine.
   Why now: the gateway and delivery path are now more production-shaped, so
   the biggest remaining honest Phase 2 gap is still builder depth rather than
   more boundary scaffolding.

2. `T5-E1-S4` Ensure policy is deterministic and separately testable.
   Why now: provider routing now sits inside the gateway boundary, so the next
   architectural risk is drift between build, review, and provider-aware
   gateway policy rather than missing policy primitives.

### P1

3. `T7-E2-S2` Deepen capability catalog and routing logic.
   Why now: a concrete provider now exists, but routing still ends in a
   webhook-shaped handoff instead of richer provider-specific request/response
   semantics.

4. `T7-E2-S3` Deepen fallback and failure handling.
   Why now: fallback now exists, so the next step is making downstream failure
   interpretation and retry/fallback policy more provider-aware.

5. `T3-E3-S2` Deepen structured acceptance through execution and delivery.
   Why now: builder can now deliver through provider-backed handoff, so the
   next product move is making acceptance semantics more meaningful than a
   deterministic checklist alone.

### P2

6. `T6-E3-S4` Promote quality trends into stronger release gating.
   Why now: release labels, latency, and regression deltas now exist, but they
   still need to drive more of the release and operator loop.

7. `T8-E2-S4` Add deployment documentation for controlled environments.
   Why now: provider routing, vault-backed auth, and project-root discipline
   are now real runtime concerns, so production-oriented setup docs need to
   catch up before broader use.

## What Closed In This Cycle

- `T7-E2-S1` `obolos.tech` now exists as an explicit provider inside the
  gateway model instead of only a future-facing contract note.
- `T7-E2-S2` Gateway routing now resolves provider capability routes by job
  kind/export mode and surfaces route readiness through CLI/runtime/reporting.
- `T7-E2-S3` Provider-backed sends now support fallback between configured
  routes when one endpoint is unavailable or returns retryable failures.
- `T7-E2-S4` Targeted gateway tests now cover provider route readiness,
  provider send success, fallback, and missing-config failure behavior.
- `T6-E3-S4` Review quality telemetry now records release labels, duration,
  and trend deltas against the previous quality baseline.
- `T8-E2-S3` Project-root and gateway config posture are now more explicit via
  repo-root inference plus env/vault-backed gateway readiness reporting.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- builder execution meaningfully deepens beyond today's bounded local mutation
  set
- provider routing grows from generic webhook handoff toward richer
  provider-specific request/response semantics
- policy behavior across build/review/provider gateway becomes easier to test
  and reason about as one deterministic story
- quality trend telemetry starts shaping release/operator decisions instead of
  staying a passive metric
- controlled-environment deployment docs catch up to the now-real gateway,
  vault, and repo-root runtime posture
