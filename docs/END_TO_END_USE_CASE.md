# End-to-End Use Case

This document describes one realistic ALS scenario that exercises most of the
runtime, not just one module in isolation.

It is intentionally operator-centered. ALS is not a one-shot script runner. The
system is strongest when an owner or operator uses Telegram, CLI, API, and the
dashboard together as one controlled workflow.

## Goal

A self-hosted operator receives a change request for a repository:

- inspect the repo
- plan the work
- request approval for risky or paid steps
- optionally call an external paid API capability
- execute a bounded build
- run verification
- run a structured post-build review
- produce evidence and delivery artifacts
- archive the result
- inspect the whole state through operator surfaces

## Components Exercised

This scenario touches most of ALS:

- identity and owner profile
- Telegram, API, dashboard, and CLI operator surfaces
- intake and planner
- control-plane persistence
- approval queue
- gateway catalog and provider API calls
- settlement workflow for `402 payment required`
- workspace manager
- bounded builder
- verification discovery and execution
- structured review runtime
- acceptance evaluators
- persisted artifacts and delivery records
- reporting, inbox, and runtime model
- evidence export and archival

## Preconditions

Before running the scenario, have these available:

- a configured ALS instance
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`, `AGENT_VAULT_KEY`, `AGENT_API_KEY`
- optional but recommended:
  - `AGENT_NAME`
  - `AGENT_SERVER_NAME`
  - `AGENT_OWNER_NAME` / `AGENT_OWNER_FULL_NAME`
- one working LLM backend
- a repo to review and modify
- optional external gateway configuration if you want to exercise provider calls

Useful baseline checks:

```bash
.venv/bin/python -m agent --status
.venv/bin/python -m agent --runtime-model
.venv/bin/python -m agent --report
.venv/bin/python -m agent --release-readiness --release-readiness-release-label local-e2e
```

## Scenario

### 1. Establish the operator context

The owner starts ALS and confirms:

- the correct owner identity is loaded
- the runtime profile is expected for the server
- approval queue and reporting are reachable

This can happen over:

- Telegram for conversational control
- dashboard for visual operator control
- CLI for repeatable local testing
- API for external automation

### 2. Import work through unified intake

The operator submits a request such as:

`Review this repo, prepare a bounded fix for the failing workflow, and do not deliver anything without review and acceptance gates.`

Example CLI intake:

```bash
.venv/bin/python -m agent \
  --intake-repo /path/to/repo \
  --intake-work-type build \
  --intake-description "Review, fix, verify, and prepare evidence" \
  --intake-plan-file plan.json \
  --intake-acceptance-file acceptance.json \
  --intake-preview
```

Expected ALS behavior:

- qualify the work
- assign scope and risk
- estimate budget
- decide whether approval is required
- persist a `JobPlan`
- emit planning traces

### 3. Pause on approval when the work is risky

If the work is expensive, risky, or delivery-impacting, ALS should not continue
blindly. Instead it should:

- create an approval request
- link it to the plan and later job records
- surface it in the operator inbox
- wait for operator approval

The operator can inspect pending approvals from the report, dashboard, Telegram,
or API, depending on which surfaces are active in the deployment.

### 4. Use an external provider capability when needed

If the task needs an outside capability, for example OCR, enrichment, or
provider-backed review handoff, the operator can route it through the gateway.

Example:

```bash
.venv/bin/python -m agent \
  --call-provider-api \
  --provider-api-provider obolos.tech \
  --provider-api-capability marketplace_api_call_v1 \
  --provider-api-resource ocr-text-extraction \
  --provider-api-method POST \
  --provider-api-json '{"mode":"fast"}'
```

Expected ALS behavior:

- resolve the provider route through the policy registry
- record cost and traces
- persist request/response artifacts
- block or request approval if policy requires it

If the provider responds with `402 payment required`, ALS should:

- create a settlement request
- persist it
- surface it in the dashboard, Telegram, API, and report inbox
- let the operator approve, deny, or execute the settlement
- retry the original call after successful top-up if the route supports it

### 5. Run the bounded builder

After approval and any required settlement, ALS executes a bounded build plan.

Example:

```bash
.venv/bin/python -m agent \
  --build-repo /path/to/repo \
  --build-description "Apply approved bounded plan" \
  --build-plan-file plan.json \
  --build-acceptance-file acceptance.json
```

Expected ALS behavior:

- create a workspace
- apply deterministic file operations
- persist job, workspace, and artifact records
- prepare patch and diff artifacts

### 6. Verify and review the result

ALS should not stop at “the file changed”.

It should:

- discover and run verification steps such as tests, lint, and typecheck
- persist verification reports
- run a structured post-build review
- count findings by severity
- evaluate required acceptance criteria

This is where ALS turns from a patch generator into an operator-grade build and
review system.

### 7. Decide whether the change is acceptable

If required acceptance fails, ALS should:

- mark the build as failed or rejected
- retain the artifacts
- keep the result reviewable
- avoid pretending the work is ready for delivery

If required acceptance passes, ALS should:

- keep the delivery bundle and evidence package ready
- make the result exportable or handoff-ready

### 8. Export evidence and archive the run

Once the job is complete, the operator should be able to inspect and export the
evidence.

Examples:

```bash
.venv/bin/python -m agent --list-persisted-jobs
.venv/bin/python -m agent --list-artifacts
.venv/bin/python -m agent --list-deliveries
.venv/bin/python -m agent --export-evidence-job <job_id>
.venv/bin/python -m agent --export-evidence-job <job_id> --export-evidence-mode client_safe
```

Expected ALS behavior:

- provide patch, diff, verification, review, and acceptance artifacts
- preserve traceability from plan to approval to execution
- support archival for later audit or operator replay

### 9. Inspect the whole run from the operator side

Finally, the operator reviews the whole system state:

```bash
.venv/bin/python -m agent --report
.venv/bin/python -m agent --list-plans
.venv/bin/python -m agent --list-traces
.venv/bin/python -m agent --list-workspaces
.venv/bin/python -m agent --list-cost-ledger
```

The report should show:

- recent jobs
- failed or pending attention items
- approvals
- settlement attention
- workspace attention
- retained artifacts
- delivery and trace posture

## What “Success” Looks Like

This use case is successful when ALS does all of the following truthfully:

- plans before acting
- pauses for approval when policy says it must
- records external provider usage and settlement state
- executes a bounded change in a workspace
- verifies the result
- reviews the result
- rejects delivery when acceptance is not met
- exports evidence and archives the run
- surfaces the entire state through operator reporting

## What “Failure” Should Look Like

A good ALS run is not one that always ends green. It is one that fails honestly.

Good failure behavior includes:

- policy-blocked provider calls with structured denial reasons
- settlement requests that survive restart and need explicit operator action
- builds that stop on failed verification
- builds that are rejected when review findings violate acceptance
- reports that show the failure clearly instead of hiding it

## Recommended Demo Order

If you want to demonstrate ALS to another operator, use this order:

1. `--status`
2. `--runtime-model`
3. `--gateway-catalog`
4. unified `--intake-*`
5. approval handling
6. optional provider API call
7. bounded `--build-*`
8. `--report`
9. `--export-evidence-job`
10. dashboard and Telegram follow-up

## Notes

- Not every deployment will use every surface every day.
- The strongest ALS posture is self-hosted and operator-controlled.
- If a step in this scenario is blocked by policy, missing gateway config, or
  acceptance findings, that is often proof that the system is behaving
  correctly, not proof that the system is broken.
