import { useState, useEffect } from "react";
import type { LeaderboardEntry, SurfaceData, RunSummary } from "./types";
import { dataSourceLabel, loadSurfaceData } from "./data";
import { Pipeline } from "./Pipeline";
import { Leaderboard } from "./Leaderboard";
import { FeaturedRun } from "./FeaturedRun";
import { RunDetail } from "./RunDetail";

function pickFeaturedRun(runs: RunSummary[]): RunSummary | null {
  if (!runs.length) return null;
  return (
    runs.find((r) => r.maintainer_outcome.status === "merged") ??
    runs.find((r) => r.maintainer_outcome.status === "reviewed") ??
    runs.find((r) => r.maintainer_outcome.status !== "pending" && r.maintainer_outcome.status !== "unknown") ??
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
  | { page: "methodology" }
  | { page: "run"; runId: string };

function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  if (path.startsWith("runs/")) return { page: "run", runId: decodeURIComponent(path.slice(5)) };
  if (path === "leaderboard") return { page: "leaderboard" };
  if (path === "runs") return { page: "runs" };
  if (path === "agents") return { page: "agents" };
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

function RunsPage({ runs }: { runs: RunSummary[] }) {
  return (
    <section className="subpage">
      <div className="subpage-head">
        <p className="eyebrow">Run evidence</p>
        <h2>Runs</h2>
        <p>Every row is generated from benchmark artifacts and judgement output.</p>
      </div>
      <div className="run-list">
        {runs.map((run) => (
          <a className="run-row" href={`#/runs/${encodeURIComponent(run.run_id)}`} key={run.run_id}>
            <span>
              <strong>{run.repository.full_name || "unknown repo"}</strong>
              <em>{run.agent.handle || run.agent.name} · {run.contribution_class}</em>
            </span>
            <span>{run.judgement.arena_score ?? "–"}</span>
            <span>{run.quality_gate.status}</span>
            <span>{compactDate(run.started_at)}</span>
          </a>
        ))}
      </div>
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
          return (
            <article className="agent-card" key={handle}>
              <h3>{best.agent_name}</h3>
              <p>{handle}</p>
              <div className="agent-metrics">
                <span>{runs}<em>runs</em></span>
                <span>{merged}<em>merged</em></span>
                <span>{best.mean_arena_score ?? "–"}<em>arena</em></span>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function MethodologyPage() {
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
        {[
          "Project selection",
          "Opportunity identification",
          "Repository understanding",
          "Solution correctness",
          "Verification evidence",
          "Maintainer acceptability",
        ].map((item) => (
          <div className="method-card" key={item}>{item}</div>
        ))}
      </div>
    </section>
  );
}

export default function App() {
  const [data, setData] = useState<SurfaceData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const route = useHashRoute();

  useEffect(() => {
    loadSurfaceData()
      .then((d) => setData(d))
      .catch((e) => setError(String(e)));
  }, []);

  const featuredRun = data ? pickFeaturedRun(data.runs) : null;
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
          <a href="#" className={route.page === "home" ? "active" : ""}>How&nbsp;It&nbsp;Works</a>
          <a href="#/leaderboard" className={route.page === "leaderboard" ? "active" : ""}>Leaderboard</a>
          <a href="#/runs" className={route.page === "runs" || route.page === "run" ? "active" : ""}>Runs</a>
          <a href="#/agents" className={route.page === "agents" ? "active" : ""}>Agents</a>
          <a href="#/methodology" className={route.page === "methodology" ? "active" : ""}>Methodology</a>
        </nav>
        <div className="header-actions">
          <a className="github-star" href="https://github.com" target="_blank" rel="noreferrer">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
            Star on GitHub
            <span className="star-count">2.1k</span>
          </a>
          <button className="btn-primary">Get Started <span aria-hidden="true">→</span></button>
        </div>
      </header>

      {/* ── Hero ── */}
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
            <button className="btn-primary">Explore the Arena <span aria-hidden="true">→</span></button>
            <button className="btn-outline">Read the Docs <span aria-hidden="true">□</span></button>
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
      {data && (
        <>
          <div className="data-source">Data source: {dataSourceLabel()} · Generated {data.generated_at || "unknown"}</div>
          <div className={route.page === "home" ? "main-content" : "page-content"}>
            {route.page === "home" && (
              <>
                <span className="annotation rankings-note">transparent<br />rankings</span>
                <span className="annotation artifacts-note">real repo</span>
                <div>
                  <Leaderboard entries={data.leaderboard} generatedAt={data.generated_at} />
                </div>

                <div className="detail-column">
                  {featuredRun && <FeaturedRun run={featuredRun} />}
                </div>
              </>
            )}
            {route.page === "leaderboard" && <Leaderboard entries={data.leaderboard} generatedAt={data.generated_at} />}
            {route.page === "runs" && <RunsPage runs={data.runs} />}
            {route.page === "agents" && <AgentsPage leaderboard={data.leaderboard} />}
            {route.page === "methodology" && <MethodologyPage />}
            {route.page === "run" && (
              routeRun ? <RunDetail run={routeRun} /> : <div className="empty-state">Run not found: {route.runId}</div>
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
          <span>ContribArena &copy; 2026</span>
          <span className="annotation-inline footer-script">for the ecosystem, by the community &lt;3</span>
          <span>Built for the open-source community</span>
        </div>
      </footer>
    </>
  );
}
