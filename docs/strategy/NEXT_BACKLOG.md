# Next Backlog

This file is the near-term execution backlog derived from the current state of
`main`.

Assessment basis:
- branch: `main`
- interpretation date: `2026-03-31`
- baseline: after the documented buyer-side Obolos API-call slice

## Ready Now

### P2

1. `T7-E2-S6` Add file-upload-safe and x402 payment-aware marketplace calls.
   Why now: the buyer-side API path covers JSON and query routes, but many
   useful marketplace APIs still need multipart uploads and richer payment flow.

## What Closed In This Cycle

- `T7-E1-S1` Gateway contracts now distinguish handoff-style delivery from
  direct API invocation via `external_capability_gateway_v1` and
  `external_api_call_v1` instead of forcing both through one webhook-shaped
  envelope.
- `T7-E2-S2` `obolos.tech` now exposes documented buyer-side capability routes
  for marketplace catalog listing, wallet balance, and slug-based API calls in
  addition to the older handoff compatibility routes.
- `T7-E2-S3` Buyer-side API calls now persist traces, cost records, retained
  request/response artifacts, and structured `payment required` denials instead
  of surfacing only raw HTTP failure detail.

- `T3-E1-S5` The bounded local builder engine now supports deterministic
  `copy_file` and `move_file` mutations in addition to the earlier
  insert/delete-safe workspace operations, keeping execution scoped without
  pretending to be freeform generation.
- `T3-E3-S2` Acceptance criteria now reach deeper into execution and delivery:
  implementation-backed criteria can validate changed-operation counts, changed
  paths, operation types, and required implementation mode through the same
  delivery-ready acceptance report.
- `T5-E1-S4` Deterministic policy coverage now includes separately testable
  builder guardrail evaluation, provider outcome classification, and
  release-readiness thresholds.
- `T6-E3-S4` Quality trend telemetry now drives a real release-readiness gate
  surfaced through CLI, CI, control-plane traces, and operator reporting.
- `T4-E3-S4` Delivery workflow now records provider-specific outcomes in the
  shared operator report instead of stopping at generic gateway success/fail
  events.
- `T8-E2-S4` Controlled-environment deployment documentation now exists for the
  vault, gateway, runtime profile, and release-readiness path.

- `T4-E4` Operator Telegram Surface: `/report`, `/intake`, and `/build`
  Telegram commands now delegate to the unified operator services, providing
  overview/inbox/budget reporting and qualify→plan→execute intake flow directly
  from Telegram (v1.19.0).

- `T4-E2-S3` Review/verify/deliver planner phases now bind to execution policies
  and delivery policies (not just planner profiles). Execution policy enrichment
  completed alongside `/jobs` and `/deliver` Telegram commands (v1.20.0).

- `T5-E1-S1` Policy model now includes RuntimeActionRequest and
  evaluate_runtime_action(), pushing policy toward a broader runtime action
  boundary instead of isolated per-domain branches (v1.21.0).
- `T6-E1-S1` Per-job cost recording now includes a cost accuracy feedback loop
  that validates estimates against actual recorded costs, improving operator
  cost posture beyond raw ledger entries (v1.21.0).

- `T4-E3-S4` Provider delivery workflow now surfaces provider outcome, receipt,
  attention flags, retry capability, and outcome-based filtering through
  enriched `/deliver` commands and `/report delivery`, making provider outcomes
  actionable operator workflow instead of just report detail (v1.22.0).
- `T6-E2-S1` Runtime telemetry now captures point-in-time snapshots of job
  throughput, latency percentiles, cost, delivery health, and system resources
  as persisted trace records, with time-window aggregation, trend detection,
  and `/telemetry` Telegram command for operator visibility (v1.22.0).

- `T7-E2-S5` Seller-side Obolos publishing and wallet top-up now have documented
  capability routes (`seller_publish_v1`, `wallet_topup_v1`), gateway request/response
  modes, and wallet auth integration (v1.23.0).
- `T7-E1-S1` Gateway contract now supports multi-provider resolution:
  `list_providers_for_capability()`, `resolve_capability_across_providers()`,
  `call_api_across_providers()` with intelligent fallback, plus capability-to-providers
  map in gateway catalog (v1.23.0).
- `T8-E1-S3` Architecture invariants now enforced through 22 tests covering import
  graph boundaries, execution mode contracts, gateway boundary enforcement,
  cross-domain isolation, shared control-plane contracts, and multi-provider
  gateway contracts (v1.23.0).

## Exit Criteria For The Next Backlog Slice

The next slice should be considered successful when:
- planner output stops being only informative and starts binding more of the
  operator workflow to real runtime capabilities
- provider-specific delivery outcomes become actionable operator workflow, not
  just report detail
- policy decisions across build, review, and gateway read more like one shared
  control story
- runtime telemetry and cost posture become more useful for active operator
  decisions
- Phase 3 starts with operatorization and runtime cohesion, not with another
  round of builder-core cleanup
