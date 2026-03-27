# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-27`
- baseline: after Builder Runtime Integration + Review-Driven Hardening

## Ready Now

### P0

1. `T3-E1-S2` Expose builder through a real product entrypoint.
   Why now: builder is tracked, workspace-synced, and orchestrator-wired, but
   there is still no API, chat, or operator path that starts build jobs.

2. `T3-E2-S2` Add review-after-build before completion.
   Why now: builder verifies code mechanically, but still lacks a reviewer pass
   before any delivery-ready completion state.

3. `T1-E1-S4` Add a cross-system job query layer.
   Why now: review and build now both persist useful job state, but inspection
   remains fragmented and operator automation has no common query surface.

### P1

4. `T1-E1-S1` Migrate `ReviewJob` onto shared control-plane primitives.
   Why now: builder already consumes the shared job model directly, so reviewer
   is now the clearest remaining convergence gap.

5. `T5-E2-S1` Persist approval requests and link them to jobs and artifacts.
   Why now: delivery gating exists, but approvals are still not durable enough
   for stronger audit and compliance expectations.

6. `T6-E2-S4` Add an operator-facing reporting surface or inbox.
   Why now: local build/review counters now exist, but nobody outside local
   process status can act on them yet.

### P2

7. `T3-E1-S4` Add resumable build checkpoints.
   Why now: recovery-safe job storage exists, but interruption recovery still
   restarts the whole build flow.

8. `T4-E1-S1` Create a unified operator intake model for review/build routing.
   Why now: the product now has two meaningful bounded contexts, but operator
   intake still does not route work between them explicitly.

## Bug Fixes Already Closed In This Cycle

- Builder runtime is tracked on `main` and no longer hidden behind `.gitignore`.
- Build jobs sync the requested repo into the managed workspace before
  verification.
- Builder verification now adds typecheck when project config is present.
- Acceptance criteria fail closed and support explicit `verify:` commands.
- Review job recovery preserves `include_patterns` and `exclude_patterns`.
- `AgentOrchestrator` now initializes builder storage/service and exposes local
  build/review status counters.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- a real entrypoint can start build jobs end-to-end
- build completion can invoke a review-after-build gate
- review and build jobs are queryable through one shared inspection layer
- backlog progress can move Builder Product from early foundation toward a
  usable controlled execution path
