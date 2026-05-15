export type StageStatus =
  | "not_started" | "running" | "passed" | "failed"
  | "blocked" | "skipped" | "pending" | "unknown";

export type MaintainerStatus =
  | "pending" | "reviewed" | "changes_requested" | "merged"
  | "closed" | "stale" | "unknown";

export type ArtifactVisibility = "public" | "operator" | "internal";

export type ContributionClass = "low_risk_code" | "tests" | "docs" | "mixed" | "unknown";

export interface PipelineStage {
  stage_id: string;
  status: StageStatus;
  started_at: string;
  completed_at: string;
  summary: string;
  source_artifacts: string[];
}

export interface Artifact {
  name: string;
  kind: string;
  visibility: ArtifactVisibility;
  url: string;
  size_bytes: number;
  redacted: boolean;
}

export interface RunSummary {
  schema_version: string;
  run_id: string;
  agent: { name: string; handle: string };
  repository: { full_name: string; url: string };
  started_at: string;
  completed_at: string;
  duration_seconds: number;
  run_status: string;
  terminal_reason: string;
  contribution_class: ContributionClass;
  pipeline: PipelineStage[];
  quality_gate: { status: string; warnings: string[] };
  pull_request: { url: string; number: number | null; state: string };
  maintainer_outcome: { status: MaintainerStatus; observed_at: string; source: string };
  artifacts: Artifact[];
}

export interface LeaderboardEntry {
  agent_name: string;
  agent_handle: string;
  runs: number;
  prs_opened: number;
  quality_gate_pass_rate: number;
  reviewed_prs: number;
  merged_prs: number;
  merge_rate: number;
}

export interface SurfaceData {
  runs: RunSummary[];
  leaderboard: LeaderboardEntry[];
  generated_at: string;
}
