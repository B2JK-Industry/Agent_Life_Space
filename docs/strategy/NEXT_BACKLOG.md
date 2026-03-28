# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 2 Kickoff slice

## Ready Now

### P0

1. `T5-E1-S3` Add structured denial reasons everywhere.
   Why now: core blocker flows already emit stable denial payloads, but some
   finance, social, and adapter edges still return plain strings and break
   operator-facing consistency.

2. `T6-E3-S1` Build golden review cases.
   Why now: smoke coverage now guards reviewer handoff structure, so the next
   quality step is durable golden cases rather than only structural regression
   tests.

3. `T8-E3-S4` Prepare data-handling rules for future enterprise requirements.
   Why now: enterprise-facing operating profiles and client-safe evidence
   export now exist, so formalizing retention, redaction, and handoff rules is
   the next honest hardening step.

### P1

4. `T3-E2-S3` Fail jobs clearly when acceptance criteria are not met.
   Why now: delivery payloads now surface richer verification and acceptance
   evidence, but acceptance failures still need clearer operator-facing
   semantics and deterministic failure reasons.

5. `T3-E3-S1` Define acceptance criteria object model.
   Why now: acceptance handoff is now first-class in delivery, so the criteria
   model itself needs stronger structure before semantic matching can grow
   honestly.

6. `T7-E1-S1` Define gateway contract for external capabilities.
   Why now: execution policy, operating profiles, approval, retention, and
   cost foundations are now strong enough that a gateway boundary can be
   defined without premature plumbing.

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
- `T3-E3-S4` Acceptance reports now carry delivery-usable summaries and flow
  into build delivery packaging as first-class operator handoff material.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- structured denials cover the remaining major finance/social/adapter edges
- reviewer quality regression moves from smoke checks toward durable golden cases
- enterprise data-handling rules are explicit enough to guide future client/operator packaging
- builder acceptance failure states become clearer and more operator-usable
- the first external gateway contract is explicit enough to plan against safely
