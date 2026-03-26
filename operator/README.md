# Operator Delivery Console

Contract-first TypeScript foundation for operator-facing review workflow surface.

## Status: Foundation / Mock-driven

This is NOT a production application yet. It is:
- typed DTO contracts derived from Python reviewer models
- mock data for development
- foundation for future live backend integration

## Structure

```
operator/
  src/
    models/
      review.ts       # DTOs: ReviewJobSummary, DeliveryBundleSummary, etc.
    mock/
      data.ts         # Mock data derived from Python reviewer output
  MAPPING.md          # Python → TypeScript model mapping
  README.md
```

## Python → TypeScript Mapping

See [MAPPING.md](MAPPING.md) for detailed field mapping.

## What this enables

- Operator can view pending review jobs
- Operator can preview delivery bundles
- Operator can see approval queue
- Operator can inspect review findings

## What this does NOT do yet

- No live backend connection
- No authentication
- No real-time updates
- No deployment configuration
