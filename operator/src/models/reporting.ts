/**
 * Operator-facing reporting DTOs over the shared control-plane query surface.
 */

import type { ApprovalRequestSummary } from "./review";

export type ControlPlaneJobKind = "build" | "review" | "operate";

export interface ControlPlaneJobSummary {
  job_id: string;
  job_kind: ControlPlaneJobKind;
  status: string;
  title: string;
  subkind?: string;
  requester?: string;
  execution_mode?: string;
  created_at?: string;
  completed_at?: string;
  artifact_count?: number;
  scope?: string;
  outcome?: string;
  blocked_reason?: string;
}

export interface OperatorInboxItem {
  kind: "approval" | "job_attention";
  id: string;
  status: string;
  title: string;
  detail: string;
}

export interface OperatorReportSummary {
  total_jobs: number;
  blocked_jobs: number;
  pending_approvals: number;
  disabled_tools: number;
}

export interface OperatorReport {
  summary: OperatorReportSummary;
  inbox: OperatorInboxItem[];
  recent_jobs: ControlPlaneJobSummary[];
  pending_approvals: ApprovalRequestSummary[];
  controls: Record<string, unknown>;
  agent_status: Record<string, unknown>;
}
