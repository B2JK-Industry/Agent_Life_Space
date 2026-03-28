# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 2 Builder Engine v2 and Provider Receipt slice

## Ready Now

### P0

1. `T3-E1-S5` Deepen the bounded local implementation engine.
   Why now: richer deterministic mutations and guardrails now exist, so the
   remaining honest Phase 2 gap is pushing the builder from explicit structured
   plans toward a production-ready execution envelope.

2. `T3-E3-S2` Deepen structured acceptance through execution and delivery.
   Why now: builder execution and delivery are now stronger, so the next
   product move is making acceptance semantics more meaningful than a
   deterministic checklist alone.

### P1

3. `T5-E1-S4` Ensure policy is deterministic and separately testable.
   Why now: builder guardrails and provider receipts now exist, so the next
   architectural risk is drift between build, review, and gateway enforcement
   rather than missing policy primitives.

4. `T6-E3-S4` Promote quality trends into stronger release gating.
   Why now: release labels, latency, regression deltas, and provider receipts
   now exist, but they still need to drive more of the release and operator
   loop.

5. `T4-E3-S4` Promote provider-specific delivery outcomes into operator workflow.
   Why now: gateway sends now return provider receipts, so operator handoff
   should understand provider outcomes instead of stopping at generic delivery
   state.

### P2

6. `T8-E2-S4` Add deployment documentation for controlled environments.
   Why now: provider routing, vault-backed auth, project-root discipline, and
   richer builder execution are now real runtime concerns, so
   production-oriented setup docs need to catch up before broader use.

## What Closed In This Cycle

- `T3-E1-S5` The bounded local builder engine now supports richer
  insert/delete-safe mutation types plus capability-scoped operation and
  target-file guardrails.
- `T7-E2-S2` Provider routing now carries provider-specific request and
  receipt modes instead of stopping at generic webhook-shaped handoff.
- `T7-E2-S3` Provider-backed sends now fall back not only on unavailable or
  retryable routes, but also on incomplete provider receipts.
- `T5-E1-S4` Deterministic policy coverage now includes builder guardrails and
  provider receipt validation with targeted tests.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- builder execution and acceptance move closer to a production-ready Phase 2
  closure instead of staying at deterministic plan mechanics only
- policy behavior across build/review/provider gateway becomes easier to test
  and reason about as one deterministic story
- provider receipts flow into operator-facing delivery understanding instead of
  stopping at low-level gateway traces
- quality trend telemetry starts shaping release/operator decisions instead of
  staying a passive metric
- controlled-environment deployment docs catch up to the now-real gateway,
  vault, repo-root, and richer builder runtime posture
