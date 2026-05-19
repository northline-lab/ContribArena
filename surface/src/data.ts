import type {
  DiscoveryCall,
  Participant,
  PhaseHistoryItem,
  PrLifecycle,
  RunSummary,
  Season,
  SeasonDetailData,
  SelfReview,
  SurfaceData,
  ToolViolation,
} from "./types";

const DATA_PATH = import.meta.env.BASE_URL + "data/surface.json";
const API_BASE = (import.meta.env.VITE_CONTRIBARENA_API_BASE_URL ?? "").replace(/\/$/, "");

export async function loadSurfaceData(): Promise<SurfaceData> {
  const res = await fetch(API_BASE ? `${API_BASE}/api/surface` : DATA_PATH);
  if (!res.ok) throw new Error(`Failed to load surface data: ${res.status}`);
  return res.json();
}

async function fetchJson<T>(path: string): Promise<T | null> {
  if (!API_BASE) return null;
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`Failed to load ${path}: ${res.status}`);
  }
  return res.json();
}

export function filterSurfaceBySeason(data: SurfaceData, seasonId: string): SurfaceData {
  if (!seasonId || seasonId === "all") return data;
  return {
    ...data,
    runs: data.runs.filter((run) => run.season.id === seasonId),
    leaderboard: data.leaderboard.filter((row) => row.season_id === seasonId),
  };
}

export function seasonsFromSurface(data: SurfaceData): Season[] {
  if (data.seasons?.length) return [...data.seasons].sort((a, b) => a.id.localeCompare(b.id));
  const seasons = new Map<string, Season>();
  for (const run of data.runs) {
    if (run.season.id) seasons.set(run.season.id, run.season);
  }
  for (const row of data.leaderboard) {
    if (!row.season_id || seasons.has(row.season_id)) continue;
    seasons.set(row.season_id, {
      id: row.season_id,
      name: row.season_name || row.season_id,
      phase: row.season_phase,
    });
  }
  return Array.from(seasons.values()).sort((a, b) => a.id.localeCompare(b.id));
}

export function defaultSeasonId(data: SurfaceData): string {
  const seasons = seasonsFromSurface(data);
  return seasons[0]?.id ?? "all";
}

export function participantsFromSurface(data: SurfaceData, seasonId: string): Participant[] {
  if (data.participants?.length) {
    const runsByParticipant = new Map<string, RunSummary[]>();
    for (const run of filterSurfaceBySeason(data, seasonId).runs) {
      const participantId = run.agent.participant_id || run.agent.handle || run.agent.name;
      runsByParticipant.set(participantId, [...(runsByParticipant.get(participantId) ?? []), run]);
    }
    const lifecycleByParticipant = new Map<string, PrLifecycle[]>();
    for (const item of data.pr_lifecycle ?? []) {
      const participantId = item.participant_id || "";
      lifecycleByParticipant.set(participantId, [...(lifecycleByParticipant.get(participantId) ?? []), item]);
    }
    return data.participants
      .filter((participant) => seasonId === "all" || participant.season_id === seasonId)
      .map((participant) => ({
        ...participant,
        runs_detail: participant.runs_detail ?? runsByParticipant.get(participant.participant_id) ?? [],
        pr_lifecycle: participant.pr_lifecycle ?? lifecycleByParticipant.get(participant.participant_id) ?? [],
      }));
  }
  const scoped = filterSurfaceBySeason(data, seasonId);
  const buckets = new Map<string, Participant & { _scores: number[] }>();
  for (const run of scoped.runs) {
    const participantId = run.agent.participant_id || run.agent.handle || run.agent.name;
    if (!participantId) continue;
    const existing = buckets.get(participantId) ?? {
      season_id: run.season.id,
      participant_id: participantId,
      agent_name: run.agent.name || "builtin",
      agent_handle: run.agent.handle || participantId,
      runs_count: 0,
      prs_opened: 0,
      merged_prs: 0,
      failures: 0,
      last_run_at: "",
      latest_run_id: "",
      mean_arena_score: null,
      runs_detail: [],
      pr_lifecycle: [],
      _scores: [],
    };
    existing.runs_count += 1;
    if (run.run_status !== "completed") existing.failures += 1;
    if (run.pull_request.url || ["open", "closed", "merged"].includes(run.pull_request.state)) {
      existing.prs_opened += 1;
    }
    if (run.pull_request.state === "merged" || run.maintainer_outcome.status === "merged") {
      existing.merged_prs += 1;
    }
    if (run.started_at >= existing.last_run_at) {
      existing.last_run_at = run.started_at;
      existing.latest_run_id = run.run_id;
    }
    if (run.judgement.arena_score != null) existing._scores.push(run.judgement.arena_score);
    existing.runs_detail?.push(run);
    buckets.set(participantId, existing);
  }
  return Array.from(buckets.values()).map((item) => {
    const scores = item._scores;
    const mean = scores.length ? Math.round((scores.reduce((a, b) => a + b, 0) / scores.length) * 100) / 100 : null;
    return {
      season_id: item.season_id,
      participant_id: item.participant_id,
      agent_name: item.agent_name,
      agent_handle: item.agent_handle,
      runs_count: item.runs_count,
      prs_opened: item.prs_opened,
      merged_prs: item.merged_prs,
      failures: item.failures,
      last_run_at: item.last_run_at,
      latest_run_id: item.latest_run_id,
      mean_arena_score: mean,
      runs_detail: item.runs_detail,
      pr_lifecycle: item.pr_lifecycle,
    };
  });
}

export async function loadSeasonDetail(surface: SurfaceData, seasonId: string): Promise<SeasonDetailData> {
  const apiData = await fetchJson<SeasonDetailData>(`/api/seasons/${encodeURIComponent(seasonId)}`);
  if (apiData) {
    const prData = await fetchJson<{ pr_lifecycle: PrLifecycle[] }>(
      `/api/seasons/${encodeURIComponent(seasonId)}/pr-lifecycle`,
    );
    return { ...apiData, pr_lifecycle: prData?.pr_lifecycle ?? [] };
  }
  const season = seasonsFromSurface(surface).find((item) => item.id === seasonId) ?? {
    id: seasonId,
    name: seasonId,
    phase: "unknown" as const,
  };
  return {
    season,
    stats: filterSurfaceBySeason(surface, seasonId).stats,
    participants: participantsFromSurface(surface, seasonId),
    scheduler: (surface.scheduler ?? []).filter((item) => !item.season_id || item.season_id === seasonId),
    workspaces: (surface.workspaces ?? []).filter((item) => !item.season_id || item.season_id === seasonId),
    pr_lifecycle: (surface.pr_lifecycle ?? []).filter((item) => !item.season_id || item.season_id === seasonId),
  };
}

export async function loadParticipantDetail(
  surface: SurfaceData,
  participantId: string,
  seasonId: string,
): Promise<Participant | null> {
  const apiData = await fetchJson<{ participant: Participant }>(
    `/api/participants/${encodeURIComponent(participantId)}`,
  );
  if (apiData?.participant) return apiData.participant;
  return participantsFromSurface(surface, seasonId).find((item) => item.participant_id === participantId) ?? null;
}

export async function loadRunEvidence(runId: string): Promise<{
  discovery: DiscoveryCall[];
  selfReview: SelfReview[];
  phaseHistory: PhaseHistoryItem[];
  toolViolations: ToolViolation[];
}> {
  const [discovery, selfReview, runDetail] = await Promise.all([
    fetchJson<{ discovery: DiscoveryCall[] }>(`/api/runs/${encodeURIComponent(runId)}/discovery`),
    fetchJson<{ self_review: SelfReview[] }>(`/api/runs/${encodeURIComponent(runId)}/self-review`),
    fetchJson<RunSummary & { phase_history?: PhaseHistoryItem[]; tool_violations?: ToolViolation[] }>(
      `/api/runs/${encodeURIComponent(runId)}`,
    ),
  ]);
  return {
    discovery: discovery?.discovery ?? [],
    selfReview: selfReview?.self_review ?? [],
    phaseHistory: runDetail?.phase_history ?? [],
    toolViolations: runDetail?.tool_violations ?? [],
  };
}

export function runEvidenceFromSurface(surface: SurfaceData, runId: string): {
  discovery: DiscoveryCall[];
  selfReview: SelfReview[];
  phaseHistory: PhaseHistoryItem[];
  toolViolations: ToolViolation[];
} {
  const discovery = surface.discovery;
  const discoveryRows = Array.isArray(discovery)
    ? discovery.filter((item) => !item.run_id || item.run_id === runId)
    : discovery?.[runId] ?? [];
  const run = surface.runs.find((item) => item.run_id === runId) as
    | (RunSummary & {
        self_review?: SelfReview[];
        phase_history?: PhaseHistoryItem[];
        tool_violations?: ToolViolation[];
      })
    | undefined;
  return {
    discovery: discoveryRows,
    selfReview: run?.self_review ?? [],
    phaseHistory: run?.phase_history ?? [],
    toolViolations: run?.tool_violations ?? [],
  };
}

export function dataSourceLabel(): string {
  return API_BASE || "static bundle";
}

export function apiEnabled(): boolean {
  return Boolean(API_BASE);
}
