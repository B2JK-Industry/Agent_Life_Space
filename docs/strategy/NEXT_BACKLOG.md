# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-04-02`
- baseline: self-host onboarding closure release `v1.34.0` on `main`

For the archival narrative of how `main` reached this baseline, see
`AS_IS_TO_BE_2026_04_01.md`.

## Ready Now

Next release candidate: `v1.35.0` — Selective Extraction Readiness.

## Why This Slice

- `main` now includes the full Phase 4 closure, the `v1.32.0` and `v1.33.0`
  builder arc, and the `v1.34.0` self-host onboarding closure slice.
- The next honest gap is no longer first-run onboarding, but clearer service
  seams and public boundaries for the architecture arc after Phase 4.
- The highest leverage now sits in selective extraction readiness, hidden
  coupling removal, and stronger explicit contracts between runtime surfaces.

## Proposed Scope

- `P0` `T8-E1-S4`: Make future service extraction obvious from module boundaries
- `P0` `T8-E1-S2`: Remove hidden coupling and implicit shared state
- `P1` `T8-E3-S4`: Prepare data-handling rules for future enterprise requirements
- `P1` `T5-E1-S1`: Extend broader runtime action policy toward a stronger cross-domain contract

## Exit Criteria For The Next Release

The next slice should be considered successful when:
- service and runtime seams are easier to see from public APIs and module boundaries
- hidden coupling and post-init mutation are reduced further across the runtime
- data-handling expectations are clearer before broader provider and operator surfaces arrive
- the next architecture arc can start from stronger extraction readiness rather
  than another deployment cleanup pass
