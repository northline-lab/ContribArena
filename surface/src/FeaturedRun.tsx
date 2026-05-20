import type { RunSummary } from "./types";

const STAGE_LABELS: Record<string, string> = {
  agent: "Planning",
  repo_discovery: "Repo Found",
  workspace: "Workspace",
  patch_diff: "Patch Diff",
  quality_gate: "Quality Gate",
  pull_request: "PR Created",
  maintainer_outcome: "Maintainer",
};

const ARTIFACT_ICON_PATHS: Record<string, React.ReactNode> = {
  json: <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M10 13l-1 4 1 4M14 13l1 4-1 4" />,
  diff: <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M12 18v-6M9 15h6" />,
  md: <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M9 13l2 2 4-4" />,
  default: <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8" />,
};

function getArtifactIconPath(name: string) {
  if (name.endsWith(".json")) return ARTIFACT_ICON_PATHS.json;
  if (name.endsWith(".diff") || name.endsWith(".patch")) return ARTIFACT_ICON_PATHS.diff;
  if (name.endsWith(".md")) return ARTIFACT_ICON_PATHS.md;
  return ARTIFACT_ICON_PATHS.default;
}

function fmt(iso: string) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtTime(iso: string) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit", minute: "2-digit",
  });
}

function dur(s: number) {
  if (!s) return "";
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

function formatBytes(bytes: number) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const DOT_STATUS_COLOR: Record<string, string> = {
  passed: "var(--accent-blue)",
  running: "var(--accent-orange)",
  failed: "var(--accent-red)",
  pending: "var(--border)",
  blocked: "var(--ink-faint)",
};

export function FeaturedRun({ run }: { run: RunSummary }) {
  const pr = run.pull_request || { url: "", number: null, state: "unknown" };
  const mo = run.maintainer_outcome || { status: "unknown", observed_at: "", source: "none" };
  const qg = run.quality_gate || { status: "unknown", warnings: [] };
  const judgement = run.judgement;
  const publicArtifacts = (run.artifacts || []).filter((a) => a.visibility === "public");
  const pipeline = run.pipeline || [];

  return (
    <div className="featured-run-card">
      <div className="featured-run-header">
        <div className="featured-run-title-row">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6" />
            <path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18" />
            <path d="M4 22h16" />
            <path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22" />
            <path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22" />
            <path d="M18 2H6v7a6 6 0 0 0 12 0V2z" />
          </svg>
          <span className="featured-run-label">Featured Run</span>
          <span className="run-id-badge">
            {run.run_id.slice(0, 8)}
          </span>
        </div>
        <span className={`badge badge-${mo.status}`}>{mo.status}</span>
      </div>

      <div className="run-meta-row">
        <span>Agent <strong>{run.agent.name}</strong></span>
        <span className="sep">·</span>
        <span>
          <a href={run.repository.url} target="_blank" rel="noreferrer">{run.repository.full_name}</a>
        </span>
        {run.contribution_class && run.contribution_class !== "unknown" && (
          <>
            <span className="sep">·</span>
            <span className="contrib-class-tag">{run.contribution_class}</span>
          </>
        )}
        <span className="sep">·</span>
        <span>{fmt(run.started_at)}</span>
        {run.duration_seconds > 0 && (
          <>
            <span className="sep">·</span>
            <span>{dur(run.duration_seconds)}</span>
          </>
        )}
      </div>

      <div className="run-timeline">
        {pipeline.map((stage, idx) => {
          const color = DOT_STATUS_COLOR[stage.status] ?? "var(--border)";
          return (
            <div key={stage.stage_id} style={{ display: "contents" }}>
              <div className="timeline-stage-wrap">
                <div
                  className="timeline-dot"
                  style={{ background: color, borderColor: color }}
                  title={`${STAGE_LABELS[stage.stage_id] ?? stage.stage_id}: ${stage.status}`}
                />
                <div className="timeline-stage-label">{STAGE_LABELS[stage.stage_id] ?? stage.stage_id}</div>
                {stage.started_at && (
                  <div className="timeline-stage-time">{fmtTime(stage.started_at)}</div>
                )}
              </div>
              {idx < pipeline.length - 1 && (
                <div className={`timeline-line ${stage.status === "passed" ? "passed" : ""}`} />
              )}
            </div>
          );
        })}
      </div>

      <div className="featured-run-status-row">
        <div className={`fr-status-chip fr-qg-${qg.status}`}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            {qg.status === "pass" && <path d="M9 12l2 2 4-4" />}
          </svg>
          Quality Gate: <strong>{qg.status}</strong>
        </div>

        {pr.url ? (
          <a href={pr.url} target="_blank" rel="noreferrer" className="fr-status-chip fr-pr-link">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="6" cy="6" r="2.5" /><circle cx="6" cy="18" r="2.5" /><circle cx="18" cy="18" r="2.5" />
              <path d="M6 8.5v7M18 15.5v-7a3 3 0 0 0-3-3h-3" />
            </svg>
            PR{pr.number ? ` #${pr.number}` : ""} · {pr.state}
          </a>
        ) : (
          <span className="fr-status-chip fr-pr-none">No PR</span>
        )}

        {mo.status !== "pending" && mo.status !== "unknown" && (
          <div className={`fr-status-chip badge badge-${mo.status}`}>
            Maintainer Outcome: <strong>{mo.status}</strong>
            {mo.observed_at && <span style={{ opacity: 0.7, marginLeft: 4 }}>{fmt(mo.observed_at)}</span>}
          </div>
        )}
      </div>

      {judgement && (
        <div className="fr-scores-row">
          {judgement.judge_score != null && (
            <div className="fr-score-chip">
              <span className="fr-score-label">Judge Score</span>
              <span className="fr-score-val">{judgement.judge_score.toFixed(1)}</span>
            </div>
          )}
          {judgement.arena_score != null && (
            <div className="fr-score-chip">
              <span className="fr-score-label">Arena Score</span>
              <span className="fr-score-val fr-score-arena">{judgement.arena_score.toFixed(1)}</span>
            </div>
          )}
          <span className={`badge badge-${judgement.status}`}>{judgement.status}</span>
        </div>
      )}

      {publicArtifacts.length > 0 && (
        <div className="artifacts-section">
          <div className="artifacts-title">Approved public evidence artifacts</div>
          <div className="artifact-grid">
            {publicArtifacts.map((a) => (
              <div key={a.name} className={`artifact-card ${!a.url ? "artifact-card-indexed" : ""}`}>
                <svg className="artifact-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  {getArtifactIconPath(a.name)}
                </svg>
                <span className="artifact-name">
                  {a.url ? (
                    <a href={a.url} target="_blank" rel="noreferrer">{a.name}</a>
                  ) : (
                    <span>{a.name}</span>
                  )}
                </span>
                {a.url && a.size_bytes > 0
                  ? <span className="artifact-size">{formatBytes(a.size_bytes)}</span>
                  : <span className="artifact-indexed-chip">indexed</span>
                }
              </div>
            ))}
          </div>
        </div>
      )}

      {(run.terminal_reason || qg.warnings.length > 0) && (
        <div className="fr-evidence-note">
          <span className="fr-evidence-label">run note</span>
          {run.terminal_reason && (
            <span className="fr-evidence-reason">{run.terminal_reason}</span>
          )}
          {qg.warnings.map((w, i) => (
            <span key={i} className="fr-evidence-warn">{w}</span>
          ))}
        </div>
      )}
    </div>
  );
}
