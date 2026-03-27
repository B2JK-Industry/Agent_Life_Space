# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-27`
- baseline: after Durable Planning + Delivery Lifecycle slice

## Ready Now

### P0

1. `T1-E2-S4` Add artifact retention and recovery rules.
   Why now: plan, trace, patch, diff, report, and delivery-oriented artifacts
   are now durable and queryable, but retention and pruning rules are still
   undefined.

2. `T5-E1-S1` Extend policy model to job, artifact, delivery, and external
   gateway decisions.
   Why now: builder now has deterministic review-gate and delivery policy
   profiles, but policy is still fragmented and does not yet govern all
   control-plane decisions uniformly.

3. `T1-E1-S3` Persist job metadata, execution history, artifacts, and cost
   data.
   Why now: plans, traces, and delivery records now have shared persistence,
   but product job metadata and cost history are still split across bounded
   contexts.

### P1

4. `T6-E1-S1` Record per-job model usage and token cost.
   Why now: stronger control-plane persistence now gives usage/cost data a
   meaningful place to land instead of remaining only a local field shell.

5. `T2-E4-S5` Route Telegram and API review entrypoints through `ReviewService`
   instead of legacy review paths.
   Why now: the product/control-plane slices are converging, but channel
   adapters can still drift away from the clean reviewer bounded context.

6. `T5-E1-S5` Bring repository and diff analysis under the shared execution and
   policy boundary.
   Why now: review/build policy has improved, but review-side repo/diff access
   still sits outside the unified execution-policy model.

7. `T4-E1-S4` Reject unsupported work cleanly and honestly.
   Why now: `git_url` is honestly blocked, but intake still cannot acquire or
   import supported remote work.

### P2

8. `T6-E1-S2` Add hard budget, soft budget, and stop-loss behavior.
   Why now: planning already emits budget envelopes, so the next gap is binding
   them to durable runtime controls rather than leaving them advisory.

## What Closed In This Cycle

- `T4-E1-S3` Planner output is now persisted as a first-class handoff record
  with queryable plan IDs, orchestrator methods, and CLI list/get surfaces.
- `T4-E2-S4` Planner decisions now emit durable qualification, budget,
  capability, and delivery traces through the shared control-plane store.
- `T4-E3-S4` Builder delivery now records prepared/awaiting_approval/approved/
  rejected/handed_off lifecycle state plus audit events and report-visible
  delivery inbox entries.
- `T1-E3-S3` Workspace records are now queryable as shared control-plane joins
  over jobs, artifacts, approvals, and delivery bundles.
- `T3-E2-S1` Build verification now performs repo-aware discovery for test,
  lint, and typecheck surfaces instead of relying only on static defaults.
- `T3-E2-S2` Post-build review thresholds are now policy-driven via explicit
  review-gate profiles rather than only a hard-coded fail/critical rule.
- `T1-E1-S1` `ReviewJob` migrated onto shared control-plane primitives.
- `T1-E1-S4` Shared job queries now cover build, review, task, job-runner, and
  agent-loop records.
- `T1-E1-S5` Runtime coexistence rules are now explicit through
  `RuntimeModelService` and `python -m agent --runtime-model`.
- `T1-E2-S5` Shared artifact query/recovery now spans build and review through
  `ArtifactQueryService`, orchestrator list/get methods, and CLI artifact
  inspection.
- `T5-E2-S1` Approval requests are now persistent and queryable with
  job/artifact filters.
- `T6-E2-S4` Operator-facing report/inbox surface exists in runtime, CLI, and
  TS contracts.
- `T3-E1-S1` Builder capability catalog exists and is surfaced in runtime
  status/query metadata.
- `T3-E1-S4` Builder execution now supports resumable checkpoints and CLI
  resume.
- `T4-E1-S1` Unified operator intake now exists for review/build routing, with
  honest rejection of unsupported git-only execution.
- `T4-E1-S2` Qualification now resolves scope signals, risk factors, and a
  policy-backed budget envelope instead of only a heuristic tier.
- `T4-E2-S1` `JobPlan` now exists and is surfaced through intake preview and
  submission outputs.
- `T4-E2-S2` `JobPlan` now models explicit qualify/review/build/verify/deliver
  phases.
- `T4-E2-S3` Planner output now assigns concrete build catalog capabilities plus
  planner profiles and structured budget metadata.
- `T3-E1-S3` Builder now captures deterministic patch + diff artifacts instead
  of relying on placeholder workspace diff metadata.
- `T3-E3-S3` Acceptance now supports richer domain-aware evaluators for
  post-build review, documentation changes, and target-file changes.
- `T6-E2-S2` Operator report now exposes workspace health and worker execution
  summaries.
- `T5-E2-S4` Approval linkage now covers workspace and delivery bundle records
  in addition to jobs and artifacts.
- `T4-E3-S1` Shared `DeliveryPackage` model now exists in the control-plane
  foundation.
- `T4-E3-S2` Builder now assembles artifacts, acceptance results, and review
  output into a build delivery package preview.
- `T4-E3-S3` Build delivery now uses the shared approval gate instead of
  reviewer-only delivery approval.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- artifact retention and recovery rules exist across build/review/delivery
  outputs
- policy extends beyond isolated build profiles into shared job/artifact/
  delivery/gateway decisions
- persisted product-job metadata starts converging into one shared control-plane
  store
- cost and budget decisions become more durable and runtime-relevant
