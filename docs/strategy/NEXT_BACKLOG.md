# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 2 Structured Acceptance slice

## Ready Now

### P0

1. `T5-E1-S3` Add structured denial reasons everywhere.
   Why now: builder denial payloads are now richer, but some finance, social,
   and adapter edges still return plain strings and break operator-facing
   consistency.

2. `T6-E3-S1` Build golden review cases.
   Why now: smoke coverage now guards reviewer handoff structure, so the next
   quality step is durable golden cases rather than only structural regression
   tests.

### P1

3. `T3-E2-S1` Deepen repo-aware verification discovery beyond heuristics.
   Why now: the builder now has stronger acceptance semantics and structured
   evaluator coverage, so the next honest builder gap is smarter verification
   discovery instead of more keyword rules.

4. `T8-E3-S4` Prepare data-handling rules for future enterprise requirements.
   Why now: enterprise-facing operating profiles and client-safe evidence
   export now exist, so formalizing retention, redaction, and handoff rules is
   the next honest hardening step.

5. `T7-E1-S1` Define gateway contract for external capabilities.
   Why now: execution policy, operating profiles, approval, retention, and
   cost foundations are now strong enough that a gateway boundary can be
   defined without premature plumbing.

### P2

6. `T8-E2-S3` Add configuration discipline for project roots, secrets, and storage.
   Why now: the higher-level operating environment matrix now exists, but
   concrete deployment-grade configuration discipline is still loose.

## What Closed In This Cycle

- `T3-E3-S2` Build acceptance criteria now survive as structured objects across
  CLI JSON input, unified operator intake, planner output, and builder
  handoff, with acceptance-summary metadata visible before execution starts.
- `T3-E3-S3` Deterministic acceptance validation now covers structured
  workspace text and JSON checks, change-set path/count/docs checks, explicit
  verification-kind targeting, and structured review-threshold policies.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- structured denial payloads cover the remaining major finance, social, and
  adapter edges
- reviewer quality regression moves from smoke checks toward durable golden
  cases
- builder verification discovery grows beyond the current heuristic repo-signal
  set
- enterprise data-handling rules are explicit enough to guide future
  client/operator packaging
- the first external gateway contract is explicit enough to plan against safely
