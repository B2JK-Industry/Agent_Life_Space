# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-27`
- baseline: after Review Runtime Convergence + Budget Governance slice

## Ready Now

### P0

1. `T8-E3-S1` Improve audit export and artifact traceability.
   Why now: retained artifacts, delivery bundles, and policy traces now exist,
   but there is still no evidence package/export surface on top of them.

2. `T6-E2-S1` Track job status, failures, retries, and durations.
   Why now: persisted product-job records and runtime traces exist, but
   failure/retry telemetry is still thinner than the shared query and reporting
   surfaces around them.

3. `T6-E1-S3` Make escalation budget-aware.
   Why now: budget posture now blocks or gates runtime intake execution, so
   escalation policy is the next honest control-plane layer to align.

### P1

4. `T4-E1-S4` Add supported repo acquisition/import path behind honest gating.
   Why now: unsupported work is now rejected honestly, but there is still no
   safe acquisition path for supported remote import.

5. `T5-E2-S3` Support multi-step approvals where needed.
   Why now: unified intake and delivery now both create approval requests,
   making richer approval workflows the clearest next governance gap.

6. `T1-E3-S4` Add environment profiles for safe execution modes.
   Why now: review execution policy and runtime budget gating now exist, so
   environment-sensitive behavior can be formalized more explicitly.

7. `T6-E1-S4` Deepen operator cost posture and margin hints.
   Why now: budget posture is visible now, but escalation and delivery still
   lack richer operator-facing cost context.

### P2

8. `T7-E1-S1` Define gateway contract for external capabilities.
   Why now: policy, retention, approval, and cost foundations are stronger now,
   making gateway definition the next clean boundary rather than premature
   plumbing.

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
- `T2-E4-S5` Telegram `/review` and structured `/api/review` now converge
  through the shared review runtime, and review intake preserves channel source
  through recovery-safe persistence.
- `T5-E1-S5` Review-side repository and diff access now runs under explicit
  deterministic review execution policies with durable control-plane traces and
  persisted product-job metadata.
- `T6-E1-S2` Unified intake now enforces hard-cap and stop-loss budget blocks
  at runtime instead of treating budgets as preview-only metadata.
- `T6-E1-S4` Finance budget state and the operator report now surface explicit
  budget posture, warnings, and budget-attention inbox items.
- `T5-E2-S2` Unified intake can now request finance or tool approval before
  execution for budget-sensitive or high-risk work.
- `T4-E1-S4` Runtime intake now reports honest `blocked` and
  `awaiting_approval` states instead of pretending unsupported or policy-blocked
  work started.

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- retained artifacts, traces, and delivery bundles can be assembled into a
  compliance-friendly evidence export
- product-job records surface clearer failure, retry, and duration telemetry
- escalation logic becomes budget-aware instead of living outside runtime cost
  posture
- operator/runtime surfaces can distinguish safe acquisition paths from
  unsupported remote work
