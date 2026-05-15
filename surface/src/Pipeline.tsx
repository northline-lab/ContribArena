import type { PipelineStage } from "./types";

const STAGE_ICONS: Record<string, React.ReactNode> = {
  agent: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M22 12h-3M5 12H2M19.07 4.93l-2.12 2.12M7.05 16.95l-2.12 2.12M19.07 19.07l-2.12-2.12M7.05 7.05L4.93 4.93" />
    </svg>
  ),
  repo_discovery: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.35-4.35" />
    </svg>
  ),
  workspace: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <path d="M3.27 6.96L12 12.01l8.73-5.05M12 22.08V12" />
    </svg>
  ),
  patch_diff: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M12 5v14M5 12h14" />
      <path d="M5 6h14M5 18h10" />
    </svg>
  ),
  quality_gate: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  ),
  pull_request: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="18" r="2.5" />
      <path d="M6 8.5v7M18 15.5v-7a3 3 0 0 0-3-3h-3" />
      <path d="M14 7.5L12 5.5L14 3.5" />
    </svg>
  ),
  maintainer_outcome: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="9" cy="7" r="3" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M3 21v-1a5 5 0 0 1 5-5h2a5 5 0 0 1 5 5v1M15 21v-1a3 3 0 0 1 3-3h1a3 3 0 0 1 3 3v1" />
    </svg>
  ),
};

const STAGE_META: Record<string, { label: string; desc: string; tag: string; tagColor: string }> = {
  agent:              { label: "AI Agent",          desc: "Plans and acts\nwith tools",                  tag: "LLM + Tools",        tagColor: "blue" },
  repo_discovery:     { label: "Repo\nDiscovery",   desc: "Finds real issues\n& opportunities",          tag: "GitHub API",         tagColor: "default" },
  workspace:          { label: "Docker\nWorkspace", desc: "Reproducible env\nper repository",            tag: "Isolated · Ephemeral", tagColor: "teal" },
  patch_diff:         { label: "Patch Diff",        desc: "Implements\nchange",                          tag: "git diff",           tagColor: "orange" },
  quality_gate:       { label: "Quality Gate",      desc: "Tests, lint, build,\npolicy checks",          tag: "Gated",              tagColor: "default" },
  pull_request:       { label: "Pull Request",      desc: "Creates PR with\ncontext & trace",            tag: "GitHub PR",          tagColor: "purple" },
  maintainer_outcome: { label: "Maintainer\nOutcome", desc: "Human reviewed.\nOutcome recorded.",        tag: "",                   tagColor: "default" },
};

const STAGE_ORDER = [
  "agent", "repo_discovery", "workspace", "patch_diff",
  "quality_gate", "pull_request", "maintainer_outcome",
];

const DOT_COLORS: Record<string, string> = {
  passed:  "#2563eb",
  running: "#d97706",
  failed:  "#dc2626",
  pending: "#d1d5db",
  blocked: "#9ca3af",
};

export function Pipeline({ stages }: { stages: PipelineStage[] }) {
  const byId = new Map(stages.map((s) => [s.stage_id, s]));

  return (
    <div style={{ position: "relative" }}>
      {/* Stage boxes row */}
      <div className="pipeline">
        {STAGE_ORDER.map((id, idx) => {
          const meta = STAGE_META[id];
          const stage = byId.get(id);
          const isActive = stage?.status === "passed" || stage?.status === "running";
          const isFinal = id === "maintainer_outcome";
          const tagColorClass = `stage-tag-${meta.tagColor}`;

          return (
            <div key={id} className="pipeline-stage">
              <div className="stage-number">{idx + 1}</div>
              <div className={`stage-icon-box ${isActive ? "active" : ""}`}>
                {STAGE_ICONS[id]}
              </div>
              <div className="stage-title">
                {meta.label.split("\n").map((line, i) => (
                  <span key={i}>{line}{i < meta.label.split("\n").length - 1 && <br />}</span>
                ))}
              </div>
              <div className="stage-desc">
                {meta.desc.split("\n").map((line, i) => (
                  <span key={i}>{line}{i < meta.desc.split("\n").length - 1 && <br />}</span>
                ))}
              </div>
              {meta.tag && (
                <div className={`stage-tag ${tagColorClass}`}>{meta.tag}</div>
              )}
              {isFinal && (
                <div className="status-dots">
                  <span className="status-dot merged">Merged</span>
                  <span className="status-dot reviewed">Reviewed</span>
                  <span className="status-dot changes_requested">Changes Requested</span>
                  <span className="status-dot closed">Closed</span>
                </div>
              )}
              {idx < STAGE_ORDER.length - 1 && (
                <>
                  <div className="stage-connector" />
                  <div className="stage-arrow">
                    <svg viewBox="0 0 14 14"><path d="M2 7h10M8 3l4 4-4 4" /></svg>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>

      {/* Dashed timeline row below stages */}
      <div className="pipeline-timeline">
        <div className="pipeline-timeline-track" />
        {STAGE_ORDER.map((id) => {
          const stage = byId.get(id);
          const color = DOT_COLORS[stage?.status ?? "pending"];
          return (
            <div key={id} className="pipeline-timeline-dot-wrap">
              <div
                className="pipeline-timeline-dot"
                style={{ background: color, borderColor: color === "#d1d5db" ? "#9ca3af" : color }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
