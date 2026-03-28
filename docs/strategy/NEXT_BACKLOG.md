# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 2 Verification Hardening slice

## Ready Now

### P0

1. `T7-E1-S2` Add auth, timeout, retry, and rate-limit policy.
   Why now: the first external gateway contract now exists, so the next honest
   step is turning that contract into an enforceable runtime boundary instead
   of leaving it as planning metadata only.

2. `T6-E3-S2` Measure finding precision and false positives.
   Why now: golden review cases now exist in CI, so the next quality step is
   to turn them into tracked quality signals instead of one-time regression
   fixtures.

### P1

3. `T5-E1-S4` Ensure policy is deterministic and separately testable.
   Why now: structured denials now span more runtime edges, but build, review,
   gateway, and broader action execution still are not governed by one clearly
   testable enforcement story.

4. `T8-E2-S3` Add configuration discipline for project roots, secrets, and storage.
   Why now: runtime/data-handling contracts are clearer, but deployment-grade
   configuration discipline is still loose across project roots and storage.

5. `T3-E2-S2` Add review-after-build pass before completion.
   Why now: post-build review already exists and verification discovery is now
   deeper, so the next builder verification gap is converging that review gate
   with the wider execution-policy boundary.

### P2

6. `T7-E1-S3` Add audit and cost tracking for external calls.
   Why now: the gateway contract now names request/response and denial fields,
   so the next safe move is durable audit and cost bookkeeping before any real
   provider integration.

7. `T7-E1-S4` Add policy gating for when external capability use is allowed.
   Why now: the gateway contract and policy defaults both exist now, but they
   are not yet tied together into one enforceable external-call decision path.

## What Closed In This Cycle

- `T3-E2-S1` Builder verification discovery now looks at Python and Node/TS
  repo signals, package scripts, Makefile targets, CI workflow hints, and
  repo-local toolchains before resolving verification commands.
- `T5-E1-S3` Structured denial payloads now cover the remaining major social,
  web, tool-execution, and finance-budget edges instead of falling back to
  plain strings.
- `T6-E3-S1` Reviewer quality moved from smoke-only structure checks toward
  durable golden verdict cases, and CI now runs both smoke and golden suites.
- `T7-E1-S1` Runtime model now exposes a first explicit gateway contract for
  future external capabilities.
- `T8-E3-S4` Runtime model now carries explicit internal, client-safe, and
  retained-trace data-handling rules for future enterprise packaging and
  handoff work.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- the gateway contract grows from planning metadata into an enforceable
  auth/timeout/retry/rate-limit runtime boundary
- golden review cases gain tracked precision/false-positive signals instead of
  only static verdict expectations
- policy behavior across build/review/gateway edges is clearer and more
  separately testable
- deployment-oriented configuration discipline becomes explicit for roots,
  secrets, and storage
- post-build review policy converges further with the wider build execution
  boundary
