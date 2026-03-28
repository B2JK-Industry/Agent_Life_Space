# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 1 Final Closure slice

## Ready Now

### P0

1. `T5-E1-S2` Keep policy deny-by-default across execution modes.
   Why now: review/tool/intake/delivery/export blockers now emit structured
   denials, but build/runtime actions still do not share one unified
   deny-by-default enforcement boundary.

2. `T8-E2-S1` Define local, operator, and enterprise environment profiles.
   Why now: flow-level environment profiles exist, so the next step is turning
   them into a higher-level operating model that can guide deployment and
   policy defaults honestly.

3. `T3-E2-S4` Capture all verification artifacts and verdicts.
   Why now: builder verification is repo-aware, but delivery/evidence still
   benefits from richer first-class verification artifacts instead of only
   summarized reports.

### P1

4. `T3-E3-S4` Produce acceptance reports for delivery.
   Why now: acceptance reports exist as build artifacts, but delivery and
   evidence packaging still treat them more as side data than as first-class
   operator handoff material.

5. `T8-E3-S4` Prepare data-handling rules for future enterprise requirements.
   Why now: client-safe evidence packaging and retention prune flows now exist,
   so formalizing enterprise data-handling rules is the next honest hardening
   step.

6. `T5-E1-S3` Add structured denial reasons everywhere.
   Why now: core runtime blockers now use stable denial payloads, but some
   remaining finance/social/adapter edges still return plain error strings.

7. `T6-E3-S1` Build golden review cases.
   Why now: CI now runs a smoke check for reviewer handoff artifacts, so the
   next quality step is adding durable golden cases instead of only structural
   regression tests.

### P2

8. `T7-E1-S1` Define gateway contract for external capabilities.
   Why now: policy, retention, approval, environment profile, and cost
   foundations are stronger now, making gateway definition the next clean
   boundary rather than premature plumbing.

## What Closed In This Cycle

- `T2-E4-S1` Review delivery now emits operator-summary and copy-paste-ready
  PR comment artifacts and includes them in shared delivery bundles and
  client-safe evidence export.
- `T6-E3-S3` Reviewer handoff smoke checks now run in CI through
  `tests/test_review_eval_smoke.py`.
- `T5-E1-S3` Structured denial payloads now cover core tool/intake/build/
  review/export blocker flows and feed operator-visible blocked-job detail.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- deny-by-default policy reaches deeper into build/runtime execution
- higher-level local/operator/enterprise environment profiles are explicit
- builder verification and acceptance evidence become more first-class and queryable
- structured denials cover the remaining major runtime edges
- reviewer quality regression moves from smoke checks toward golden eval cases
