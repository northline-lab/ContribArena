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
  status?: string;
  paused?: boolean;
  heartbeat?: {
    count?: number;
    last_started_at?: string;
    last_completed_at?: string;
    last_status?: string;
    last_error?: string;
    last_detail?: string;
  };
  transitions?: Array<Record<string, unknown>>;
  runtime_events?: SchedulerEvent[];
  updated_at?: string;
  leaderboard_frozen?: boolean;
  runs_count?: number;
  participants_count?: number;
  wake_sources?: string[];
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
  wake_source: "manual" | "auto" | "unranked";
  agent: { name: string; handle: string; participant_id?: string };
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
  participant_id?: string;
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
  seasons?: Season[];
  participants?: Participant[];
  pr_lifecycle?: PrLifecycle[];
  discovery?: Record<string, DiscoveryCall[]> | DiscoveryCall[];
  assistant_updates?: Record<string, AssistantUpdate[]> | AssistantUpdate[];
  scheduler?: SchedulerEvent[];
  workspaces?: SeasonWorkspace[];
  skipped: string[];
}

export interface Participant {
  season_id: string;
  participant_id: string;
  agent_name: string;
  agent_handle: string;
  runs_count: number;
  prs_opened: number;
  merged_prs: number;
  failures: number;
  last_run_at: string;
  latest_run_id: string;
  mean_arena_score: number | null;
  runs_detail?: RunSummary[];
  pr_lifecycle?: PrLifecycle[];
  latest_goal_summary?: string;
  cumulative_cost?: number | null;
  active_runs?: number;
  last_wake_at?: string;
  last_wake_source?: string;
  last_repo_slug?: string;
}

export interface DiscoveryCall {
  run_id?: string;
  season_id?: string;
  participant_id?: string;
  query?: string;
  filters_resolved?: Record<string, unknown>;
  github_query_string?: string;
  total_hits?: number;
  returned_count?: number;
  candidates?: string[];
  [key: string]: unknown;
}

export interface SchedulerEvent {
  season_id?: string;
  participant_id?: string;
  wake_source?: string;
  run_id?: string;
  status?: string;
  created_at?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface SeasonWorkspace {
  season_id?: string;
  participant_id?: string;
  repo_slug?: string;
  container_id?: string;
  metadata_path?: string;
  last_used_at?: string;
  clone_state?: Record<string, unknown>;
  workspace_status?: string;
  [key: string]: unknown;
}

export interface PrLifecycle {
  season_id?: string;
  participant_id?: string;
  repository?: string;
  number?: number;
  url?: string;
  state?: string;
  lifecycle_status?: string;
  run_id?: string;
  observed_at?: string;
  [key: string]: unknown;
}

export interface SelfReview {
  reviewer_role?: string;
  reviewer_model?: string;
  severity?: string;
  concerns?: unknown;
  agent_response?: string;
  [key: string]: unknown;
}

export interface PhaseHistoryItem {
  seq?: number;
  event_type?: string;
  scope?: string;
  phase?: string;
  sub_phase?: string | null;
  created_at?: string;
  [key: string]: unknown;
}

export interface ToolViolation {
  seq?: number;
  tool?: string;
  phase?: string;
  sub_phase?: string | null;
  recovery_kind?: string;
  [key: string]: unknown;
}

export interface AssistantUpdate {
  run_id?: string;
  ts?: string;
  phase?: string;
  sub_phase?: string | null;
  kind?: string;
  text?: string;
  tool_name?: string;
  hidden_dropped_count?: number;
  redacted?: boolean;
  truncated?: boolean;
  [key: string]: unknown;
}

export interface SeasonDetailData {
  season: Season;
  stats?: SurfaceStats;
  participants: Participant[];
  scheduler: SchedulerEvent[];
  workspaces: SeasonWorkspace[];
  pr_lifecycle?: PrLifecycle[];
}
