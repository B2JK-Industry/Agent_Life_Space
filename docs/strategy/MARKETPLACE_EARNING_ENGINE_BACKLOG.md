# Marketplace Earning Engine Backlog

## Purpose

This document turns the "John should earn money through Telegram" idea into a
grounded backlog that fits current ALS architecture.

The goal is not to build an `obolos.tech` one-off.

The goal is to build a reusable marketplace earning engine where:
- John is the Telegram-facing operator persona
- ALS is the execution/control-plane/runtime substrate
- `obolos.tech` is the first connector
- more platforms can be added later without rewriting the whole system

## Product Goal

John should be able to:
- browse available jobs from supported platforms
- inspect job detail
- decide whether ALS can realistically complete the work
- prepare or submit bids
- execute delivery work through ALS build/review flows
- hand off results back to the marketplace
- track revenue, cost, approvals, and payouts safely

The system must be designed for future multi-platform expansion.

## Current Grounded State

ALS already has useful building blocks:
- external gateway/provider model
- concrete `obolos.tech` provider catalog
- buyer-side marketplace API access
- seller publish route
- wallet balance route
- wallet top-up route
- x402-aware settlement workflow
- approval queue
- Telegram operator surface
- build/review product job system
- persisted jobs, projects, workflows, and cost ledger

ALS does not yet have an end-to-end "marketplace worker" product flow.

Missing product slices today:
- normalized marketplace job model
- job discovery surface in Telegram/API
- qualification/risk scoring for external jobs
- bid draft / bid submit workflow
- negotiation / terms tracking
- job-to-build/review execution mapping
- delivery contract per marketplace job
- earnings and payout policy layer
- multi-platform connector abstraction above provider-specific routes

## Product Principles

### 1. Multi-platform first

Do not hardcode business logic to `obolos.tech`.

`obolos.tech` should be the first adapter, not the whole product.

### 2. Deterministic-first

Job discovery, qualification gates, cost limits, payout rules, and approval
rules must be explicit and auditable.

### 3. Fail-closed money movement

Never start with unrestricted payout automation.

Revenue/payout automation must arrive only after:
- clear approval policy
- destination policy
- auditability
- dry-run / observe-only support

### 4. Human-supervised before autonomous

First useful version:
- browse jobs
- qualify jobs
- prepare bid draft
- submit only with approval

Autonomous bidding and payout come later, if ever.

### 5. Reuse ALS bounded contexts

Do not rebuild:
- job execution
- approvals
- gateway
- delivery records
- projects
- recurring workflows
- cost ledger

Instead, compose them behind a marketplace domain.

## Target Domain Model

## PlatformConnector

Connector contract for each marketplace:
- `list_jobs(filters) -> list[MarketplaceJob]`
- `get_job(job_id) -> MarketplaceJobDetail`
- `qualify(job_id, runtime_context) -> QualificationResult`
- `create_bid_draft(job_id, strategy) -> BidDraft`
- `submit_bid(job_id, bid_payload) -> SubmissionReceipt`
- `get_engagement_status(remote_id) -> EngagementStatus`
- `submit_delivery(remote_id, delivery_payload) -> DeliveryReceipt`
- `list_earnings() -> list[EarningRecord]`

Optional later:
- `withdraw()`
- `sync_reputation()`

## MarketplaceJob

Minimum normalized fields:
- `platform`
- `job_id`
- `title`
- `summary`
- `full_description`
- `budget`
- `currency`
- `deadline`
- `skills_required`
- `artifacts_required`
- `delivery_mode`
- `job_url`
- `raw_payload`

Derived fields:
- `normalized_budget_usd`
- `risk_flags`
- `complexity_band`

## QualificationResult

Minimum fields:
- `job_id`
- `platform`
- `can_execute`
- `confidence`
- `reasons_yes`
- `reasons_no`
- `required_capabilities`
- `missing_capabilities`
- `estimated_effort`
- `estimated_cost_usd`
- `approval_required`
- `recommended_action`

## BidDraft

Minimum fields:
- `job_id`
- `platform`
- `cover_text`
- `delivery_scope`
- `estimated_timeline`
- `price_quote`
- `assumptions`
- `risks`
- `requires_human_edit`

## EngagementRecord

Tracks participation in an external job:
- `engagement_id`
- `platform`
- `remote_job_id`
- `local_project_id`
- `status`
- `bid_id`
- `accepted_at`
- `delivery_job_ids`
- `delivery_record_ids`
- `revenue_expected`
- `revenue_received`
- `notes`

## PayoutPolicy

Separate money policy from marketplace connector logic.

Minimum fields:
- `policy_id`
- `enabled`
- `mode`
- `allowed_destinations`
- `require_approval`
- `min_confirmations`
- `minimum_payout_amount`
- `fee_reserve_policy`
- `dry_run`

## Telegram Surface

John should remain the operator-facing interface.

Recommended command family:
- `/market connectors`
- `/market jobs --platform obolos`
- `/market job <platform> <job_id>`
- `/market qualify <platform> <job_id>`
- `/market bid-draft <platform> <job_id>`
- `/market bid-submit <platform> <job_id>`
- `/market engagements`
- `/market earnings`
- `/market payouts`

Do not start with rich conversational magic only.

Make the first slice deterministic and commandable.

## Backlog Structure

## Epic M0: Marketplace Domain Foundation

### Goal

Create the generic earning engine domain without committing to one provider.

### Stories

#### M0-S1 Normalized marketplace models
- add `MarketplaceJob`
- add `MarketplaceJobDetail`
- add `QualificationResult`
- add `BidDraft`
- add `EngagementRecord`
- add `PayoutPolicy`

#### M0-S2 Connector abstraction
- define base connector interface
- define registry/discovery for connectors
- allow lookup by platform id

#### M0-S3 Persistence
- persist jobs cache or snapshots
- persist engagement records
- persist bid drafts
- persist payout policies

#### M0-S4 Reporting hooks
- expose marketplace state into operator report surfaces
- surface counts and recent engagements

### Definition of Done
- normalized models exist
- connector interface exists
- persistence exists
- tests prove two connectors can coexist without shared hardcoded logic

## Epic M1: Obolos Read-Only Connector

### Goal

Use `obolos.tech` as the first connector for browsing and inspecting work.

### Stories

#### M1-S1 Job discovery adapter
- map Obolos marketplace listing/catalog/job APIs to normalized jobs
- support pagination/filtering where available

#### M1-S2 Job detail adapter
- fetch one job / listing / API offer detail
- normalize fields into `MarketplaceJobDetail`

#### M1-S3 Telegram read-only commands
- `/market jobs --platform obolos`
- `/market job obolos <id>`

#### M1-S4 Grounded capability/status truth
- ALS must truthfully say that Obolos browsing exists
- no hallucinated bid/delivery if not implemented yet

### Definition of Done
- John can browse Obolos opportunities via Telegram
- data is normalized
- no LLM required for listing/detail retrieval

## Epic M2: Qualification And Suitability Engine

### Goal

John should know whether ALS can realistically take a job.

### Stories

#### M2-S1 Qualification rules
- repository/code task detection
- review vs build vs research vs unsupported task bands
- capability gap mapping

#### M2-S2 Cost/effort/risk model
- estimate effort band
- estimate cost band
- budget/risk gating
- approval requirement flag

#### M2-S3 Telegram command
- `/market qualify obolos <id>`

#### M2-S4 Project linkage
- create or update project record for promising opportunities
- link future jobs back to the external engagement

### Definition of Done
- John can give grounded yes/no/maybe qualification
- unsupported jobs are rejected honestly
- promising jobs can be tracked as projects

## Epic M3: Bid Drafting And Submission

### Goal

Move from "I can inspect the job" to "I can participate".

### Stories

#### M3-S1 Bid draft generation
- deterministic bid template
- optional cheap LLM polish on top
- preserve structured assumptions and risks

#### M3-S2 Bid submit connector method
- platform-specific submit action
- approval-required by default
- persisted submission receipts

#### M3-S3 Telegram commands
- `/market bid-draft obolos <id>`
- `/market bid-submit obolos <id>`

#### M3-S4 Negotiation state
- track status changes
- track notes / clarifications / terms

### Definition of Done
- John can draft bids
- John can submit bids with approval
- submission receipts are persisted

## Epic M4: Engagement Execution

### Goal

Accepted marketplace work must map cleanly to ALS product jobs.

### Stories

#### M4-S1 Engagement -> project mapping
- accepted job creates or upgrades a project
- project stores remote platform metadata

#### M4-S2 Engagement -> build/review execution mapping
- create build/review intake from engagement
- persist relationship between remote engagement and local product jobs

#### M4-S3 Delivery package mapping
- package build/review outputs into platform delivery payloads
- preserve evidence export compatibility

#### M4-S4 Telegram commands
- `/market engagements`
- `/market deliver <platform> <engagement_id>`

### Definition of Done
- accepted work turns into local execution cleanly
- delivery artifacts remain traceable

## Epic M5: Revenue, Settlement, And Payout Policy

### Goal

Track money safely without opening unsafe transfer behavior.

### Stories

#### M5-S1 Earnings ledger
- record expected revenue
- record received revenue
- compare cost vs revenue

#### M5-S2 Settlement linkage
- link marketplace payment events to engagement records
- reuse x402/settlement machinery where relevant

#### M5-S3 Whitelist-only payout policy
- define payout policy model
- allow only preconfigured destination wallets
- deny all non-whitelisted payout destinations
- approval required by default

#### M5-S4 Observe-only payment detection
- detect inbound balances or payment state
- do not auto-withdraw yet

### Definition of Done
- revenue is visible
- payouts are impossible to arbitrary addresses
- payout logic is fail-closed

## Epic M6: Multi-Platform Expansion

### Goal

Prove the architecture is not Obolos-only.

### Stories

#### M6-S1 Second connector contract validation
- create connector test harness
- add stub or second lightweight connector

#### M6-S2 Platform-neutral ranking
- compare opportunities across multiple platforms

#### M6-S3 Telegram filtering
- `/market jobs --platform all`
- filter by capability, risk, budget, profitability

### Definition of Done
- architecture supports more than one marketplace cleanly

## Recommended Delivery Order

### Phase 1
- M0 domain foundation
- M1 read-only Obolos connector

### Phase 2
- M2 qualification engine
- M3 bid drafting

### Phase 3
- M3 supervised submit
- M4 engagement execution mapping

### Phase 4
- M5 earnings + whitelist payout policy

### Phase 5
- M6 multi-platform expansion

## What Not To Do First

Do not start with:
- full autonomous bidding
- auto-payout to arbitrary wallets
- hardcoded Obolos-only control flow
- pure chat magic without command surfaces
- broad marketplace scraping without normalized models

## First Practical MVP

The smallest valuable slice is:
- generic marketplace models
- one Obolos connector
- `/market jobs`
- `/market job`
- `/market qualify`
- `/market bid-draft`

This makes John useful before touching money movement.

## Success Criteria

John is meaningfully useful when he can:
- find real external opportunities
- tell which ones ALS can safely execute
- prepare a usable bid
- track the opportunity as a project
- later convert accepted work into ALS build/review execution

The system is meaningfully safe when:
- unsupported work is declined honestly
- bids are approval-gated until proven safe
- payout destinations are restricted by policy
- no arbitrary transfer path exists
