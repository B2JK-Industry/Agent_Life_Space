# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-27`
- baseline: after Builder Control-Plane Mini-Release

## Ready Now

### P0

1. `T1-E1-S1` Migrate `ReviewJob` onto shared control-plane primitives.
   Why now: shared build/review list/get queries now exist, so `ReviewJob`
   itself is the clearest remaining convergence gap.

2. `T5-E2-S1` Persist approval requests and link them to jobs and artifacts.
   Why now: build/review jobs are now first-class queryable runtime objects,
   but approval history is still not durable or cross-linked enough.

3. `T6-E2-S4` Add an operator-facing reporting surface or inbox.
   Why now: the shared query surface exists, but there is still no place for an
   operator to inspect and act on blocked/completed work.

### P1

4. `T3-E1-S1` Define implementation capability catalog.
   Why now: builder now has real entrypoints, so the next honest upgrade is to
   make capabilities explicit instead of adding more wrappers.

5. `T3-E1-S4` Add resumable build checkpoints.
   Why now: builder now has entrypoint, query, and review-gate foundations, so
   interruption recovery is the next practical runtime upgrade.

6. `T4-E1-S1` Create a unified operator intake model for review/build routing.
   Why now: review and build are now both first-class runtime flows, but intake
   still enters them through separate local models.

### P2

7. `T1-E1-S4` Extend shared job query coverage beyond build/review.
   Why now: list/get job queries now exist for build and review, but they still
   do not cover `JobRunner`, `Task`, or `AgentLoop`.

8. `T3-E3-S3` Add richer domain-specific acceptance evaluators.
   Why now: builder now has verification, review-after-build, and structured
   acceptance artifacts, but acceptance logic is still intentionally rule-based.

## Bug Fixes Already Closed In This Cycle

- Builder runtime is tracked on `main` and no longer hidden behind `.gitignore`.
- Build jobs sync the requested repo into the managed workspace before
  verification.
- Builder verification now adds typecheck when project config is present.
- Acceptance criteria fail closed and support explicit `verify:` commands.
- Review job recovery preserves `include_patterns` and `exclude_patterns`.
- `AgentOrchestrator` now initializes builder storage/service and exposes local
  build/review status counters.
- Builder can now start through shared runtime and CLI entrypoints.
- Successful build jobs can now invoke deterministic review-after-build gating.
- Build and review jobs are now queryable through one shared control-plane
  surface.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- shared queries reach beyond build/review into the wider runtime surface
- approval state becomes durable and linked to runtime job/artifact records
- an operator-facing surface can inspect blocked/completed work from the shared
  query layer
- backlog progress can move from builder-only vertical slice into real
  operator/control-plane usability
