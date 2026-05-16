export type StageStatus =
  | "not_started" | "running" | "passed" | "failed"
  | "blocked" | "skipped" | "pending" | "unknown";

export type MaintainerStatus =
  | "pending" | "reviewed" | "changes_requested" | "merged"
  | "closed" | "stale" | "unknown";

export type ArtifactVisibility = "public" | "operator" | "internal";

export type ContributionClass = "low_risk_code" | "tests" | "docs" | "mixed" | "unknown";

export type SeasonPhase = "owned_repo_calibration" | "external_live" | "archived" | "unknown";

export type JudgementStatus =
  | "not_judged" | "judged" | "partial_fallback" | "fallback"
  | "deferred" | "failed" | "unknown";

export interface Season {
  id: string;
  name: string;
  phase: SeasonPhase;
}

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

export interface RubricScore {
  dimension: string;
  score: number;
  max_score: number;
  weight: number;
}

export interface Judgement {
  status: JudgementStatus;
  judge_score: number | null;
  real_world_adjustment: number;
  arena_score: number | null;
  rubric_summary: RubricScore[];
  source_artifacts: string[];
}

export interface RunSummary {
  schema_version: string;
  run_id: string;
  run_mode: string;
  model: string;
  agent: { name: string; handle: string };
  repository: { full_name: string; url: string };
  season: Season;
  opportunity_source: "issue_url" | "discovery_event_id" | "none";
  opportunity_source_ref: string;
  started_at: string;
  completed_at: string;
  duration_seconds: number;
  run_status: string;
  terminal_reason: string;
  terminal_layer: string;
  contribution_class: ContributionClass;
  pipeline: PipelineStage[];
  quality_gate: { status: string; warnings: string[] };
  pull_request: { url: string; number: number | null; state: string };
  maintainer_outcome: { status: MaintainerStatus; observed_at: string; source: string };
  judgement: Judgement;
  artifacts: Artifact[];
}

export interface LeaderboardEntry {
  season_id: string;
  season_name: string;
  season_phase: SeasonPhase;
  agent_name: string;
  agent_handle: string;
  runs: number;
  prs_opened: number;
  quality_gate_pass_rate: number;
  reviewed_prs: number;
  merged_prs: number;
  judged_runs: number;
  judgement_fallback_runs: number;
  merge_rate: number;
  mean_judge_score: number | null;
  mean_arena_score: number | null;
}

export interface SurfaceStats {
  schema_version: string;
  runs: number;
  seasons: string[];
  prs_opened: number;
  reviewed_prs: number;
  merged_prs: number;
  judged_runs: number;
  quality_gate_pass_rate: number;
  merge_rate: number;
}

export interface SurfaceData {
  schema_version: string;
  generated_at: string;
  runs: RunSummary[];
  leaderboard: LeaderboardEntry[];
  stats: SurfaceStats;
  skipped: string[];
}
