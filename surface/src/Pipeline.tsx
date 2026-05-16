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

const STATIC_STAGES = [
  { id: "agent", title: "AI Agent", desc: "Picks up issue", tag: "LLM", tagColor: "blue" },
  { id: "repo_discovery", title: "Discovery", desc: "Finds repos", tag: "API", tagColor: "purple" },
  { id: "workspace", title: "Workspace", desc: "Isolated env", tag: "Docker", tagColor: "teal" },
  { id: "patch_diff", title: "Patch", desc: "Code changes", tag: "diff", tagColor: "default" },
  { id: "quality_gate", title: "Quality", desc: "Lint & test", tag: "Gate", tagColor: "orange" },
  { id: "pull_request", title: "PR", desc: "Opens PR", tag: "GitHub", tagColor: "blue" },
  { id: "maintainer_outcome", title: "Maintainer", desc: "Reviews", tag: "", tagColor: "default" },
];

const STATUS_LABELS = [
  { key: "merged", label: "Merged" },
  { key: "reviewed", label: "Reviewed" },
  { key: "changes_requested", label: "Changes" },
  { key: "closed", label: "Closed" },
];

export function Pipeline() {
  return (
    <div className="pipeline">
      {STATIC_STAGES.map((stage, i) => {
        const icon = STAGE_ICONS[stage.id];
        const isLast = i === STATIC_STAGES.length - 1;
        return (
          <div key={stage.id} className="pipeline-stage">
            <div className="stage-card">
              <span className="stage-number">{i + 1}</span>
              <span className="stage-icon-box">{icon}</span>
              <span className="stage-title">{stage.title}</span>
              <span className="stage-desc">{stage.desc}</span>
              {stage.id === "maintainer_outcome" ? (
                <div className="status-dots">
                  {STATUS_LABELS.map((s) => (
                    <span key={s.key} className={`status-dot ${s.key}`}>{s.label}</span>
                  ))}
                </div>
              ) : stage.tag ? (
                <span className={`stage-tag stage-tag-${stage.tagColor}`}>{stage.tag}</span>
              ) : null}
            </div>
            {!isLast && (
              <span className="stage-arrow">
                <svg viewBox="0 0 12 12"><path d="M2 6h8M7 3l3 3-3 3" /></svg>
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
