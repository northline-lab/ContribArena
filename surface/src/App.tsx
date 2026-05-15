import { useState, useEffect } from "react";
import type { SurfaceData, RunSummary } from "./types";
import { loadSurfaceData } from "./data";
import { Pipeline } from "./Pipeline";
import { Leaderboard } from "./Leaderboard";
import { RunDetail } from "./RunDetail";

function timeAgo(iso: string) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diff / 3600000);
  if (h < 1) return `${Math.floor(diff / 60000)}m ago`;
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function RunListItem({ run, selected, onClick }: { run: RunSummary; selected: boolean; onClick: () => void }) {
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 12px",
        borderRadius: "var(--radius)",
        border: selected ? "1.5px solid var(--ink)" : "1px solid var(--border-light)",
        background: selected ? "white" : "transparent",
        cursor: "pointer",
        transition: "border-color 0.15s, background 0.15s",
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)" }}>
          {run.repository.full_name}
        </div>
        <div style={{ fontSize: 10, color: "var(--ink-faint)" }}>
          {run.agent.name} &middot; {timeAgo(run.started_at)}
        </div>
      </div>
      <span className={`badge badge-${run.maintainer_outcome.status}`}>
        {run.maintainer_outcome.status}
      </span>
    </div>
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

function heroPipeline(runs: RunSummary[]) {
  if (!runs.length) return [];
  return runs.reduce((best, r) =>
    r.pipeline.length >= best.pipeline.length ? r : best
  ).pipeline;
}

export default function App() {
  const [data, setData] = useState<SurfaceData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    loadSurfaceData()
      .then((d) => {
        setData(d);
        if (d.runs.length) setSelectedId(d.runs[0].run_id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const selectedRun = data?.runs.find((r) => r.run_id === selectedId) ?? null;

  return (
    <div>
      {/* ── Header ── */}
      <header className="site-header">
        <a className="logo" href="/">
          <span className="logo-mark">CA</span>
          ContribArena
        </a>
        <nav className="nav">
          <a href="#" className="active">How It Works</a>
          <a href="#">Leaderboard</a>
          <a href="#">Datasets</a>
          <a href="#">Docs</a>
          <a href="#">Lab Notes</a>
          <a href="#">About</a>
        </nav>
        <div className="header-actions">
          <a className="github-star" href="https://github.com" target="_blank" rel="noreferrer">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
            Star on GitHub
          </a>
          <button className="btn-primary">Get Started</button>
        </div>
      </header>

      {/* ── Hero ── */}
      <section className="hero">
        <div className="hero-text" style={{ position: "relative" }}>
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
            <button className="btn-primary">Explore Runs</button>
            <button className="btn-outline">Read the Docs</button>
          </div>
          <span className="annotation" style={{ top: -8, left: -10, transform: "rotate(-4deg)", fontSize: 16 }}>
            real world impact ↓
          </span>
        </div>
        <div className="hero-pipeline" style={{ position: "relative" }}>
          <div className="pipeline-title">
            <span className="pipeline-title-text">The Contribution Pipeline</span>
          </div>
          {data && <Pipeline stages={heroPipeline(data.runs)} />}
          <span className="annotation" style={{ top: -8, right: 0, transform: "rotate(-2deg)", fontSize: 16 }}>
            governed write ↓
          </span>
          <span className="annotation" style={{ bottom: 8, left: 0, transform: "rotate(1.5deg)", fontSize: 15 }}>
            trace captured ✓
          </span>
        </div>
      </section>

      {/* ── Error state ── */}
      {error && (
        <div style={{ color: "var(--accent-red)", padding: "12px 32px", background: "var(--red-bg)", margin: "0 32px", borderRadius: "var(--radius)" }}>
          {error}
        </div>
      )}

      {/* ── Loading ── */}
      {!data && !error && (
        <div style={{ color: "var(--ink-faint)", padding: "60px 0", textAlign: "center" }}>
          Loading...
        </div>
      )}

      {/* ── Main Content ── */}
      {data && (
        <div className="main-content" style={{ position: "relative" }}>
          <span className="annotation" style={{ top: -22, left: 14, transform: "rotate(-2deg)", fontSize: 17 }}>
            transparent rankings ↓
          </span>
          <span className="annotation" style={{ top: -22, right: 14, transform: "rotate(2deg)", fontSize: 17 }}>
            full trace & artifacts →
          </span>
          <div>
            <Leaderboard entries={data.leaderboard} />

            {/* Run list below leaderboard */}
            <div style={{ marginTop: 14, position: "relative" }}>
              <div className="section-header">
                <div className="section-title">Recent Runs</div>
                <div className="section-meta">{data.runs.length} runs</div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {data.runs.map((r) => (
                  <RunListItem
                    key={r.run_id}
                    run={r}
                    selected={r.run_id === selectedId}
                    onClick={() => setSelectedId(r.run_id)}
                  />
                ))}
              </div>
            </div>
          </div>

          <div style={{ position: "relative" }}>
            {selectedRun && <RunDetail run={selectedRun} />}
          </div>
        </div>
      )}

      {/* ── Footer ── */}
      <footer className="site-footer">
        <div className="footer-grid" style={{ position: "relative" }}>
          <span className="annotation" style={{ top: -32, left: "50%", transform: "translateX(-50%) rotate(-2deg)", fontSize: 18 }}>
            ↓ what makes this different
          </span>
          <FooterPillar
            icon={<><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" /></>}
            title="Open Source Native"
            desc="Every run targets a real repo. No synthetic benchmarks. All artifacts are public."
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
            desc="Datasets, leaderboards, and insights — all open for the community."
          />
        </div>
        <div className="footer-bottom">
          <span>ContribArena &copy; 2026</span>
          <span className="annotation-inline" style={{ transform: "rotate(-1deg)" }}>for the ecosystem, by the community</span>
          <span>Built for the open-source community</span>
        </div>
      </footer>
    </div>
  );
}
