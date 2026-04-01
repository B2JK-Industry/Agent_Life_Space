# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-04-01`
- baseline: deployment-contract hardening release `v1.30.0` on `f2345c0`

For the archival narrative of how `main` reached this baseline, see
`AS_IS_TO_BE_2026_04_01.md`.

## Ready Now

Next release candidate: `v1.31.0` — Runtime Contract Closure.

## Why This Slice

- `main` now includes the full Phase 4 merge, settlement-workflow closure, and
  deployment-contract hardening through `v1.30.0`.
- The biggest remaining production risk is no longer settlement behavior, but
  the remaining implicit config/runtime coupling across self-host deployment.
- The next honest step is to close startup/config/runtime contract gaps rather
  than adding another operator surface.

## Proposed Scope

- `P0` `T5-E1-S2`: Keep policy deny-by-default across execution modes
- `P0` `T8-E2-S3`: Add configuration discipline for project roots, secrets,
  and storage
- `P1` `T8-E1-S2`: Remove hidden coupling and implicit shared state
- `P1` `T8-E1-S4`: Make future service extraction obvious from module boundaries

## Exit Criteria For The Next Release

The next slice should be considered successful when:
- self-host startup and runtime behavior fail explicitly instead of falling back
  through hidden defaults
- config for roots, pidfiles, secrets, storage, and runtime posture is clearer,
  more testable, and less environment-dependent
- deny-by-default behavior cannot be bypassed through runtime mode or setup
  shortcuts
- hidden coupling and post-init mutation are reduced enough that future service
  extraction is easier to see and safer to test
