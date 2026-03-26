/**
 * Review Job Detail View
 *
 * Mock-driven skeleton for viewing a single review job.
 * Includes findings breakdown and verdict display.
 */

import type {
  ReviewFindingSummary,
  ReviewJobSummary,
  Severity,
} from "../models/review";
import type { ClientSafeJobSummary } from "../models/client-safe";
import { mockJobs } from "../mock/data";
import { mockBundle } from "../mock/data";
import { toClientSafe } from "./job-list";

// ─────────────────────────────────────────────
// Lookup
// ─────────────────────────────────────────────

export function getJobById(id: string): ReviewJobSummary | undefined {
  return mockJobs.find((j) => j.id === id);
}

export function getClientSafeJobById(
  id: string
): ClientSafeJobSummary | undefined {
  const job = getJobById(id);
  return job ? toClientSafe(job) : undefined;
}

// ─────────────────────────────────────────────
// Findings for a job
// ─────────────────────────────────────────────

export function getFindingsForJob(jobId: string): ReviewFindingSummary[] {
  if (mockBundle.job_id === jobId) {
    return mockBundle.findings_only;
  }
  return [];
}

export function getFindingsBySeverity(
  jobId: string
): Partial<Record<Severity, ReviewFindingSummary[]>> {
  const findings = getFindingsForJob(jobId);
  const grouped: Partial<Record<Severity, ReviewFindingSummary[]>> = {};

  for (const f of findings) {
    if (!grouped[f.severity]) {
      grouped[f.severity] = [];
    }
    grouped[f.severity]!.push(f);
  }

  return grouped;
}

// ─────────────────────────────────────────────
// Job detail view model
// ─────────────────────────────────────────────

export interface JobDetailView {
  job: ReviewJobSummary;
  findings: ReviewFindingSummary[];
  findings_by_severity: Partial<Record<Severity, ReviewFindingSummary[]>>;
  has_critical: boolean;
  duration_ms: number | null;
}

export function getJobDetailView(id: string): JobDetailView | undefined {
  const job = getJobById(id);
  if (!job) return undefined;

  const findings = getFindingsForJob(id);
  const findings_by_severity = getFindingsBySeverity(id);

  let duration_ms: number | null = null;
  if (job.started_at && job.completed_at) {
    const start = new Date(job.started_at).getTime();
    const end = new Date(job.completed_at).getTime();
    duration_ms = end - start;
  }

  return {
    job,
    findings,
    findings_by_severity,
    has_critical: job.finding_counts.critical > 0,
    duration_ms,
  };
}
