# Controlled Environments

This guide documents the practical Phase 2 deployment posture for Agent Life
Space after Builder v1 closure.

Use it when you want to run ALS consistently in one of the supported operating
profiles:
- `local_owner`
- `operator_controlled`
- `enterprise_hardened`

## What This Covers

- project-root and data-dir expectations
- gateway and vault configuration for `obolos.tech`
- release-readiness checks before a release or handoff
- the execution posture behind build, review, acquisition, and delivery flows

## Operating Profiles

### `local_owner`

Use when one operator is running ALS locally and accepts repo-local execution
tradeoffs.

Expected posture:
- builder executes in managed workspaces
- reviewer stays `READ_ONLY_HOST`
- managed import/acquisition is allowed
- external delivery stays approval-gated

Good fit for:
- local review runs
- bounded builder implementation plans
- release-readiness checks before a local release or handoff

### `operator_controlled`

Use when ALS is run in a more disciplined owner-operated environment with
clearer runtime boundaries and repeatable config.

Expected posture:
- stable project root and data dir
- gateway config resolved from env and/or vault
- approvals and reporting treated as operational surfaces, not just local dev
  tooling
- release-readiness gate run before outbound delivery or release creation

Good fit for:
- regular internal operation
- approved build/review handoff through the gateway
- repeatable release workflow

### `enterprise_hardened`

Use when you want the stricter posture implied by the runtime model, even
though the whole stack is not yet extraction-grade.

Expected posture:
- explicit environment and data-handling discipline
- gateway auth and targets fully configured
- approval and evidence paths treated as first-class audit surfaces
- release-readiness required before release or external handoff

Important:
- this profile is useful now for stricter operation
- it is not yet a full enterprise-hardening guarantee across the whole stack

## Project Root And Data Directory

Recommended:
- run ALS from the checked-out repository root
- keep the SQLite/control-plane data under the repo-local configured data dir
- avoid ad-hoc alternate roots unless you also validate runtime model and
  gateway posture there

The current runtime now prefers the checked-out repository root when available
instead of falling back too eagerly to a home-directory assumption.

## Gateway Configuration

Current provider:
- `obolos.tech`

Relevant environment variables:
- `AGENT_OBOLOS_REVIEW_WEBHOOK_URL`
- `AGENT_OBOLOS_REVIEW_WEBHOOK_URL_BACKUP`
- `AGENT_OBOLOS_BUILD_WEBHOOK_URL`
- `AGENT_OBOLOS_BUILD_WEBHOOK_URL_BACKUP`
- `AGENT_OBOLOS_AUTH_TOKEN`

Relevant vault secret:
- `obolos.tech.auth_token`

Resolution posture:
- target URLs come from env vars
- auth token can come from env or from the vault secret
- route readiness is visible through the gateway catalog

Useful checks:

```bash
python -m agent --gateway-catalog
python -m agent --gateway-catalog --gateway-provider obolos.tech --gateway-capability review_handoff_v1 --gateway-export-mode client_safe
python -m agent --gateway-catalog --gateway-provider obolos.tech --gateway-capability build_bundle_v1
```

## Release-Readiness Gate

Before a release or important external handoff, run:

```bash
python -m agent --release-readiness --release-readiness-release-label v1.15.0
```

What it checks today:
- golden review quality posture
- regression against previous quality baseline
- release policy thresholds
- gateway catalog posture and warnings

Fail-closed behavior:
- command exits non-zero when the deterministic release gate is not ready
- CI can run the same gate to prevent a weak release from landing

## Practical Phase 2 Workflow

Recommended order:
1. run local tests, lint, and typecheck
2. inspect runtime/gateway posture
3. run `--release-readiness`
4. only then create the release and external handoff

Typical command set:

```bash
./.venv/bin/ruff check .
./.venv/bin/pytest -q
PATH="$PWD/.tools/node-v24.14.0-darwin-arm64/bin:$PATH" npm run typecheck
python -m agent --runtime-model
python -m agent --report
python -m agent --release-readiness --release-readiness-release-label v1.15.0
```

## Phase 2 Reality Check

This deployment guide is good enough for Phase 2 builder closure.

It does not claim:
- general code generation
- a live operator backend/UI
- a fully unified runtime enforcement engine
- extraction-grade service boundaries across the whole stack

That remaining work belongs to Phase 3 operatorization and Phase 4 enterprise
hardening.
