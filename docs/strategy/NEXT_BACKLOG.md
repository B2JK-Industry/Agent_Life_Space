# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-04-01`
- baseline: post-merge closure release `v1.28.1` on `f3f6b80`

For the archival narrative of how `main` reached this baseline, see
`AS_IS_TO_BE_2026_04_01.md`.

## Ready Now

Next release candidate: `v1.29.0` — Settlement Workflow Closure.

## Why This Slice

- `main` now includes the full Phase 4 merge plus the `v1.28.1` post-merge
  closure pass.
- Archive retrieval is already delivered, so the next highest-leverage gap is
  not more export plumbing but turning settlement from `foundation_only` into a
  real operator workflow.
- The current runtime can detect payment-required failures, create settlement
  requests, expose them via API and Telegram, and top up manually, but it still
  loses pending state on restart and lacks a dashboard-driven retry loop.

## Proposed Scope

- `P0` `T4-E3-S5`: Add active settlement workflow across API, Telegram, and
  dashboard surfaces
- `P0` `T7-E2-S7`: Persist settlement requests and recovery state
- `P1` `T7-E2-S8`: Add approval-backed 402 → balance → topup → retry loop
- `P1` `T4-E4-S6`: Bring settlement control to parity across Telegram and the
  broader operator workflow

## Exit Criteria For The Next Release

The next slice should be considered successful when:
- settlement requests survive restart and are queryable/recoverable like other
  operator-facing runtime state
- operators can list, approve, deny, and progress settlement work from both the
  authenticated API and the dashboard, with Telegram staying in sync
- payment-required gateway failures can move through an explicit approved retry
  path instead of stopping at a passive denial
- operator reporting shows pending settlement attention and recent settlement
  outcomes without overstating automation that does not yet exist
