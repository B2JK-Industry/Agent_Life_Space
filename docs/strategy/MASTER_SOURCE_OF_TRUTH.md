# Master Source Of Truth

## Purpose

This document is the main product and architecture brief for the future of
Agent Life Space.

It is not:
- a README
- a marketing page
- a temporary brainstorm
- a backlog

It is:
- the source of truth for strategic direction
- the source from which backlog is derived
- the reference for Claude Code tasks
- the benchmark for deciding whether work moves the project forward

If backlog, code, or docs drift away from this document, that drift should be
called out explicitly.

## Core Vision

Agent Life Space should become a sovereign engineering operator.

That means a system which can:
- perform deep technical review
- perform real engineering work
- operate repeatable valuable workflows
- gradually participate in revenue generation
- evolve toward enterprise-ready architecture without losing discipline

The system should not become:
- a generic chatbot
- an unbounded autonomous agent
- a multi-tenant SaaS in the first phase
- a bundle of disconnected AI features
- an autonomous financial robot

## Product Arc

The product should evolve through three tightly connected modes.

### 1. Reviewer

The system can:
- review repos
- review PRs and diffs
- review release readiness
- review security and architecture risk
- produce structured findings, severity, evidence, and recommended changes

### 2. Builder

The system can:
- implement changes in isolated workspaces
- execute backend, frontend, integration, and devops tasks
- test and iterate
- work against acceptance criteria
- produce deliverable engineering artifacts

### 3. Operator

The system can:
- qualify incoming work
- plan and route work across capabilities
- coordinate execution and verification
- prepare delivery
- track cost and margin
- support monetizable workflows

These are not three separate products.
They are three modes of one controlled system.

## Product Identity

The project should remain:
- self-hosted
- security-first
- deterministic-first where possible
- auditable
- explainable
- policy-driven
- cost-aware
- microservice-ready but not prematurely microservice-heavy

The short identity statement is:

`Agent Life Space is a sovereign engineering operator with enterprise-ready
foundations.`

## Architectural Doctrine

The system should be built as a modular monolith with hard boundaries and
microservice-ready contracts.

That means:
- bounded contexts are designed now
- service extraction happens only when there is real pressure
- control plane and execution plane are conceptually separate from the start
- state machines, artifacts, approvals, and policies are explicit
- LLMs do reasoning, drafting, classification, and synthesis
- deterministic layers do policy, auth, approvals, budget, state transitions,
  and security boundaries

## Target System Layers

### Control Plane

The control plane owns:
- intake
- planning
- routing
- job lifecycle
- status
- approvals
- policy enforcement
- budget control
- audit
- observability

### Execution Plane

The execution plane owns:
- workspace execution
- capability workers
- backend work
- frontend work
- integration work
- devops work
- implementation artifacts

### Verification Plane

The verification plane owns:
- test execution
- linting
- type checking
- review pass
- security checks
- acceptance validation
- release readiness validation

### Delivery Plane

The delivery plane owns:
- report generation
- artifact packaging
- patch and diff packaging
- release bundle generation
- client-safe output
- handoff workflows

### Monetization Plane

The monetization plane owns:
- usage tracking
- model cost accounting
- budget caps
- margin visibility
- pricing support
- billing event foundations
- delivery approval before external send

### External Capability Gateway

The external gateway owns:
- external API capability access
- auth
- retries
- rate limits
- timeouts
- cost tracking
- audit
- policy gating

`obolos.tech` should live behind this gateway model, not as a hardcoded escape
hatch.

## Canonical Domain Objects

These domain objects should exist explicitly in the system:
- Job
- JobPlan
- Capability
- WorkerAssignment
- Workspace
- Artifact
- ReviewFinding
- AcceptanceCriteria
- VerificationResult
- ApprovalRequest
- BudgetPolicy
- DeliveryPackage
- ClientProfile
- WorkSignal
- CostLedger
- ExecutionTrace

## Job Model

All meaningful work should be represented as a Job.

Each Job should carry:
- id
- type
- source
- requester
- owner
- scope
- input artifacts
- acceptance criteria
- risk profile
- budget profile
- assigned capabilities
- state
- execution history
- outputs
- delivery state
- monetization metadata

Target job types:
- repo_audit
- pr_review
- release_review
- implementation
- integration
- devops_task
- testing_task
- client_delivery
- recurring_operator_job

## Capability Model

Each system capability should declare:
- name
- purpose
- allowed contexts
- required inputs
- produced outputs
- risk level
- cost profile
- execution environment
- approval rules
- fallback behavior
- verification requirements

Example capabilities:
- analyze_repository
- analyze_diff
- suggest_patch
- implement_backend_change
- implement_frontend_change
- run_tests
- run_lint
- validate_acceptance_criteria
- export_report
- build_release_package
- call_external_api
- deploy_to_target

## Artifact Model

The system should be artifact-first.

Important outputs should not live only inside chat text.

Core artifact types:
- review_report
- patch_set
- diff_bundle
- test_report
- lint_report
- typecheck_report
- security_report
- acceptance_report
- release_report
- deployment_manifest
- delivery_bundle
- billing_summary

Each artifact should be:
- identifiable
- timestamped or versioned
- traceable to a job
- exportable
- auditable

## Acceptance Criteria Model

Acceptance criteria should be explicit objects, not just prompt text.

They should support:
- functional requirements
- quality requirements
- security requirements
- performance requirements
- deployment requirements
- delivery requirements
- business constraints

Every major job should end with:
- a verdict on acceptance
- a record of what was validated
- an explicit list of unmet criteria if any remain

## Reviewer Mode Requirements

Reviewer mode is the nearest path to client value.

It must support:
- repo audit
- PR review
- diff review
- release readiness review
- security and architecture review
- structured severity
- evidence and file refs
- recommended fixes
- explicit assumptions and open questions
- explicit low-confidence handling

It must include:
- a verification pass
- client-safe output mode
- export to Markdown and JSON
- delivery approval before sending externally
- cost control per review job

## Builder Mode Requirements

Builder mode must support real implementation work.

It must support:
- isolated workspaces
- code changes
- backend tasks
- frontend tasks
- integration tasks
- devops tasks under policy control
- test-driven iteration
- review-before-delivery
- acceptance-based completion

It must be:
- recoverable
- auditable
- budget-aware
- approval-aware

## Operator Mode Requirements

Operator mode must support coordinated value delivery.

It must support:
- intake qualification
- job planning
- capability routing
- execution orchestration
- verification orchestration
- delivery preparation
- cost tracking
- margin awareness
- monetizable recurring workflows

## Work Discovery

Long-term, the system may discover work from:
- inbound requests
- scheduled reviews
- failing pipelines
- repo events
- client inbox or API
- monitoring signals

But this should be phased:
1. human provides work
2. system qualifies and plans work
3. system detects work from defined sources
4. system becomes revenue-capable under guardrails

## Multi-LLM Strategy

The system should support multiple LLMs, but with explicit roles.

Recommended pattern:
- cheap model for triage and routing
- stronger model for deep reasoning
- verifier model or second-pass logic for output control
- deterministic final gating for policy and delivery

The system should always know:
- which model was used
- why it was used
- how much it cost
- whether escalation happened
- whether escalation was skipped due to budget or policy

## Security And Governance

This is non-negotiable.

The system must remain:
- deny-by-default
- least-privilege
- approval-gated for risky actions
- audit-first
- secret-safe
- client-safe

There must be:
- a central policy engine
- approval handling
- budget policy
- operator controls
- action audit trails
- restricted channel rules
- execution boundaries
- external API governance

LLMs must never:
- rewrite security rules
- rewrite approval rules
- decide policy boundaries
- silently bypass constraints

## Enterprise-Ready Foundations

The goal is not immediate enterprise SaaS.
The goal is enterprise-ready foundations.

Those foundations include:
- bounded contexts
- contract-first design
- explicit state machines
- recoverable jobs
- idempotent operations where needed
- observability
- redaction
- audit export
- environment isolation
- versioned artifacts
- service extraction readiness
- compliance-friendly data handling

## Client Satisfaction Definition

The client should feel satisfied when:
- output arrives on time
- output is useful
- findings are credible
- implementation meets acceptance criteria
- risk is explained honestly
- the system is transparent about scope and uncertainty
- delivery is professional and reproducible

Delivering a project therefore means delivering:
- artifacts
- findings or implementation outputs
- verification evidence
- acceptance verdict
- handoff package

## Monetization Direction

The first realistic monetization paths are:
- repo audit
- PR review
- release readiness review
- continuous engineering operator for a narrow workflow

Monetization should not start with autonomous money movement.
It should start with revenue-capable technical delivery.

The system should understand:
- job cost
- client usage
- budget cap
- price support signals
- margin visibility
- delivery approval

## obolos.tech Direction

`obolos.tech` should be treated as an external capability fabric.

It should sit behind a gateway with:
- capability catalog
- auth
- request and response schema
- timeout policy
- retry policy
- rate limits
- cost tracking
- audit
- policy gating

The system should be able to decide:
- when to execute internally
- when to call external capability
- when to deny
- when to escalate to a human

## What We Must Avoid

Avoid these anti-patterns:
- one giant smart agent with implicit behavior
- early microservice explosion
- LLM-driven security decisions
- feature sprawl without domain objects or artifacts
- monetization before delivery reliability
- platform ambition before strong single-operator value
- pretending that partial skeletons are production-complete

## Delivery Phases

### Phase 0: Foundation Hardening
- runtime stability
- CI stability
- truthful docs
- strong job, state, artifact, policy, approval foundations

### Phase 1: Productized Reviewer
- first-class review jobs
- repo audit
- PR review
- release readiness review
- verification pass
- exportable reports
- delivery approval

### Phase 2: Productized Builder
- implementation jobs
- capability-specific work execution
- workspace discipline
- test and review loop
- acceptance closure

### Phase 3: Operatorization
- recurring workflows
- intake qualification
- orchestration across review, build, test, and delivery
- usage and margin control

### Phase 4: Enterprise Hardening
- stronger contracts
- stronger observability
- selective service extraction
- environment profiles
- persistent approvals
- operator surfaces

## Rules For Backlog Generation

Every backlog item generated from this document should:
- map to a specific section of this document
- have a clear product or architecture purpose
- belong to a phase
- include acceptance criteria
- include dependencies
- include risk notes
- include test strategy
- include a definition of done

Recommended hierarchy:
- Theme
- Epic
- Story
- Task

## Closing Principle

Agent Life Space should not grow into a bigger chatbot.

It should become a sovereign work engine that can:
- think
- act
- verify
- deliver
- explain itself
- control risk and cost
- and evolve toward enterprise architecture without losing discipline
