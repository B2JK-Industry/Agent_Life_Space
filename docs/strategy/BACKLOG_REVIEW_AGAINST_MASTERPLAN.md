# Backlog Review Against Masterplan

This review compares:
- `MASTER_SOURCE_OF_TRUTH.md`
- `THEMES_EPICS_STORIES.md`
- the audited implementation snapshot on PR `#44` (`8537f26`)

Goal:
- identify where the backlog already aligns with the masterplan
- identify where the backlog is still missing explicit work
- identify where the progress labels need discipline

## Verdict

The backlog is directionally aligned with the masterplan.

It is strongest in:
- platform foundation framing
- reviewer product framing
- governance and enterprise boundary intent

It is weakest in:
- explicit migration away from legacy product paths
- artifact recovery depth
- reviewer workspace binding
- execution policy coverage for repo and diff analysis
- adapter wiring from Telegram/API into the new reviewer slice

## Findings

### High: The backlog did not explicitly call out legacy-path convergence

The masterplan says channel adapters should not own product logic.
That intent exists in the strategy, but the backlog did not explicitly force:
- migration of `/review` and other review entrypoints to `ReviewService`
- removal or deprecation of duplicated reviewer paths
- cleanup of legacy review logic hidden in social/channel code

Impact:
- reviewer can exist as a clean bounded context while production still uses an
  older path
- architectural drift can continue unnoticed

Needed backlog addition:
- add explicit story for routing Telegram/API review requests through the
  reviewer bounded context
- add explicit story for removing duplicated legacy review paths

### High: The backlog was too weak on full artifact recovery semantics

The masterplan is artifact-first and recovery-oriented.
The original backlog mentioned artifacts, but it did not explicitly require:
- persistence of full artifact payloads
- persistence of full intake payloads
- recovery-safe reload of delivery-ready outputs

Impact:
- artifacts may exist at execution time but be insufficiently recoverable for
  delivery, audit, or replay

Needed backlog addition:
- explicitly require recovery-safe persistence for full report and artifact
  payloads, not just metadata

### High: Workspace discipline was described broadly but not anchored to reviewer

The backlog had workspace stories, but did not explicitly force:
- reviewer job to bind to `WorkspaceManager`
- repo and diff analysis to run under shared execution discipline
- workspace identity to be linked back to reviewer jobs

Impact:
- reviewer remains only partially inside the execution plane discipline

Needed backlog addition:
- add reviewer-specific workspace binding and job/workspace linking work

### Medium: Policy backlog does not yet fully cover repo/diff execution path

The masterplan wants deterministic control boundaries.
The backlog speaks strongly about policy, but did not explicitly force:
- git/diff/repo analysis to move under a unified execution boundary
- review-side host operations to be brought into the same control-plane model

Impact:
- reviewer execution may remain outside the eventual policy model

Needed backlog addition:
- add policy story for repository and diff analysis execution boundaries

### Medium: Progress labeling can become too optimistic without explicit rules

`ReviewJob` exists, but that is not the same thing as system-wide canonical job
unification.

Impact:
- themes can be declared "done" too early
- strategic confidence can drift from runtime reality

Needed governance rule:
- use `mostly_complete` only for a bounded context
- reserve `complete` for strategy closure at the phase level

## Recommended Backlog Additions

These additions should be treated as explicit backlog items.

### T1 Platform Foundation

- `T1-E1-S5`: Reconcile coexistence rules between `ReviewJob`, `JobRunner`,
  `Task`, and `AgentLoop`
- `T1-E2-S5`: Persist full intake, report payloads, and artifact payloads for
  recovery-safe reload
- `T1-E3-S5`: Bind reviewer jobs to `WorkspaceManager` and persist
  job/workspace linkage

### T2 Reviewer Product

- `T2-E4-S5`: Route Telegram and API review entrypoints through `ReviewService`
  instead of legacy review paths

### T5 Security, Governance, And Policy

- `T5-E1-S5`: Bring repository and diff analysis under the shared execution and
  policy boundary

### T8 Enterprise Hardening

- `T8-E1-S5`: Remove duplicated reviewer flows and hidden channel-to-product
  coupling

## Review Summary

The backlog is already good enough to guide implementation.
It just needed a sharper edge around migration and convergence.

The next best move is not to add new themes.
It is to close the gap between:
- the new reviewer bounded context
- and the old runtime paths still serving review-like behavior
