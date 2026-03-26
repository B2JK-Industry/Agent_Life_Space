/**
 * Agent Life Space — Operator Console DTOs
 *
 * TypeScript models mapped from Python reviewer domain.
 * Contract-first: these define the API boundary between
 * Python backend and operator surface.
 *
 * Mapping: agent/review/models.py -> operator/src/models/review.ts
 */

// ─────────────────────────────────────────────
// Enums (mirror Python ReviewJobType, Severity, etc.)
// ─────────────────────────────────────────────

export type ReviewJobType = "repo_audit" | "pr_review" | "release_review";
export type ReviewJobStatus = "created" | "validating" | "analyzing" | "verifying" | "completed" | "failed" | "cancelled";
export type Severity = "critical" | "high" | "medium" | "low";
export type Confidence = "high" | "medium" | "low";
export type ExecutionMode = "read_only_host" | "workspace_bound";
export type ApprovalStatus = "pending" | "partially_approved" | "approved" | "denied" | "expired" | "executed";

// ─────────────────────────────────────────────
// Review Finding Summary
// ─────────────────────────────────────────────

export interface ReviewFindingSummary {
  id: string;
  severity: Severity;
  title: string;
  description: string;
  impact: string;
  file_path: string;
  line_start: number;
  line_end: number;
  category: string;
  evidence: string;
  recommendation: string;
  confidence: Confidence;
  tags: string[];
}

// ─────────────────────────────────────────────
// Review Job Summary
// ─────────────────────────────────────────────

export interface ReviewJobSummary {
  id: string;
  job_type: ReviewJobType;
  source: string;
  requester: string;
  status: ReviewJobStatus;
  execution_mode: ExecutionMode;
  created_at: string;
  started_at: string;
  completed_at: string;
  verdict: string;
  verdict_confidence: Confidence;
  finding_counts: Record<Severity, number>;
  total_findings: number;
  error: string;
}

// ─────────────────────────────────────────────
// Delivery Bundle Summary
// ─────────────────────────────────────────────

export interface DeliveryBundleSummary {
  job_id: string;
  job_type: ReviewJobType;
  status: ReviewJobStatus;
  requester: string;
  execution_mode: ExecutionMode;
  verdict: string;
  verdict_confidence: Confidence;
  finding_counts: Record<Severity, number>;
  markdown_report: string;
  findings_only: ReviewFindingSummary[];
  artifact_count: number;
  delivery_ready: boolean;
  export_mode?: "internal" | "client_safe";
  created_at: string;
  completed_at: string;
}

// ─────────────────────────────────────────────
// Approval Request Summary
// ─────────────────────────────────────────────

export interface ApprovalRequestSummary {
  id: string;
  category: string;
  description: string;
  risk_level: string;
  reason: string;
  proposed_by: string;
  status: ApprovalStatus;
  created_at: number;
  ttl_seconds: number;
  context: Record<string, unknown>;
}

// ─────────────────────────────────────────────
// Execution Trace Step
// ─────────────────────────────────────────────

export interface ExecutionTraceStep {
  step: string;
  status: string;
  started_at: number;
  completed_at: number;
  duration_ms: number;
  detail: string;
  error: string;
}
