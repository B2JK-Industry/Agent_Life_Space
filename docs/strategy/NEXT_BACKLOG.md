# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 2 Builder Execution slice

## Ready Now

### P0

1. `T3-E3-S2` Carry richer acceptance structure through intake and planning.
   Why now: structured implementation plans now reach runtime and planning, but
   richer acceptance structure still mostly enters the planner as plain
   strings.

2. `T3-E3-S3` Deepen deterministic acceptance evaluators.
   Why now: the builder now has a real bounded execution path, so the next
   honest builder gap is stronger deterministic acceptance evaluation over
   those changes.

### P1

3. `T5-E1-S3` Add structured denial reasons everywhere.
   Why now: builder denial payloads are now richer, but some finance, social,
   and adapter edges still return plain strings and break operator-facing
   consistency.

4. `T6-E3-S1` Build golden review cases.
   Why now: smoke coverage now guards reviewer handoff structure, so the next
   quality step is durable golden cases rather than only structural regression
   tests.

5. `T8-E3-S4` Prepare data-handling rules for future enterprise requirements.
   Why now: enterprise-facing operating profiles and client-safe evidence
   export now exist, so formalizing retention, redaction, and handoff rules is
   the next honest hardening step.

6. `T7-E1-S1` Define gateway contract for external capabilities.
   Why now: execution policy, operating profiles, approval, retention, and
   cost foundations are now strong enough that a gateway boundary can be
   defined without premature plumbing.

### P2

7. `T8-E2-S3` Add configuration discipline for project roots, secrets, and storage.
   Why now: the higher-level operating environment matrix now exists, but
   concrete deployment-grade configuration discipline is still loose.

## What Closed In This Cycle

- `T5-E1-S2` Build execution now resolves explicit source-aware execution
  policies, records control-plane policy traces, and blocks unsupported
  mutable execution sources with stable deny-by-default payloads.
- `T8-E2-S1` Runtime model now exposes local-owner, operator-controlled, and
  enterprise-hardened operating profiles with default build/delivery/gateway
  posture layered over the lower-level execution environment profiles.
- `T3-E2-S4` Builder verification now persists suite-level plus per-step
  verification artifacts and includes that evidence in the build delivery
  bundle.
- `T3-E1-S5` Builder now has a bounded local implementation engine for
  structured workspace mutations, with persisted per-operation results flowing
  through planning, product-job metadata, and delivery bundles.
- `T3-E3-S4` Acceptance reports now carry delivery-usable summaries and flow
  into build delivery packaging as first-class operator handoff material.
- `T3-E2-S3` Acceptance failures now emit structured denial payloads and
  detailed unmet-required-criterion summaries instead of generic count-only
  rejection strings.
- `T3-E3-S1` Acceptance criteria now have explicit required/optional semantics
  and evaluator hints, with lightweight parsing from operator/CLI strings into
  the richer builder object model.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- richer acceptance structure is visible earlier in intake/planning, not only
  inside the build service
- builder acceptance evaluator coverage grows beyond the current heuristic set
- structured denials cover the remaining major finance/social/adapter edges
- reviewer quality regression moves from smoke checks toward durable golden
  cases
- enterprise data-handling rules are explicit enough to guide future
  client/operator packaging
- the first external gateway contract is explicit enough to plan against safely
