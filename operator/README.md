# Operator Delivery Console

Mock-driven operator console skeleton for review workflow.

## Status: Skeleton / Mock-driven

This is a **skeleton** — typed views over mock data. It is NOT a production
application and has no live backend, no auth, and no deployment.

What exists:
- TypeScript DTOs mapped from Python reviewer domain
- Client-safe model surface (fields excluded from external view)
- Mock-driven view modules: job list, job detail, delivery preview, approval queue
- Mock-driven operator reporting/inbox view over shared control-plane jobs
- Typecheck in CI (catches TS regressions)

## Structure

```
operator/
  package.json          # TS toolchain
  tsconfig.json         # Strict TS config
  src/
    models/
      review.ts         # Full DTOs: ReviewJobSummary, DeliveryBundleSummary, etc.
      client-safe.ts    # Client-safe subset (no requester, source, evidence, etc.)
    mock/
      data.ts           # Mock data derived from Python reviewer output
    views/
      job-list.ts       # List/filter review jobs, stats, client-safe projection
      job-detail.ts     # Single job with findings breakdown
      delivery-preview.ts  # Bundle preview, client-safe projection, readiness check
      approval-queue.ts # Pending approvals, expiry check, queue stats
      reporting.ts      # Operator inbox/report over shared control-plane jobs
    index.ts            # Barrel exports
  MAPPING.md            # Python → TypeScript field mapping
  README.md
```

## Client-Safe Surface

Fields excluded from client-safe models (see `src/models/client-safe.ts`):

| Field | Reason |
|-------|--------|
| `requester` | Internal identity |
| `source` | Internal channel info (telegram/api) |
| `execution_mode` | Infrastructure detail |
| `started_at` | Internal performance metric |
| `error` | May leak paths/stack traces |
| `evidence` | May contain sensitive code snippets |
| `proposed_by` | Internal agent identity |
| `context` | Internal approval metadata |

## Python → TypeScript Mapping

See [MAPPING.md](MAPPING.md) for detailed field mapping.

## Development

```bash
cd operator
npm install
npm run typecheck   # strict typecheck
```

## What this does NOT do

- No live backend connection
- No UI framework / rendering
- No authentication
- No real-time updates
- No deployment configuration
