/**
 * Agent Life Space — Operator Console Skeleton
 *
 * Mock-driven skeleton for operator review workflow.
 * No live backend, no auth, no deployment.
 *
 * Exports:
 *   - models: full DTOs + client-safe subset
 *   - views: job list, job detail, delivery preview, approval queue
 *   - mock: development data
 */

// Models
export type {
  ReviewJobType,
  ReviewJobStatus,
  Severity,
  Confidence,
  ExecutionMode,
  ApprovalStatus,
  ReviewFindingSummary,
  ReviewJobSummary,
  DeliveryBundleSummary,
  ApprovalRequestSummary,
  ExecutionTraceStep,
} from "./models/review";

export type {
  ClientSafeFinding,
  ClientSafeJobSummary,
  ClientSafeDeliveryBundle,
  ClientSafeApproval,
} from "./models/client-safe";

// Views
export {
  getJobs,
  getCompletedJobs,
  getFailedJobs,
  getClientSafeJobs,
  getJobListStats,
} from "./views/job-list";
export type { JobListFilter, JobListStats } from "./views/job-list";

export {
  getJobById,
  getClientSafeJobById,
  getFindingsForJob,
  getFindingsBySeverity,
  getJobDetailView,
} from "./views/job-detail";
export type { JobDetailView } from "./views/job-detail";

export {
  getBundleForJob,
  toClientSafeBundle,
  getClientSafeBundleForJob,
  checkDeliveryReadiness,
} from "./views/delivery-preview";
export type { DeliveryReadiness } from "./views/delivery-preview";

export {
  getPendingApprovals,
  getAllApprovals,
  getApprovalById,
  toClientSafeApproval,
  getApprovalQueueStats,
  isExpired,
  getExpiredApprovals,
} from "./views/approval-queue";
export type { ApprovalQueueStats } from "./views/approval-queue";

// Mock data (for development only)
export { mockJobs, mockBundle, mockApprovals } from "./mock/data";
