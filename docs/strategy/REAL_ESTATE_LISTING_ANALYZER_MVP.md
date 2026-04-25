# Real Estate Listing Analyzer MVP

## Purpose

This document turns the loose `sreality.cz` idea into a grounded MVP brief that
fits the current ALS architecture and current operational constraints.

The goal is not to build a one-off scraper.

The goal is to build a reusable listing-analysis foundation that can:
- run one-off analyses with per-analysis filters
- compare listings by normalized fields
- score and rank results
- support later Telegram automation and recurring alerts

## Current Constraints

These are real blockers right now:
- no GitHub token has been provided for private repo creation
- no Telegram bot token has been provided
- no deploy target or runtime host has been committed for this project

Because of that, the realistic "do what we can now" scope is:
- product brief
- architecture brief
- task prompt for implementation
- local MVP foundation work

## Product Shape

This tool should support separate analyses, each with its own configuration.

It should not rely on one global budget or one global location.

Each analysis should be able to define:
- source
- listing type
- sale vs rent
- location
- min price / max price
- min area / max area
- disposition
- building state
- optional features like balcony / parking / cellar

Example:
- Analysis A: `Brno`, `3+kk`, `max 5.5M CZK`
- Analysis B: `Praha 9`, `2+1`, `2.8M–4.2M CZK`

These must coexist without overwriting each other.

## MVP Scope

### Included

- source adapter abstraction
- first adapter for `sreality.cz`
- normalized listing model
- per-analysis filter model
- local persistence in SQLite
- one-off analysis run
- scoring helpers for price-per-m2 and simple ranking
- deterministic result formatting
- fixture-based tests

### Not Included In MVP

- automatic private GitHub repo creation
- production deploy
- Telegram live bot token integration
- full browser automation baseline
- anti-bot / CAPTCHA bypass
- historical price intelligence beyond what the scraped page exposes
- multi-user SaaS behavior

## Architecture

### Recommended Project Structure

If implemented as a standalone project, start with:

```text
real-estate-listing-analyzer/
  app/
    sources/
      base.py
      sreality.py
    domain/
      models.py
      filters.py
      scoring.py
    storage/
      sqlite.py
    services/
      analyze.py
      compare.py
      alerts.py
    interfaces/
      cli.py
      telegram.py
  tests/
    fixtures/
    test_sources_sreality.py
    test_filters.py
    test_scoring.py
    test_analysis_service.py
```

### Source Adapter Boundary

The source layer should expose a stable contract:

- `search(filters) -> list[Listing]`
- `fetch_detail(url) -> ListingDetail | None`

This keeps `sreality.cz` as one adapter instead of baking site-specific parsing
into the whole app.

## Canonical Domain Model

### Listing

Each normalized listing should contain at least:
- `source`
- `external_id`
- `title`
- `url`
- `price_czk`
- `area_m2`
- `location_text`
- `disposition`
- `property_type`
- `offer_type`
- `state`
- `floor`
- `features`
- `summary`
- `raw_payload`

Derived fields:
- `price_per_m2`
- `fingerprint`

### AnalysisProfile

Each analysis must be persisted independently.

Fields:
- `analysis_id`
- `name`
- `source`
- `property_type`
- `offer_type`
- `location_query`
- `price_min_czk`
- `price_max_czk`
- `area_min_m2`
- `area_max_m2`
- `dispositions`
- `state_filters`
- `feature_filters`
- `enabled`
- `created_at`
- `updated_at`

### AnalysisRun

Each execution should record:
- `run_id`
- `analysis_id`
- `started_at`
- `finished_at`
- `listing_count`
- `new_listing_count`
- `status`
- `error`

## Implementation Order

### Phase 1: Data Foundation

- build normalized models
- build filter model
- build SQLite persistence
- implement `sreality.cz` adapter
- add fixture-based parser tests

### Phase 2: Analysis

- filtering
- ranking
- price-per-m2
- median comparison by local result set
- result formatter

### Phase 3: Delivery Surfaces

- CLI entrypoint
- Telegram command contract
- alert/report formatting

### Phase 4: Automation

- recurring run scheduler
- watchlists
- new listing detection
- notification delivery

## Guidance For `sreality.cz`

Start with the most conservative approach:

1. try plain HTTP + HTML parsing
2. prefer deterministic extraction over LLM parsing
3. use Playwright only as a fallback when static fetch is insufficient

Do not make Playwright mandatory in the first slice unless the site demands it.

## Testing Rules

Tests should not depend on live `sreality.cz` availability.

Use:
- saved HTML fixtures
- saved JSON fixtures if the site exposes JSON payloads
- deterministic parser assertions

Minimum test coverage for MVP:
- parser extracts required fields
- filters behave correctly per analysis
- scoring is deterministic
- persistence supports multiple analyses at once
- one analysis run does not overwrite another

## Definition Of Done For MVP

The MVP is done when:
- two different analyses can be stored and run independently
- `sreality.cz` results are normalized into a shared listing model
- one-off analysis returns ranked results
- results can be formatted for later Telegram delivery
- tests pass without live network dependency

## Honest Current Next Step

The best next implementation step is not repo creation or deploy.

It is:
- scaffold the standalone project locally
- implement the normalized model
- implement one source adapter
- add fixture-driven tests

That gives us a real engineering base before secrets and deployment enter the
picture.
