/**
 * Review Jobs List View
 *
 * Mock-driven skeleton for listing review jobs.
 * Provides filtering, sorting, and client-safe projection.
 */

import type {
  ReviewJobStatus,
  ReviewJobSummary,
  ReviewJobType,
  Severity,
} from "../models/review";
import type { ClientSafeJobSummary } from "../models/client-safe";
import { mockJobs } from "../mock/data";

// ─────────────────────────────────────────────
// Filters
// ─────────────────────────────────────────────

export interface JobListFilter {
  status?: ReviewJobStatus;
  job_type?: ReviewJobType;
  min_severity?: Severity;
}

const SEVERITY_ORDER: Record<Severity, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
};

function hasMinSeverity(
  counts: Record<Severity, number>,
  min: Severity
): boolean {
  const threshold = SEVERITY_ORDER[min];
  return Object.entries(counts).some(
    ([sev, count]) => count > 0 && SEVERITY_ORDER[sev as Severity] >= threshold
  );
}

// ─────────────────────────────────────────────
// Data access (mock-driven)
// ─────────────────────────────────────────────

export function getJobs(filter?: JobListFilter): ReviewJobSummary[] {
  let jobs = [...mockJobs];

  if (filter?.status) {
    jobs = jobs.filter((j) => j.status === filter.status);
  }
  if (filter?.job_type) {
    jobs = jobs.filter((j) => j.job_type === filter.job_type);
  }
  if (filter?.min_severity) {
    jobs = jobs.filter((j) =>
      hasMinSeverity(j.finding_counts, filter.min_severity!)
    );
  }

  return jobs;
}

export function getCompletedJobs(): ReviewJobSummary[] {
  return getJobs({ status: "completed" });
}

export function getFailedJobs(): ReviewJobSummary[] {
  return getJobs({ status: "failed" });
}

// ─────────────────────────────────────────────
// Client-safe projection
// ─────────────────────────────────────────────

export function toClientSafe(job: ReviewJobSummary): ClientSafeJobSummary {
  return {
    id: job.id,
    job_type: job.job_type,
    status: job.status,
    created_at: job.created_at,
    completed_at: job.completed_at,
    verdict: job.verdict,
    verdict_confidence: job.verdict_confidence,
    finding_counts: job.finding_counts,
    total_findings: job.total_findings,
  };
}

export function getClientSafeJobs(
  filter?: JobListFilter
): ClientSafeJobSummary[] {
  return getJobs(filter).map(toClientSafe);
}

// ─────────────────────────────────────────────
// Summary stats
// ─────────────────────────────────────────────

export interface JobListStats {
  total: number;
  by_status: Partial<Record<ReviewJobStatus, number>>;
  by_type: Partial<Record<ReviewJobType, number>>;
  with_critical_findings: number;
}

export function getJobListStats(): JobListStats {
  const jobs = getJobs();
  const by_status: Partial<Record<ReviewJobStatus, number>> = {};
  const by_type: Partial<Record<ReviewJobType, number>> = {};
  let with_critical = 0;

  for (const job of jobs) {
    by_status[job.status] = (by_status[job.status] ?? 0) + 1;
    by_type[job.job_type] = (by_type[job.job_type] ?? 0) + 1;
    if (job.finding_counts.critical > 0) {
      with_critical++;
    }
  }

  return {
    total: jobs.length,
    by_status,
    by_type,
    with_critical_findings: with_critical,
  };
}
