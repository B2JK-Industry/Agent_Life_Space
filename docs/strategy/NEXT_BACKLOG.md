# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-28`
- baseline: after Phase 1 Delivery Closure slice

## Ready Now

### P0

1. `T5-E1-S2` Keep policy deny-by-default across execution modes.
   Why now: review execution, intake gating, delivery lifecycle, and evidence
   export are now stronger, but build/runtime actions still do not share one
   unified deny-by-default enforcement boundary.

2. `T8-E2-S1` Define local, operator, and enterprise environment profiles.
   Why now: flow-level environment profiles exist, so the next step is turning
   them into a higher-level operating model that can guide deployment and
   policy defaults honestly.

3. `T2-E4-S1` Prepare copy-paste-ready PR comments and summary review artifacts.
   Why now: review delivery lifecycle and client-safe export are now much
   stronger, so the next reviewer-facing gap is a truly operator-usable
   handoff artifact for PR and issue workflows.

### P1

4. `T3-E2-S4` Capture all verification artifacts and verdicts.
   Why now: builder verification is repo-aware, but delivery/evidence still
   benefits from richer first-class verification artifacts instead of only
   summarized reports.

5. `T8-E3-S4` Prepare data-handling rules for future enterprise requirements.
   Why now: client-safe evidence packaging and retention prune flows now exist,
   so formalizing enterprise data-handling rules is the next honest hardening
   step.

6. `T6-E3-S3` Add review eval smoke checks to CI or local gating.
   Why now: reviewer phase-1 scope is effectively closed functionally, so the
   next quality gap is regression discipline rather than another feature flag.

7. `T5-E1-S3` Add structured denial reasons everywhere.
   Why now: approval backlog and budget/report posture are richer now, but
   deny-by-default still needs more consistent operator-visible reasoning.

### P2

8. `T7-E1-S1` Define gateway contract for external capabilities.
   Why now: policy, retention, approval, environment profile, and cost
   foundations are stronger now, making gateway definition the next clean
   boundary rather than premature plumbing.

## What Closed In This Cycle

- `T4-E3-S2` Review delivery now assembles into the shared delivery lifecycle
  instead of staying on a parallel bundle path.
- `T1-E2-S4` Retained artifacts now support an explicit prune workflow through
  the control plane, orchestrator, and CLI.
- `T8-E3-S3` Evidence export now supports a client-safe review mode with
  redacted approval and delivery packaging.
- `T6-E2-S3` Operator report now exposes approval backlog status/category
  counts, blocked reasons, and partial-approval detail.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- deny-by-default policy reaches deeper into build/runtime execution
- higher-level local/operator/enterprise environment profiles are explicit
- reviewer handoff gains copy-paste-ready PR comment and summary artifacts
- builder verification evidence becomes more first-class and queryable
- reviewer quality regression checks start moving toward repeatable gating
