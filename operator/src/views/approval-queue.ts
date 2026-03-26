/**
 * Approval Queue Preview View
 *
 * Mock-driven skeleton for viewing pending approval requests.
 * Operator reviews and approves/denies delivery requests.
 */

import type { ApprovalRequestSummary, ApprovalStatus } from "../models/review";
import type { ClientSafeApproval } from "../models/client-safe";
import { mockApprovals } from "../mock/data";

// ─────────────────────────────────────────────
// Queue access (mock-driven)
// ─────────────────────────────────────────────

export function getPendingApprovals(): ApprovalRequestSummary[] {
  return mockApprovals.filter((a) => a.status === "pending");
}

export function getAllApprovals(
  statusFilter?: ApprovalStatus
): ApprovalRequestSummary[] {
  if (statusFilter) {
    return mockApprovals.filter((a) => a.status === statusFilter);
  }
  return [...mockApprovals];
}

export function getApprovalById(
  id: string
): ApprovalRequestSummary | undefined {
  return mockApprovals.find((a) => a.id === id);
}

// ─────────────────────────────────────────────
// Client-safe projection
// ─────────────────────────────────────────────

export function toClientSafeApproval(
  approval: ApprovalRequestSummary
): ClientSafeApproval {
  return {
    id: approval.id,
    category: approval.category,
    description: approval.description,
    risk_level: approval.risk_level,
    status: approval.status,
  };
}

// ─────────────────────────────────────────────
// Queue stats
// ─────────────────────────────────────────────

export interface ApprovalQueueStats {
  total: number;
  pending: number;
  approved: number;
  denied: number;
  expired: number;
}

export function getApprovalQueueStats(): ApprovalQueueStats {
  const all = mockApprovals;
  return {
    total: all.length,
    pending: all.filter((a) => a.status === "pending").length,
    approved: all.filter((a) => a.status === "approved").length,
    denied: all.filter((a) => a.status === "denied").length,
    expired: all.filter((a) => a.status === "expired").length,
  };
}

// ─────────────────────────────────────────────
// Expiry check
// ─────────────────────────────────────────────

export function isExpired(approval: ApprovalRequestSummary): boolean {
  const now = Date.now() / 1000;
  return now > approval.created_at + approval.ttl_seconds;
}

export function getExpiredApprovals(): ApprovalRequestSummary[] {
  return mockApprovals
    .filter((a) => a.status === "pending")
    .filter(isExpired);
}
