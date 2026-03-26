/**
 * Client-safe model surface.
 *
 * These types define what a client (external consumer) can see.
 * Internal fields like requester, source, execution_mode, raw errors,
 * and evidence are excluded.
 *
 * Separation rationale:
 *   - requester: internal identity, not relevant to client
 *   - source: internal channel (telegram/api), leaks architecture
 *   - execution_mode: infrastructure detail
 *   - started_at: internal perf metric
 *   - error: raw errors may leak paths/stack traces
 *   - evidence: may contain code snippets with secrets (redacted on Python side,
 *     but TS contract should not expose the field at all)
 *   - proposed_by: internal agent identity
 *   - context: internal metadata on approvals
 */

import type {
  Confidence,
  ReviewJobStatus,
  ReviewJobType,
  Severity,
} from "./review";

// ─────────────────────────────────────────────
// Client-Safe Finding (no evidence, no internal metadata)
// ─────────────────────────────────────────────

export interface ClientSafeFinding {
  id: string;
  severity: Severity;
  title: string;
  description: string;
  impact: string;
  file_path: string;
  line_start: number;
  line_end: number;
  category: string;
  // evidence: intentionally excluded — may contain sensitive code
  recommendation: string;
  confidence: Confidence;
  tags: string[];
}

// ─────────────────────────────────────────────
// Client-Safe Job Summary (no requester, source, execution_mode, error)
// ─────────────────────────────────────────────

export interface ClientSafeJobSummary {
  id: string;
  job_type: ReviewJobType;
  // source: excluded — internal channel info
  // requester: excluded — internal identity
  status: ReviewJobStatus;
  // execution_mode: excluded — infrastructure detail
  created_at: string;
  // started_at: excluded — internal perf metric
  completed_at: string;
  verdict: string;
  verdict_confidence: Confidence;
  finding_counts: Record<Severity, number>;
  total_findings: number;
  // error: excluded — may leak internals
}

// ─────────────────────────────────────────────
// Client-Safe Delivery Bundle
// ─────────────────────────────────────────────

export interface ClientSafeDeliveryBundle {
  job_id: string;
  job_type: ReviewJobType;
  status: ReviewJobStatus;
  // requester: excluded
  // execution_mode: excluded
  verdict: string;
  verdict_confidence: Confidence;
  finding_counts: Record<Severity, number>;
  markdown_report: string;
  findings_only: ClientSafeFinding[];
  artifact_count: number;
  delivery_ready: boolean;
  export_mode: "client_safe";
  completed_at: string;
}

// ─────────────────────────────────────────────
// Client-Safe Approval (minimal — client sees status, not internals)
// ─────────────────────────────────────────────

export interface ClientSafeApproval {
  id: string;
  category: string;
  description: string;
  risk_level: string;
  status: string;
  // proposed_by: excluded — internal agent identity
  // context: excluded — internal metadata
  // ttl_seconds: excluded — internal policy
}
