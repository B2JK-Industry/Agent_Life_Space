/**
 * Delivery Bundle Preview View
 *
 * Mock-driven skeleton for previewing delivery bundles.
 * Supports both internal (operator) and client-safe views.
 */

import type {
  DeliveryBundleSummary,
  ReviewFindingSummary,
} from "../models/review";
import type {
  ClientSafeDeliveryBundle,
  ClientSafeFinding,
} from "../models/client-safe";
import { mockBundle } from "../mock/data";

// ─────────────────────────────────────────────
// Bundle lookup (mock-driven)
// ─────────────────────────────────────────────

export function getBundleForJob(
  jobId: string
): DeliveryBundleSummary | undefined {
  if (mockBundle.job_id === jobId) {
    return mockBundle;
  }
  return undefined;
}

// ─────────────────────────────────────────────
// Client-safe projection
// ─────────────────────────────────────────────

function toClientSafeFinding(f: ReviewFindingSummary): ClientSafeFinding {
  return {
    id: f.id,
    severity: f.severity,
    title: f.title,
    description: f.description,
    impact: f.impact,
    file_path: f.file_path,
    line_start: f.line_start,
    line_end: f.line_end,
    category: f.category,
    recommendation: f.recommendation,
    confidence: f.confidence,
    tags: f.tags,
  };
}

export function toClientSafeBundle(
  bundle: DeliveryBundleSummary
): ClientSafeDeliveryBundle {
  return {
    job_id: bundle.job_id,
    job_type: bundle.job_type,
    status: bundle.status,
    verdict: bundle.verdict,
    verdict_confidence: bundle.verdict_confidence,
    finding_counts: bundle.finding_counts,
    markdown_report: bundle.markdown_report,
    findings_only: bundle.findings_only.map(toClientSafeFinding),
    artifact_count: bundle.artifact_count,
    delivery_ready: bundle.delivery_ready,
    export_mode: "client_safe",
    completed_at: bundle.completed_at,
  };
}

export function getClientSafeBundleForJob(
  jobId: string
): ClientSafeDeliveryBundle | undefined {
  const bundle = getBundleForJob(jobId);
  return bundle ? toClientSafeBundle(bundle) : undefined;
}

// ─────────────────────────────────────────────
// Delivery readiness check
// ─────────────────────────────────────────────

export interface DeliveryReadiness {
  ready: boolean;
  blockers: string[];
}

export function checkDeliveryReadiness(jobId: string): DeliveryReadiness {
  const bundle = getBundleForJob(jobId);
  const blockers: string[] = [];

  if (!bundle) {
    return { ready: false, blockers: ["No delivery bundle found"] };
  }

  if (bundle.status !== "completed") {
    blockers.push(`Job status is '${bundle.status}', expected 'completed'`);
  }

  if (!bundle.delivery_ready) {
    blockers.push("Bundle not marked as delivery-ready");
  }

  if (!bundle.verdict) {
    blockers.push("No verdict set");
  }

  if (bundle.finding_counts.critical > 0) {
    blockers.push(
      `${bundle.finding_counts.critical} critical finding(s) require review`
    );
  }

  return { ready: blockers.length === 0, blockers };
}
