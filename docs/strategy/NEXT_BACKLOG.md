# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-27`
- baseline: after Unified Control-Plane Persistence + Retention slice

## Ready Now

### P0

1. `T2-E4-S5` Route Telegram and API review entrypoints through `ReviewService`
   instead of legacy review paths.
   Why now: persisted product-job and artifact state now converge through the
   control plane, so channel adapters are the clearest remaining place where
   reviewer truth can still drift from runtime truth.

2. `T5-E1-S5` Bring repository and diff analysis under the shared execution and
   policy boundary.
   Why now: control-plane policy now covers persistence and retention, but
   review-side repo/diff access still lives outside the unified policy surface.

3. `T6-E1-S2` Add hard budget, soft budget, and stop-loss behavior.
   Why now: per-job cost entries now persist durably, so budgets can graduate
   from advisory planner metadata into real runtime controls.

### P1

4. `T4-E1-S4` Reject unsupported work cleanly and honestly.
   Why now: `git_url` is honestly blocked, but intake still cannot acquire or
   import supported remote work.

5. `T6-E1-S4` Surface cost and margin hints to the operator.
   Why now: the ledger now exists and is queryable, but the operator still does
   not get explicit cost posture in planning and delivery decisions.

6. `T5-E2-S2` Support approvals for risky execution and external delivery.
   Why now: shared persistence/policy now cover jobs, artifacts, bundles, and
   cost, so risky execution approvals are the next real governance gap.

7. `T8-E3-S1` Add retention/evidence packaging for compliance-friendly export.
   Why now: retention records now exist, but there is still no evidence bundle
   or compliance-oriented export path on top of them.

### P2

8. `T6-E2-S1` Track job status, failures, retries, and durations.
   Why now: persisted product-job records now exist, but retry/failure telemetry
   is still thinner than the new control-plane surfaces around them.

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
- `T1-E2-S4` Retained artifact records now define recovery/expiry rules across
  build, review, trace, and delivery-bundle outputs, and those rules are
  surfaced through artifact queries and operator reporting.
- `T5-E1-S1` Shared policy now covers job persistence, artifact retention, and
  external gateway defaults in addition to delivery/review gating profiles.
- `T1-E1-S3` Build and review jobs now persist shared `ProductJobRecord`
  metadata, artifact references, and policy context in the control plane.
- `T6-E1-S1` Per-job usage, token, and cost data now land in a durable
  control-plane ledger with CLI and report visibility.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- review entrypoints converge cleanly through `ReviewService` instead of legacy
  adapter logic
- repository and diff access move under the same shared execution/policy model
- budgets become enforceable at runtime instead of remaining advisory
- operator surfaces start exposing actionable cost posture and richer failure
  telemetry
