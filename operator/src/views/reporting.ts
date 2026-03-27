/**
 * Operator reporting/inbox view over shared mock control-plane data.
 */

import type { OperatorInboxItem, OperatorReport } from "../models/reporting";
import { mockOperatorReport } from "../mock/data";

export function getOperatorReport(): OperatorReport {
  return mockOperatorReport;
}

export function getInboxItems(kind?: OperatorInboxItem["kind"]): OperatorInboxItem[] {
  if (!kind) {
    return [...mockOperatorReport.inbox];
  }
  return mockOperatorReport.inbox.filter((item) => item.kind === kind);
}

export function getBlockedJobs() {
  return mockOperatorReport.recent_jobs.filter(
    (job) => job.status === "blocked" || job.status === "failed"
  );
}
