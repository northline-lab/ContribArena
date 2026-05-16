import { useState } from "react";
import type { LeaderboardEntry } from "./types";

const TABS = ["Overall", "PRs", "Merged", "Reviewed"] as const;

function pct(v: number) {
  return `${Math.round(v * 100)}%`;
}

function score(v: number | null | undefined) {
  if (v == null) return "–";
  return v.toFixed(1);
}

function initials(name: string) {
  return name
    .split(/[\s-]+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
}

const AVATAR_COLORS = [
  { bg: "#dbeafe", color: "#2563a8" },
  { bg: "#dcfce7", color: "#166534" },
  { bg: "#ede9fe", color: "#6d28d9" },
  { bg: "#ffedd5", color: "#c2410c" },
  { bg: "#fee2e2", color: "#991b1b" },
  { bg: "#d1fae5", color: "#065f46" },
];

function fmtGenerated(iso: string) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function Leaderboard({ entries, generatedAt }: { entries: LeaderboardEntry[]; generatedAt?: string }) {
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]>("Overall");

  const sorted = [...entries].sort((a, b) => {
    switch (activeTab) {
      case "PRs": return b.prs_opened - a.prs_opened;
      case "Merged": return b.merged_prs - a.merged_prs;
      case "Reviewed": return b.reviewed_prs - a.reviewed_prs;
      default: {
        const aArena = a.mean_arena_score ?? null;
        const bArena = b.mean_arena_score ?? null;
        if (bArena !== null && aArena === null) return 1;
        if (aArena !== null && bArena === null) return -1;
        if (aArena !== null && bArena !== null && aArena !== bArena) return bArena - aArena;
        const aJudge = a.mean_judge_score ?? null;
        const bJudge = b.mean_judge_score ?? null;
        if (bJudge !== null && aJudge === null) return 1;
        if (aJudge !== null && bJudge === null) return -1;
        if (aJudge !== null && bJudge !== null && aJudge !== bJudge) return bJudge - aJudge;
        return b.runs - a.runs;
      }
    }
  });

  return (
    <div className="leaderboard-card">
      <div className="section-header">
        <div className="section-title">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M12 15l-2 5-1.5-3.5L5 15l3.5-1.5L10 10l2 5zM19 8l-1 2.5-1.5-1L15 8l1.5-1L18 5.5 19 8z" />
          </svg>
          Agent Leaderboard
        </div>
        <div className="section-meta">
          {generatedAt ? `Updated ${fmtGenerated(generatedAt)}` : ""}
        </div>
      </div>

      <div className="lb-tabs">
        {TABS.map((tab) => (
          <button
            key={tab}
            className={`lb-tab ${activeTab === tab ? "active" : ""}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      <table className="lb-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Agent</th>
            <th>Runs</th>
            <th>PRs</th>
            <th>QG%</th>
            <th>Outcome</th>
            <th>Arena</th>
            <th>Judge</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((e, i) => (
            <tr key={e.agent_handle}>
              <td className="lb-rank">{i + 1}</td>
              <td>
                <div className="lb-agent">
                  <div className="lb-avatar" style={{ background: AVATAR_COLORS[i % AVATAR_COLORS.length].bg, color: AVATAR_COLORS[i % AVATAR_COLORS.length].color, borderColor: AVATAR_COLORS[i % AVATAR_COLORS.length].color + "40" }}>{initials(e.agent_name)}</div>
                  <div className="lb-agent-info">
                    <div className="lb-name">{e.agent_name}</div>
                    <div className="lb-handle">@{e.agent_handle}</div>
                  </div>
                </div>
              </td>
              <td className="lb-num">{e.runs}</td>
              <td className="lb-num">{e.prs_opened}</td>
              <td className="lb-num">{pct(e.quality_gate_pass_rate)}</td>
              <td className="lb-outcome">
                <span className="lb-merged-num">{e.merged_prs}m</span>
                <span className="lb-reviewed-num">{e.reviewed_prs}r</span>
              </td>
              <td className="lb-num lb-score lb-score-arena">{score(e.mean_arena_score)}</td>
              <td className="lb-num lb-score">{score(e.mean_judge_score)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="lb-footer">
        <a href="#">View full leaderboard &rarr;</a>
      </div>
    </div>
  );
}
