import { useEffect, useMemo, useState } from "react";
import { loadRunEvidence, runEvidenceFromSurface } from "./data";
import type { AssistantUpdate, DiscoveryCall, PhaseHistoryItem, RunSummary, SelfReview, SurfaceData, ToolViolation } from "./types";

const KIND_VARIANT: Record<string, string> = {
  intent: "blue",
  decision: "purple",
  verification: "green",
  review_response: "orange",
  blocker: "red",
  observation: "teal",
};

function kindVariant(kind?: string): string {
  if (!kind) return "blue";
  return KIND_VARIANT[kind] ?? "blue";
}

const STAGE_LABELS: Record<string, string> = {
  agent: "Planning",
  repo_discovery: "Repo Found",
  workspace: "Workspace",
  patch_diff: "Patch Diff",
  quality_gate: "Quality Gate",
  pull_request: "PR Created",
  maintainer_outcome: "Maintainer",
};

const ARTIFACT_ICONS: Record<string, React.ReactNode> = {
  json: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M10 13l-1 4 1 4M14 13l1 4-1 4" />
    </svg>
  ),
  diff: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M12 18v-6M9 15h6" />
    </svg>
  ),
  md: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M9 13l2 2 4-4" />
    </svg>
  ),
  default: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" />
    </svg>
  ),
};

function getArtifactIcon(name: string) {
  if (name.endsWith(".json")) return ARTIFACT_ICONS.json;
  if (name.endsWith(".diff") || name.endsWith(".patch")) return ARTIFACT_ICONS.diff;
  if (name.endsWith(".md")) return ARTIFACT_ICONS.md;
  return ARTIFACT_ICONS.default;
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

const EMPTY_EVIDENCE = { discovery: [], assistantUpdates: [], selfReview: [], phaseHistory: [], toolViolations: [] };

function shortJson(value: unknown): string {
  if (value == null || value === "") return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function EmptyEvidence({ label }: { label: string }) {
  return <div className="evidence-empty">{label}</div>;
}

export function RunDetail({ run, surface }: { run: RunSummary; surface?: SurfaceData }) {
  const pr = run.pull_request || { url: "", number: null, state: "unknown" };
  const mo = run.maintainer_outcome || { status: "unknown", observed_at: "", source: "none" };
  const qg = run.quality_gate || { status: "unknown", warnings: [] };
  const judgement = run.judgement || { status: "unknown", judge_score: null, real_world_adjustment: 0, arena_score: null, rubric_summary: [], source_artifacts: [] };
  const publicArtifacts = (run.artifacts || []).filter((a) => a.visibility === "public");
  const pipeline = run.pipeline || [];
  const [evidenceState, setEvidenceState] = useState<{
    runId: string;
    loading: boolean;
    discovery: DiscoveryCall[];
    assistantUpdates: AssistantUpdate[];
    selfReview: SelfReview[];
    phaseHistory: PhaseHistoryItem[];
    toolViolations: ToolViolation[];
  }>(() => ({ runId: run.run_id, loading: false, ...(surface ? runEvidenceFromSurface(surface, run.run_id) : EMPTY_EVIDENCE) }));

  useEffect(() => {
    if (surface) return;
    let cancelled = false;
    loadRunEvidence(run.run_id)
      .then((payload) => {
        if (!cancelled) setEvidenceState({ runId: run.run_id, loading: false, ...payload });
      })
      .catch(() => {
        if (!cancelled) setEvidenceState({ runId: run.run_id, loading: false, ...EMPTY_EVIDENCE });
      });
    return () => {
      cancelled = true;
    };
  }, [run.run_id, surface]);
  const evidence = surface
    ? { runId: run.run_id, loading: false, ...runEvidenceFromSurface(surface, run.run_id) }
    : evidenceState.runId === run.run_id
      ? evidenceState
      : { runId: run.run_id, loading: true, ...EMPTY_EVIDENCE };

  return (
    <div className="run-detail-card">
      {/* Header */}
      <div className="run-detail-header">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6" />
            <path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18" />
            <path d="M4 22h16" />
            <path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22" />
            <path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22" />
            <path d="M18 2H6v7a6 6 0 0 0 12 0V2z" />
          </svg>
          <span className="run-detail-title">Run Detail</span>
          <span className="run-id-badge">
            Run #{run.run_id.slice(0, 8)}
          </span>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ cursor: "pointer", opacity: 0.5 }}>
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        </div>
        {mo.status === "merged" ? (
          <span className="badge badge-merged" style={{ display: "flex", alignItems: "center", gap: 4 }}>
            Merged
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M20 6L9 17l-5-5" />
            </svg>
          </span>
        ) : (
          <span className={`badge badge-${mo.status}`}>{mo.status}</span>
        )}
      </div>

      {/* Meta row */}
      <div className="run-meta-row">
        <span>Agent <strong>{run.agent.name}</strong></span>
        <span className="sep">·</span>
        <span>Participant <a href={`#/participants/${encodeURIComponent(run.agent.participant_id || run.agent.handle || run.agent.name)}`}>{run.agent.participant_id || "unranked"}</a></span>
        <span className="sep">·</span>
        <span>Wake <strong>{run.wake_source}</strong></span>
        <span className="sep">·</span>
        <span>Repo <a href={run.repository.url} target="_blank" rel="noreferrer">{run.repository.full_name}</a></span>
        <span className="sep">·</span>
        <span>Started {fmt(run.started_at)}</span>
        <span className="sep">·</span>
        <span>Duration {dur(run.duration_seconds)}</span>
      </div>

      <div className="score-breakdown">
        <span>Judge <strong>{judgement.judge_score ?? "not judged"}</strong></span>
        <span>Adjustment <strong>{judgement.real_world_adjustment}</strong></span>
        <span>Arena <strong>{judgement.arena_score ?? "not judged"}</strong></span>
        <span className={`badge badge-${judgement.status}`}>{judgement.status}</span>
      </div>

      {/* Mini pipeline timeline with timestamps */}
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

      {/* Artifacts */}
      {publicArtifacts.length > 0 && (
        <div className="artifacts-section">
          <div className="artifacts-title">Artifacts</div>
          <div className="artifact-grid">
            {publicArtifacts.map((a) => (
              <div key={a.name} className="artifact-card">
                <svg className="artifact-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  {getArtifactIcon(a.name)}
                </svg>
                <span className="artifact-name">
                  {a.url ? (
                    <a href={a.url} target="_blank" rel="noreferrer">{a.name}</a>
                  ) : a.name}
                </span>
                <span className="artifact-size">{formatBytes(a.size_bytes)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="evidence-grid">
        <CommentarySection updates={evidence.assistantUpdates} />

        <section className="evidence-panel">
          <h3>Phase History</h3>
          {evidence.phaseHistory.length ? (
            <div className="evidence-list">
              {evidence.phaseHistory.map((item, idx) => (
                <div className="evidence-row" key={`${item.seq ?? idx}-${item.event_type ?? ""}`}>
                  <strong>{item.phase || "unknown"}{item.sub_phase ? `/${item.sub_phase}` : ""}</strong>
                  <span>{item.event_type || item.scope || "event"}</span>
                  <em>{fmt(String(item.created_at || ""))}</em>
                </div>
              ))}
            </div>
          ) : <EmptyEvidence label="No phase history projected for this run." />}
        </section>

        <section className="evidence-panel">
          <h3>Discovery Calls</h3>
          {evidence.discovery.length ? (
            <div className="evidence-list">
              {evidence.discovery.map((item, idx) => (
                <div className="evidence-row evidence-row-wide" key={idx}>
                  <strong>{item.github_query_string || item.query || "query unavailable"}</strong>
                  <span>{shortJson(item.filters_resolved)}</span>
                  <em>{item.returned_count ?? 0} shown / {item.total_hits ?? "?"} hits</em>
                </div>
              ))}
            </div>
          ) : <EmptyEvidence label="No discovery_log.jsonl entries projected for this run." />}
        </section>

        <section className="evidence-panel">
          <h3>Self Pre-Submission Review</h3>
          {evidence.selfReview.length ? (
            <div className="evidence-list">
              {evidence.selfReview.map((item, idx) => (
                <div className="evidence-row evidence-row-wide" key={idx}>
                  <strong>Self pre-submission review by {item.reviewer_model || item.reviewer_role || "model"}</strong>
                  <span>Severity: {item.severity || "unknown"}</span>
                  <em>{shortJson(item.concerns || item.agent_response)}</em>
                </div>
              ))}
            </div>
          ) : <EmptyEvidence label="No self-review artifact projected for this run." />}
        </section>

        <section className="evidence-panel">
          <h3>Tool Violations</h3>
          {evidence.toolViolations.length ? (
            <div className="evidence-list">
              {evidence.toolViolations.map((item, idx) => (
                <div className="evidence-row" key={`${item.seq ?? idx}-${item.tool ?? ""}`}>
                  <strong>{item.tool || "tool"}</strong>
                  <span>{item.phase || "unknown"}{item.sub_phase ? `/${item.sub_phase}` : ""}</span>
                  <em>{item.recovery_kind || "violation"}</em>
                </div>
              ))}
            </div>
          ) : <EmptyEvidence label="No phase/tool violations recorded." />}
        </section>
      </div>

      {/* Log + Maintainer comment side by side */}
      <div className="run-bottom-row">
        {run.terminal_reason && (
          <div className="log-snippet" style={{ flex: 1 }}>
            <div style={{ marginBottom: 4, fontSize: 10, color: "#6a9955", fontWeight: 600 }}>Log Snippet</div>
            <div>
              <span className="log-time">[{fmtTime(run.started_at)}]</span>{" "}
              <span className="log-level">INFO</span> {run.terminal_reason}
            </div>
            {qg.warnings.map((w, i) => (
              <div key={i}>
                <span className="log-time">[{fmtTime(run.completed_at)}]</span>{" "}
                <span style={{ color: "#ce9178" }}>WARN</span> {w}
              </div>
            ))}
            {pr.url && (
              <div>
                <span className="log-time">[{fmtTime(run.completed_at)}]</span>{" "}
                <span className="log-level">INFO</span> PR{pr.number ? ` #${pr.number}` : ""} created
              </div>
            )}
          </div>
        )}

        {mo.status !== "pending" && mo.status !== "unknown" && (
          <div style={{ position: "relative", flex: "0 0 auto", minWidth: 160 }}>
            <span className="annotation" style={{ top: -22, left: 0, transform: "rotate(-3deg)", fontSize: 15, position: "absolute" }}>
              human reviewed
            </span>
            <div className="maintainer-comment">
              <div className="comment-author">Maintainer Outcome</div>
              <div style={{ marginTop: 4 }}>
                Status: <span className={`badge badge-${mo.status}`}>{mo.status}</span>
              </div>
              {mo.observed_at && (
                <div style={{ marginTop: 4, fontSize: 10, color: "var(--ink-faint)" }}>
                  {fmt(mo.observed_at)}
                </div>
              )}
            </div>
            <div style={{ marginTop: 6, fontSize: 11 }}>
              <span className="annotation-inline" style={{ fontSize: 14, transform: "rotate(-1deg)", display: "inline-block" }}>
                real repo:
              </span>{" "}
              <a href={run.repository.url} target="_blank" rel="noreferrer" style={{ fontWeight: 600, fontSize: 12 }}>
                {run.repository.full_name}
              </a>
            </div>
          </div>
        )}
      </div>

      {/* PR link */}
      {pr.url && (
        <div style={{ marginTop: 10, fontSize: 11 }}>
          <a href={pr.url} target="_blank" rel="noreferrer">
            View PR #{pr.number} on GitHub →
          </a>
        </div>
      )}
    </div>
  );
}

const COMMENTARY_PREVIEW = 6;

function CommentarySection({ updates }: { updates: AssistantUpdate[] }) {
  const [expanded, setExpanded] = useState(false);
  const total = updates.length;
  const items = useMemo(() => (expanded ? updates : updates.slice(0, COMMENTARY_PREVIEW)), [updates, expanded]);
  const collapsedRemaining = Math.max(0, total - COMMENTARY_PREVIEW);

  return (
    <section className="evidence-panel commentary-panel">
      <div className="commentary-head">
        <h3>Agent Commentary</h3>
        <span className="commentary-count">{total ? `${total} update${total === 1 ? "" : "s"}` : ""}</span>
      </div>
      {total ? (
        <>
          <ol className="commentary-list">
            {items.map((item, idx) => (
              <CommentaryCard key={`${item.ts ?? ""}-${idx}`} update={item} />
            ))}
          </ol>
          {collapsedRemaining > 0 && (
            <div className="commentary-more">
              <button
                type="button"
                className="commentary-more-btn"
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? "Show fewer" : `Show ${collapsedRemaining} more`}
              </button>
            </div>
          )}
        </>
      ) : (
        <div className="empty-state">
          <span>No visible agent updates were captured for this run.</span>
        </div>
      )}
    </section>
  );
}

function CommentaryCard({ update }: { update: AssistantUpdate }) {
  const variant = kindVariant(update.kind);
  const phase = update.phase || "run";
  const subPhase = update.sub_phase ? String(update.sub_phase) : "";
  const time = update.ts ? fmtTime(String(update.ts)) : "";
  const hidden = Number(update.hidden_dropped_count ?? 0);
  const text = (update.text || "").trim();
  return (
    <li className={`commentary-card commentary-card-${variant}`}>
      <span className="commentary-stripe" aria-hidden="true" />
      <div className="commentary-body">
        <div className="commentary-meta">
          <span className={`commentary-kind commentary-kind-${variant}`}>{update.kind || "intent"}</span>
          <span className="commentary-phase">
            {phase}{subPhase ? <span className="commentary-sub">/{subPhase}</span> : null}
          </span>
          {update.tool_name && (
            <span className="commentary-tool">→ {update.tool_name}</span>
          )}
          {time && <span className="commentary-time">{time}</span>}
        </div>
        {text ? (
          <p className="commentary-text">{text}</p>
        ) : (
          <p className="commentary-text commentary-text-empty">(no visible text — only hidden reasoning)</p>
        )}
        {(update.redacted || update.truncated || hidden > 0) && (
          <div className="commentary-chips">
            {update.redacted && <span className="commentary-chip commentary-chip-warn">redacted</span>}
            {update.truncated && <span className="commentary-chip">truncated</span>}
            {hidden > 0 && <span className="commentary-chip">+{hidden} hidden reasoning</span>}
          </div>
        )}
      </div>
    </li>
  );
}
