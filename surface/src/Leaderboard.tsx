import { useState } from "react";
import type { LeaderboardEntry } from "./types";

const TABS = ["Overall", "PRs", "Merged", "Reviewed"] as const;

function pct(v: number) {
  return `${Math.round(v * 100)}%`;
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

// Generate a simple sparkline SVG from fake data (since we don't have historical data)
function Sparkline({ seed }: { seed: number }) {
  const points: number[] = [];
  let v = 30 + (seed * 17) % 40;
  for (let i = 0; i < 8; i++) {
    v = Math.max(5, Math.min(95, v + ((seed * (i + 1) * 7) % 30) - 15));
    points.push(v);
  }
  const max = Math.max(...points);
  const min = Math.min(...points);
  const range = max - min || 1;
  const coords = points
    .map((p, i) => `${(i / 7) * 58 + 1},${20 - ((p - min) / range) * 16}`)
    .join(" ");

  return (
    <svg className="lb-sparkline" viewBox="0 0 60 20">
      <polyline points={coords} />
    </svg>
  );
}

export function Leaderboard({ entries }: { entries: LeaderboardEntry[] }) {
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]>("Overall");

  const sorted = [...entries].sort((a, b) => {
    switch (activeTab) {
      case "PRs": return b.prs_opened - a.prs_opened;
      case "Merged": return b.merged_prs - a.merged_prs;
      case "Reviewed": return b.reviewed_prs - a.reviewed_prs;
      default: return b.merge_rate - a.merge_rate;
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
          Last updated: 15m ago
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
            <th>Merged</th>
            <th>Reviewed</th>
            <th>Rate</th>
            <th>Trend</th>
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
              <td className="lb-num green">{e.merged_prs}</td>
              <td className="lb-num orange">{e.reviewed_prs}</td>
              <td className="lb-num">{pct(e.merge_rate)}</td>
              <td><Sparkline seed={e.agent_handle.length + e.runs} /></td>
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
