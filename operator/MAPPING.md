# Python → TypeScript Model Mapping

## ReviewJob → ReviewJobSummary

| Python field | Python type | TS field | TS type |
|-------------|-------------|----------|---------|
| id | str | id | string |
| job_type | ReviewJobType (enum) | job_type | ReviewJobType (union) |
| source | str | source | string |
| requester | str | requester | string |
| status | ReviewJobStatus (enum) | status | ReviewJobStatus (union) |
| execution_mode | ExecutionMode (enum) | execution_mode | ExecutionMode (union) |
| created_at | str (ISO 8601) | created_at | string |
| started_at | str | started_at | string |
| completed_at | str | completed_at | string |
| report.verdict | str | verdict | string |
| report.verdict_confidence | Confidence (enum) | verdict_confidence | Confidence (union) |
| report.finding_counts | dict[str, int] | finding_counts | Record<Severity, number> |
| error | str | error | string |

## ReviewFinding → ReviewFindingSummary

| Python field | TS field | Notes |
|-------------|----------|-------|
| id | id | |
| severity | severity | Enum → union type |
| title | title | |
| description | description | |
| impact | impact | |
| file_path | file_path | |
| line_start | line_start | |
| line_end | line_end | |
| category | category | |
| evidence | evidence | Redacted in client-safe mode |
| recommendation | recommendation | |
| confidence | confidence | |
| tags | tags | |

## DeliveryBundle → DeliveryBundleSummary

| Python field | TS field | Notes |
|-------------|----------|-------|
| job_id | job_id | |
| job_type | job_type | |
| status | status | |
| requester | requester | |
| execution_mode | execution_mode | Stripped in client-safe |
| verdict | verdict | |
| verdict_confidence | verdict_confidence | |
| finding_counts | finding_counts | |
| markdown_report | markdown_report | Redacted in client-safe |
| findings_only | findings_only | Array of ReviewFindingSummary |
| artifact_count | artifact_count | |
| delivery_ready | delivery_ready | |
| export_mode | export_mode | "internal" or "client_safe" |

## ApprovalRequest → ApprovalRequestSummary

| Python field | TS field | Notes |
|-------------|----------|-------|
| id | id | |
| category | category | ApprovalCategory enum → string |
| description | description | |
| risk_level | risk_level | |
| reason | reason | |
| proposed_by | proposed_by | |
| status | status | ApprovalStatus enum → union |
| created_at | created_at | float (epoch) → number |
| ttl_seconds | ttl_seconds | |
| context | context | dict → Record<string, unknown> |
