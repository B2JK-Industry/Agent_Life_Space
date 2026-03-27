/**
 * Mock data for operator console development.
 * Derived from actual Python reviewer output structure.
 */

import type {
  ApprovalRequestSummary,
  DeliveryBundleSummary,
  ReviewJobSummary,
} from "../models/review";
import type {
  ControlPlaneJobSummary,
  OperatorReport,
} from "../models/reporting";

export const mockJobs: ReviewJobSummary[] = [
  {
    id: "a1b2c3d4e5f67890",
    job_type: "repo_audit",
    source: "telegram",
    requester: "daniel",
    status: "completed",
    execution_mode: "read_only_host",
    created_at: "2026-03-26T14:00:00+00:00",
    started_at: "2026-03-26T14:00:01+00:00",
    completed_at: "2026-03-26T14:00:05+00:00",
    verdict: "pass_with_findings",
    verdict_confidence: "medium",
    finding_counts: { critical: 0, high: 2, medium: 3, low: 1 },
    total_findings: 6,
    error: "",
  },
  {
    id: "b2c3d4e5f6789012",
    job_type: "pr_review",
    source: "api",
    requester: "ci-bot",
    status: "completed",
    execution_mode: "read_only_host",
    created_at: "2026-03-26T15:00:00+00:00",
    started_at: "2026-03-26T15:00:01+00:00",
    completed_at: "2026-03-26T15:00:03+00:00",
    verdict: "pass",
    verdict_confidence: "high",
    finding_counts: { critical: 0, high: 0, medium: 0, low: 0 },
    total_findings: 0,
    error: "",
  },
  {
    id: "c3d4e5f678901234",
    job_type: "repo_audit",
    source: "manual",
    requester: "daniel",
    status: "failed",
    execution_mode: "read_only_host",
    created_at: "2026-03-26T16:00:00+00:00",
    started_at: "",
    completed_at: "",
    verdict: "",
    verdict_confidence: "low",
    finding_counts: { critical: 0, high: 0, medium: 0, low: 0 },
    total_findings: 0,
    error: "Validation failed: repo_path must not contain '..'",
  },
];

export const mockBundle: DeliveryBundleSummary = {
  job_id: "a1b2c3d4e5f67890",
  job_type: "repo_audit",
  status: "completed",
  requester: "daniel",
  execution_mode: "read_only_host",
  verdict: "pass_with_findings",
  verdict_confidence: "medium",
  finding_counts: { critical: 0, high: 2, medium: 3, low: 1 },
  markdown_report:
    "# Review Report\n\n## Executive Summary\n\nRepo audit. 15 files, 1200 lines.\n\n## Findings\n\n### [HIGH] eval() usage\n\n**Location:** `src/handler.py:42`\n\n...",
  findings_only: [
    {
      id: "f001",
      severity: "high",
      title: "eval() usage",
      description: "Direct eval() call with user input",
      impact: "Remote code execution risk",
      file_path: "src/handler.py",
      line_start: 42,
      line_end: 42,
      category: "security",
      evidence: "result = eval(user_data)",
      recommendation: "Replace with safe parser",
      confidence: "high",
      tags: ["security", "rce"],
    },
  ],
  artifact_count: 4,
  delivery_ready: true,
  export_mode: "internal",
  created_at: "2026-03-26T14:00:00+00:00",
  completed_at: "2026-03-26T14:00:05+00:00",
};

export const mockApprovals: ApprovalRequestSummary[] = [
  {
    id: "apr-001",
    category: "external",
    description: "Deliver review report for job a1b2c3d4 (pass_with_findings)",
    risk_level: "medium",
    reason: "Review of project — 6 findings",
    proposed_by: "agent",
    status: "pending",
    created_at: 1711454400,
    ttl_seconds: 3600,
    context: { job_id: "a1b2c3d4e5f67890", verdict: "pass_with_findings" },
  },
];

export const mockControlPlaneJobs: ControlPlaneJobSummary[] = [
  {
    job_id: "build-001",
    job_kind: "build",
    status: "blocked",
    title: "Ship build candidate",
    subkind: "build_job",
    requester: "daniel",
    execution_mode: "workspace_bound",
    created_at: "2026-03-27T09:00:00+00:00",
    completed_at: "",
    artifact_count: 5,
    scope: "agent/build/service.py",
    outcome: "accepted; review=fail",
    blocked_reason: "Post-build review blocked completion",
  },
  {
    job_id: "review-001",
    job_kind: "review",
    status: "completed",
    title: "Audit release candidate",
    subkind: "review_job",
    requester: "daniel",
    execution_mode: "read_only_host",
    created_at: "2026-03-27T08:55:00+00:00",
    completed_at: "2026-03-27T08:56:00+00:00",
    artifact_count: 4,
    scope: "/tmp/repo",
    outcome: "pass_with_findings",
    blocked_reason: "",
  },
  {
    job_id: "agent_loop",
    job_kind: "operate",
    status: "running",
    title: "Agent loop",
    subkind: "agent_loop",
    outcome: "queue=2",
  },
];

export const mockOperatorReport: OperatorReport = {
  summary: {
    total_jobs: mockControlPlaneJobs.length,
    blocked_jobs: 1,
    pending_approvals: mockApprovals.length,
    disabled_tools: 1,
  },
  inbox: [
    {
      kind: "approval",
      id: "apr-001",
      status: "pending",
      title: "Deliver review report for job a1b2c3d4 (pass_with_findings)",
      detail: "Review of project — 6 findings",
    },
    {
      kind: "job_attention",
      id: "build-001",
      status: "blocked",
      title: "Ship build candidate",
      detail: "Post-build review blocked completion",
    },
  ],
  recent_jobs: mockControlPlaneJobs,
  pending_approvals: mockApprovals,
  controls: {
    total_disabled: 1,
    disabled_tools: {
      web_fetch: {
        reason: "maintenance",
      },
    },
  },
  agent_status: {
    running: false,
    control_plane: {
      queryable_job_kinds: ["build", "review", "operate"],
    },
  },
};
