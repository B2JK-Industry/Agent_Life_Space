# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-04-01`
- baseline: runtime-contract closure release `v1.31.0` on `122b152`

For the archival narrative of how `main` reached this baseline, see
`AS_IS_TO_BE_2026_04_01.md`.

## Ready Now

Next release candidate: `v1.32.0` — Selective Extraction Readiness.

## Why This Slice

- `main` now includes the full Phase 4 merge plus settlement, deployment, and
  runtime contract closure through `v1.31.0`.
- Phase 4 is now complete_for_phase, so the next honest step is not another
  closure fix but making future extraction seams and runtime contracts clearer.
- The highest leverage now sits in hidden coupling removal, public boundaries,
  and cleaner service/module seams for the next architecture arc.

## Proposed Scope

- `P0` `T8-E1-S4`: Make future service extraction obvious from module boundaries
- `P0` `T8-E1-S2`: Remove hidden coupling and implicit shared state
- `P1` `T8-E2-S3`: Add configuration discipline for project roots, secrets,
  and storage
- `P1` `T8-E3-S4`: Prepare data-handling rules for future enterprise requirements

## Exit Criteria For The Next Release

The next slice should be considered successful when:
- future service boundaries are easier to see from module seams and public APIs
- hidden coupling and post-init mutation are reduced further across the runtime
- config, storage, and data-handling posture stay explicit as the stack grows
- the next architecture arc can start from clearer extraction readiness instead
  of another cleanup pass
