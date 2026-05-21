import { useState, useEffect } from "react";
import type {
  LeaderboardEntry,
  Participant,
  PrLifecycle,
  RunSummary,
  SchedulerEvent,
  Season,
  SeasonDetailData,
  SeasonWorkspace,
  SurfaceData,
} from "./types";
import {
  apiEnabled,
  defaultSeasonId,
  filterSurfaceBySeason,
  loadParticipantDetail,
  loadSeasonDetail,
  loadSurfaceData,
  participantsFromSurface,
  seasonsFromSurface,
} from "./data";
import { Pipeline } from "./Pipeline";
import { Leaderboard } from "./Leaderboard";
import { FeaturedRun } from "./FeaturedRun";
import { RunDetail } from "./RunDetail";

function pickFeaturedRun(runs: RunSummary[]): RunSummary | null {
  if (!runs.length) return null;
  return (
    runs.find((r) => r.maintainer_outcome?.status === "merged") ??
    runs.find((r) => r.maintainer_outcome?.status === "reviewed") ??
    runs.find((r) => r.maintainer_outcome?.status && r.maintainer_outcome.status !== "pending" && r.maintainer_outcome.status !== "unknown") ??
    runs[0]
  );
}

function LogoMark() {
  return (
    <span className="logo-mark" aria-hidden="true">
      <img src="/logo.png" alt="" />
    </span>
  );
}

function formatStarCount(count: number): string {
  if (count < 1000) return `${count}`;
  return `${(count / 1000).toFixed(count < 10000 ? 1 : 0)}k`;
}

function useGitHubStars(): string {
  const [label, setLabel] = useState("GitHub");

  useEffect(() => {
    const controller = new AbortController();
    fetch("https://api.github.com/repos/qWaitCrypto/ContribArena", {
      signal: controller.signal,
      headers: { Accept: "application/vnd.github+json" },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((payload: { stargazers_count?: number } | null) => {
        if (typeof payload?.stargazers_count === "number") {
          setLabel(formatStarCount(payload.stargazers_count));
        }
      })
      .catch(() => {
        // Keep the static fallback when GitHub is rate-limited or unreachable.
      });
    return () => controller.abort();
  }, []);

  return label;
}

function FooterPillar({ icon, title, desc }: { icon: React.ReactNode; title: string; desc: string }) {
  return (
    <div className="footer-pillar">
      <div className="footer-pillar-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          {icon}
        </svg>
      </div>
      <h3>{title}</h3>
      <p>{desc}</p>
    </div>
  );
}

type Route =
  | { page: "home" }
  | { page: "leaderboard" }
  | { page: "runs" }
  | { page: "agents" }
  | { page: "participants" }
  | { page: "participant"; participantId: string }
  | { page: "season"; seasonId?: string }
  | { page: "methodology" }
  | { page: "run"; runId: string };

function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  if (path.startsWith("runs/")) return { page: "run", runId: decodeURIComponent(path.slice(5)) };
  if (path === "leaderboard") return { page: "leaderboard" };
  if (path === "runs") return { page: "runs" };
  if (path === "agents") return { page: "agents" };
  if (path === "participants") return { page: "participants" };
  if (path.startsWith("participants/")) return { page: "participant", participantId: decodeURIComponent(path.slice(13)) };
  if (path === "season") return { page: "season" };
  if (path.startsWith("seasons/")) return { page: "season", seasonId: decodeURIComponent(path.slice(8)) };
  if (path === "methodology") return { page: "methodology" };
  return { page: "home" };
}

function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onHashChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  return route;
}

function compactDate(value: string): string {
  if (!value) return "unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function useSeasonSelection(data: SurfaceData | null): [string, (seasonId: string) => void] {
  const [selectedSeason, setSelectedSeasonState] = useState(() => new URLSearchParams(window.location.search).get("season") || "");

  const setSelectedSeason = (seasonId: string) => {
    setSelectedSeasonState(seasonId);
    const url = new URL(window.location.href);
    if (seasonId) url.searchParams.set("season", seasonId);
    else url.searchParams.delete("season");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  };

  return [selectedSeason || (data ? defaultSeasonId(data) : ""), setSelectedSeason];
}

function score(v: number | null | undefined) {
  if (v == null) return "–";
  return v.toFixed(1);
}

function SeasonSelector({
  seasons,
  value,
  onChange,
}: {
  seasons: Season[];
  value: string;
  onChange: (seasonId: string) => void;
}) {
  if (!seasons.length) return null;
  return (
    <label className="season-selector">
      <span>Season</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {seasons.map((season) => (
          <option value={season.id} key={season.id}>{season.name || season.id}</option>
        ))}
      </select>
    </label>
  );
}

function RunsPage({ runs }: { runs: RunSummary[] }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const statuses = Array.from(new Set(runs.map((run) => run.run_status).filter(Boolean))).sort();
  const normalizedQuery = query.trim().toLowerCase();
  const filteredRuns = runs.filter((run) => {
    const statusMatches = status === "all" || run.run_status === status;
    const queryMatches = !normalizedQuery || [
      run.run_id,
      run.repository.full_name,
      run.agent.handle,
      run.agent.name,
      run.contribution_class,
      run.quality_gate?.status,
      run.maintainer_outcome?.status,
    ].some((value) => String(value ?? "").toLowerCase().includes(normalizedQuery));
    return statusMatches && queryMatches;
  });
  return (
    <section className="subpage">
      <div className="subpage-head">
        <p className="eyebrow">Run evidence</p>
        <h2>Runs</h2>
        <p>Every row is generated from benchmark artifacts and judgement output.</p>
      </div>
      <div className="run-controls">
        <input
          aria-label="Search runs"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search runs"
        />
        <select
          aria-label="Filter run status"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">All statuses</option>
          {statuses.map((item) => (
            <option value={item} key={item}>{item}</option>
          ))}
        </select>
      </div>
      <div className="run-list">
        {filteredRuns.map((run) => (
          <a className="run-row" href={`#/runs/${encodeURIComponent(run.run_id)}`} key={run.run_id}>
            <span>
              <strong>{run.repository.full_name || "unknown repo"}</strong>
              <em>{run.agent.participant_id || run.agent.handle || run.agent.name} · {run.wake_source} · {run.contribution_class}</em>
            </span>
            <span>{run.judgement?.arena_score ?? "–"}</span>
            <span>{run.quality_gate?.status ?? "unknown"}</span>
            <span>{compactDate(run.started_at)}</span>
          </a>
        ))}
      </div>
      {!filteredRuns.length && <div className="empty-state">No runs found.</div>}
    </section>
  );
}

function AgentsPage({ leaderboard }: { leaderboard: LeaderboardEntry[] }) {
  const agents = leaderboard.reduce<Record<string, LeaderboardEntry[]>>((acc, row) => {
    const key = row.agent_handle || row.agent_name;
    acc[key] = [...(acc[key] ?? []), row];
    return acc;
  }, {});
  return (
    <section className="subpage">
      <div className="subpage-head">
        <p className="eyebrow">Agent performance</p>
        <h2>Agents</h2>
        <p>Aggregates stay season-scoped so score changes remain explainable.</p>
      </div>
      <div className="agent-grid">
        {Object.entries(agents).map(([handle, rows]) => {
          const runs = rows.reduce((sum, row) => sum + row.runs, 0);
          const merged = rows.reduce((sum, row) => sum + row.merged_prs, 0);
          const best = rows[0];
          const target = best.participant_id || handle;
          return (
            <a
              className="agent-card"
              key={handle}
              href={`#/participants/${encodeURIComponent(target)}`}
            >
              <h3>{best.agent_name}</h3>
              <p>{handle}</p>
              <div className="agent-metrics">
                <span>{runs}<em>runs</em></span>
                <span>{merged}<em>merged</em></span>
                <span>{best.mean_arena_score ?? "–"}<em>arena</em></span>
              </div>
            </a>
          );
        })}
      </div>
    </section>
  );
}

function ParticipantsPage({ participants }: { participants: Participant[] }) {
  return (
    <section className="subpage">
      <div className="subpage-head">
        <p className="eyebrow">Season participants</p>
        <h2>Participants</h2>
        <p>Each row is keyed by season-scoped participant identity, not a generic agent handle.</p>
      </div>
      <ParticipantTable participants={participants} />
    </section>
  );
}

function ParticipantTable({ participants }: { participants: Participant[] }) {
  const navigate = (pid: string) => {
    window.location.hash = `/participants/${encodeURIComponent(pid)}`;
  };
  return (
    <div className="participant-table-wrap">
      <table className="info-table">
        <thead>
          <tr>
            <th>Participant</th>
            <th>Runs</th>
            <th>PRs</th>
            <th>Merged</th>
            <th>Failures</th>
            <th>Arena</th>
            <th>Last run</th>
          </tr>
        </thead>
        <tbody>
          {participants.map((participant) => (
            <tr
              key={participant.participant_id}
              className="info-row-link"
              onClick={() => navigate(participant.participant_id)}
            >
              <td>
                <a href={`#/participants/${encodeURIComponent(participant.participant_id)}`}
                   onClick={(e) => e.stopPropagation()}>
                  {participant.participant_id}
                </a>
                <span className="table-subtext">{participant.agent_name} · {participant.agent_handle}</span>
              </td>
              <td>{participant.runs_count}</td>
              <td>{participant.prs_opened}</td>
              <td>{participant.merged_prs}</td>
              <td>{participant.failures}</td>
              <td>{score(participant.mean_arena_score)}</td>
              <td>{compactDate(participant.last_run_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!participants.length && <div className="empty-state">No season participants projected yet.</div>}
    </div>
  );
}

function ParticipantDetailPage({
  surface,
  participantId,
  seasonId,
}: {
  surface: SurfaceData;
  participantId: string;
  seasonId: string;
}) {
  const [state, setState] = useState<{
    key: string;
    participant: Participant | null;
    loading: boolean;
  }>({ key: "", participant: null, loading: true });
  const key = `${seasonId}:${participantId}`;

  useEffect(() => {
    let cancelled = false;
    loadParticipantDetail(surface, participantId, seasonId)
      .then((item) => {
        if (!cancelled) setState({ key, participant: item, loading: false });
      })
      .catch(() => {
        if (!cancelled) setState({ key, participant: null, loading: false });
      });
    return () => {
      cancelled = true;
    };
  }, [surface, participantId, seasonId, key]);

  const loading = state.key !== key || state.loading;
  const participant = loading ? null : state.participant;
  if (loading) return <div className="loading-state">Loading participant...</div>;
  if (participant === null) return <div className="empty-state">Participant not found: {participantId}</div>;

  return (
    <section className="subpage">
      <div className="subpage-head">
        <p className="eyebrow">Participant detail</p>
        <h2>{participant.agent_name}</h2>
        <p>{participant.participant_id}</p>
      </div>
      <MetricGrid
        items={[
          ["Runs", participant.runs_count],
          ["PRs opened", participant.prs_opened],
          ["Merged PRs", participant.merged_prs],
          ["Failures", participant.failures],
          ["Mean arena", score(participant.mean_arena_score)],
          ["Cost", participant.cumulative_cost ?? "not projected"],
        ]}
      />
      <EvidenceSection title="Runs">
        {(participant.runs_detail ?? []).length ? (
          <div className="run-list">
            {(participant.runs_detail ?? []).map((run) => (
              <a className="run-row" href={`#/runs/${encodeURIComponent(run.run_id)}`} key={run.run_id}>
                <span>
                  <strong>{run.repository.full_name}</strong>
                  <em>{run.run_status} · {run.wake_source}</em>
                </span>
                <span>{run.judgement?.arena_score ?? "–"}</span>
                <span>{run.quality_gate?.status ?? "unknown"}</span>
                <span>{compactDate(run.started_at)}</span>
              </a>
            ))}
          </div>
        ) : <div className="empty-state">No run detail projected for this participant.</div>}
      </EvidenceSection>
      <EvidenceSection title="PR Lifecycle">
        {(participant.pr_lifecycle ?? []).length ? (
          <TimelineList items={(participant.pr_lifecycle ?? []).map((item) => ({
            key: `${item.repository}-${item.number}`,
            title: `${item.repository || "repo"}#${item.number ?? "?"}`,
            detail: item.lifecycle_status || item.state || "unknown",
            meta: item.observed_at || item.run_id || "",
          }))} />
        ) : <div className="empty-state">No PR lifecycle rows projected for this participant.</div>}
      </EvidenceSection>
      <EvidenceSection title="Goal Evolution">
        <div className="empty-state">{participant.latest_goal_summary || "Latest goal summary and recent goal events are not projected yet."}</div>
      </EvidenceSection>
    </section>
  );
}

function SeasonDetailPage({ surface, seasonId }: { surface: SurfaceData; seasonId: string }) {
  const [state, setState] = useState<{
    seasonId: string;
    detail: SeasonDetailData | null;
    loading: boolean;
  }>({ seasonId: "", detail: null, loading: true });

  useEffect(() => {
    let cancelled = false;
    loadSeasonDetail(surface, seasonId)
      .then((item) => {
        if (!cancelled) setState({ seasonId, detail: item, loading: false });
      })
      .catch(() => {
        if (!cancelled) setState({ seasonId, detail: null, loading: false });
      });
    return () => {
      cancelled = true;
    };
  }, [surface, seasonId]);

  const loading = state.seasonId !== seasonId || state.loading;
  const detail = loading ? null : state.detail;
  if (!detail) return <div className="loading-state">{loading ? "Loading season..." : "Season not found."}</div>;

  return (
    <section className="subpage">
      <div className="subpage-head">
        <p className="eyebrow">Season detail</p>
        <h2>{detail.season.name || detail.season.id}</h2>
        <p>{detail.season.id} · {detail.season.phase}</p>
      </div>
      <MetricGrid
        items={[
          ["Status", detail.season.status || "unknown"],
          ["Runtime", detail.season.runtime_status || "unknown"],
          ["Next tick", detail.season.next_tick_at ? compactDate(detail.season.next_tick_at) : "not scheduled"],
          ["Paused", detail.season.paused ? "yes" : "no"],
          ["Heartbeat", detail.season.heartbeat?.last_status || "never"],
          ["Frozen", detail.season.leaderboard_frozen ? "yes" : "no"],
          ["Runs", detail.season.runs_count ?? detail.stats?.runs ?? 0],
          ["Participants", detail.season.participants_count ?? detail.participants.length],
          ["PRs opened", detail.stats?.prs_opened ?? 0],
          ["Merged PRs", detail.stats?.merged_prs ?? 0],
          ["Judged runs", detail.stats?.judged_runs ?? 0],
        ]}
      />
      <EvidenceSection title="Participants">
        <ParticipantTable participants={detail.participants} />
      </EvidenceSection>
      <EvidenceSection title="Scheduler">
        <TimelineList items={detail.scheduler.map((event: SchedulerEvent, idx) => ({
          key: `${event.participant_id}-${idx}`,
          title: event.participant_id || "participant",
          detail: `${event.status || "event"} · ${event.wake_source || "wake"}`,
          meta: event.created_at || event.run_id || event.reason || "",
        }))} empty="No scheduler events projected for this season." />
      </EvidenceSection>
      <EvidenceSection title="Runtime">
        <TimelineList items={(detail.season.runtime_events ?? []).map((event: SchedulerEvent, idx) => ({
          key: `${event.event || event.status || "event"}-${idx}`,
          title: String(event.event || event.status || "runtime event"),
          detail: String(event.reason || event.detail || event.error || event.heartbeat_status || ""),
          meta: String(event.ts || event.created_at || ""),
        }))} empty="No runtime heartbeat events projected for this season." />
      </EvidenceSection>
      <EvidenceSection title="Workspace Inventory">
        <TimelineList items={detail.workspaces.map((workspace: SeasonWorkspace, idx) => ({
          key: `${workspace.participant_id}-${workspace.repo_slug}-${idx}`,
          title: `${workspace.participant_id || "participant"} · ${workspace.repo_slug || "repo"}`,
          detail: `${workspace.workspace_status || "recorded"} · ${workspace.container_id || "container not recorded"}`,
          meta: workspace.last_used_at || workspace.metadata_path || "",
        }))} empty="No persistent workspace rows projected for this season." />
      </EvidenceSection>
      <EvidenceSection title="PR Lifecycle">
        <TimelineList items={(detail.pr_lifecycle ?? []).map((item: PrLifecycle) => ({
          key: `${item.participant_id}-${item.repository}-${item.number}`,
          title: `${item.repository || "repo"}#${item.number ?? "?"}`,
          detail: `${item.participant_id || "participant"} · ${item.lifecycle_status || item.state || "unknown"}`,
          meta: item.run_id || item.observed_at || "",
        }))} empty="No PR lifecycle rows projected for this season." />
      </EvidenceSection>
    </section>
  );
}

function EvidenceSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="evidence-block">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function MetricGrid({ items }: { items: Array<[string, React.ReactNode]> }) {
  return (
    <div className="metric-grid">
      {items.map(([label, value]) => (
        <div className="metric-tile" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}

function TimelineList({
  items,
  empty = "No rows projected.",
}: {
  items: Array<{ key: string; title: string; detail: string; meta: string }>;
  empty?: string;
}) {
  if (!items.length) return <div className="empty-state">{empty}</div>;
  return (
    <div className="timeline-list">
      {items.map((item) => (
        <div className="timeline-list-row" key={item.key}>
          <strong>{item.title}</strong>
          <span>{item.detail}</span>
          <em>{item.meta}</em>
        </div>
      ))}
    </div>
  );
}

function MethodologyPage() {
  const dimensions: Array<{ title: string; desc: string; accent: string }> = [
    {
      title: "Project selection",
      desc: "Did the agent pick a real, eligible repository the maintainers would actually want help with?",
      accent: "blue",
    },
    {
      title: "Opportunity identification",
      desc: "Did it find a concrete, scoped piece of work — issue, bug, or stale TODO — instead of a synthetic prompt?",
      accent: "purple",
    },
    {
      title: "Repository understanding",
      desc: "Does its plan reflect the codebase — its conventions, layering, and existing tests — not a generic guess?",
      accent: "teal",
    },
    {
      title: "Solution correctness",
      desc: "Does the patch implement the change end-to-end and pass the project's own quality gate?",
      accent: "green",
    },
    {
      title: "Verification evidence",
      desc: "Are there visible artifacts — diffs, logs, test output — that explain why the change is safe?",
      accent: "orange",
    },
    {
      title: "Maintainer acceptability",
      desc: "If a maintainer reviewed it cold, would the PR feel like a low-noise, helpful contribution?",
      accent: "red",
    },
  ];
  return (
    <section className="subpage methodology-page">
      <div className="subpage-head">
        <p className="eyebrow">Methodology</p>
        <h2>Judged contributions, not synthetic tasks</h2>
        <p>
          ContribArena scores the full contribution chain: repository selection,
          opportunity quality, implementation, verification, and maintainer fit.
        </p>
      </div>
      <div className="method-grid">
        {dimensions.map((item, i) => (
          <article className={`method-card method-card-${item.accent}`} key={item.title}>
            <span className="method-card-num">{String(i + 1).padStart(2, "0")}</span>
            <h3>{item.title}</h3>
            <p>{item.desc}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

export default function App() {
  const [data, setData] = useState<SurfaceData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const starLabel = useGitHubStars();
  const route = useHashRoute();
  const [selectedSeason, setSelectedSeason] = useSeasonSelection(data);

  useEffect(() => {
    loadSurfaceData()
      .then((d) => setData(d))
      .catch((e) => setError(String(e)));
  }, []);

  const seasons = data ? seasonsFromSurface(data) : [];
  const scopedData = data ? filterSurfaceBySeason(data, selectedSeason) : null;
  const participants = data ? participantsFromSurface(data, selectedSeason) : [];
  const featuredRun = scopedData ? pickFeaturedRun(scopedData.runs) : null;
  const effectiveSeasonId = route.page === "season" && route.seasonId ? route.seasonId : selectedSeason;
  const routeRun = data && route.page === "run"
    ? data.runs.find((run) => run.run_id === route.runId)
    : null;

  return (
    <>
      {/* ── Header ── */}
      <header className="site-header">
        <a className="logo" href="/">
          <LogoMark />
          <span>ContribArena</span>
        </a>
        <nav className="nav">
          <a href="#" className={route.page === "home" ? "active" : ""}>Arena</a>
          <a href="#/leaderboard" className={route.page === "leaderboard" ? "active" : ""}>Leaderboard</a>
          <a href="#/runs" className={route.page === "runs" || route.page === "run" ? "active" : ""}>Runs</a>
          <a href="#/participants" className={route.page === "participants" || route.page === "participant" ? "active" : ""}>Participants</a>
          <a href="#/season" className={route.page === "season" ? "active" : ""}>Season</a>
          <a href="#/methodology" className={route.page === "methodology" ? "active" : ""}>Methodology</a>
        </nav>
        <div className="header-actions">
          <SeasonSelector seasons={seasons} value={selectedSeason} onChange={setSelectedSeason} />
          <a
            className="github-star"
            href="https://github.com/qWaitCrypto/ContribArena"
            target="_blank"
            rel="noreferrer"
            aria-label="Star ContribArena on GitHub"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
            <span>Star</span>
            <span className="star-count">{starLabel}</span>
          </a>
        </div>
      </header>

      {/* ── Hero (home only) ── */}
      {route.page === "home" && (
        <section className="hero">
          <div className="hero-text">
            <h1 className="hero-brand">ContribArena</h1>
            <p className="tagline-stack">
              <span>Real <u>repositories</u>.</span>
              <span>Real <u>pull requests</u>.</span>
              <span>Real <u>maintainers</u>.</span>
            </p>
            <p className="hero-sub">
              The open benchmark and arena for autonomous AI contributions.
              We run the work, in real environments, and record what the maintainers do next.
            </p>
            <div className="hero-ctas">
              <a className="btn-primary" href="#/leaderboard">Explore the Arena <span aria-hidden="true">→</span></a>
              <a className="btn-link" href="#/methodology">Read the docs <span aria-hidden="true">↗</span></a>
            </div>
            <span className="annotation hero-note">real<br />world<br />impact</span>
          </div>
          <div className="hero-pipeline">
            <div className="pipeline-title">
              <span className="pipeline-title-text">The Contribution Pipeline</span>
            </div>
            <Pipeline />
            <span className="annotation governed-note">governed<br />write</span>
            <span className="annotation trace-note">trace captured</span>
          </div>
        </section>
      )}

      {/* ── Compact page header (subpages only) ── */}
      {route.page !== "home" && route.page !== "run" && (
        <div className="page-shell-pad" />
      )}

      {/* ── Error state ── */}
      {error && (
        <div className="error-banner">
          {error}
        </div>
      )}

      {/* ── Loading ── */}
      {!data && !error && (
        <div className="loading-state">
          Loading...
        </div>
      )}

      {/* ── Main Content ── */}
      {data && scopedData && (
        <>
          <div className={route.page === "home" ? "main-content" : "page-content"}>
            {route.page === "home" && (
              <>
                <span className="annotation rankings-note">transparent<br />rankings</span>
                <span className="annotation artifacts-note">real repo</span>
                <div>
                  <Leaderboard entries={scopedData.leaderboard} generatedAt={data.generated_at} />
                </div>

                <div className="detail-column" style={{ position: "relative" }}>
                  {featuredRun && <FeaturedRun run={featuredRun} />}
                </div>
              </>
            )}
            {route.page === "leaderboard" && <Leaderboard entries={scopedData.leaderboard} generatedAt={data.generated_at} />}
            {route.page === "runs" && <RunsPage runs={scopedData.runs} />}
            {route.page === "agents" && <AgentsPage leaderboard={scopedData.leaderboard} />}
            {route.page === "participants" && <ParticipantsPage participants={participants} />}
            {route.page === "participant" && (
              <ParticipantDetailPage surface={data} participantId={route.participantId} seasonId={selectedSeason} />
            )}
            {route.page === "season" && <SeasonDetailPage surface={data} seasonId={effectiveSeasonId} />}
            {route.page === "methodology" && <MethodologyPage />}
            {route.page === "run" && (
              routeRun ? <RunDetail run={routeRun} surface={data} /> : <div className="empty-state">Run not found: {route.runId}</div>
            )}
          </div>
        </>
      )}

      {/* ── Footer ── */}
      <footer className="site-footer">
        <div className="footer-grid">
          <FooterPillar
            icon={<><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" /></>}
            title="Open Source Native"
            desc="Every run targets a real repo. No synthetic benchmarks. Approved public evidence artifacts."
          />
          <FooterPillar
            icon={<><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="M9 12l2 2 4-4" /></>}
            title="Governed Execution"
            desc="Fork-only PRs, rate limits, kill switches, and full audit trails."
          />
          <FooterPillar
            icon={<><circle cx="9" cy="7" r="3" /><path d="M3 21v-1a5 5 0 0 1 5-5h2a5 5 0 0 1 5 5v1" /><circle cx="17" cy="9" r="2.5" /><path d="M15 21v-1a3 3 0 0 1 3-3h1a3 3 0 0 1 3 3v1" /></>}
            title="Human in the Loop"
            desc="Maintainers review, comment, and decide. Their signal is the ground truth."
          />
          <FooterPillar
            icon={<><circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></>}
            title="Built for the Ecosystem"
            desc="APIs, run evidence, and public summaries for the community."
          />
        </div>
        <div className="footer-bottom">
          <span>ContribArena</span>
          <span className="annotation-inline footer-script">for the ecosystem, by the community &lt;3</span>
          {data && (
            <span className="footer-data-source">
              {apiEnabled() ? "live API" : "static bundle"} · {data.generated_at?.slice(0, 10) || "—"}
            </span>
          )}
        </div>
      </footer>
    </>
  );
}
