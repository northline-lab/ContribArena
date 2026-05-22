from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contribarena.engine.seasons import normalize_model_identity
from contribarena.engine.surface_indexer import (
    SURFACE_SCHEMA_VERSION,
    _apply_frozen_leaderboard,
    _leaderboard,
    _load_run_summaries,
    _merge_runtime_events,
    _merge_season_state,
    _merge_state_workspaces,
    _scheduler_events,
    _season_workspaces,
    _stats,
)


@dataclass(frozen=True)
class ReadModelRefreshResult:
    db_path: Path
    input_dir: Path
    runs_indexed: int
    public_artifacts_indexed: int
    skipped: list[str]
    generated_at: str


@dataclass(frozen=True)
class ReadModelStatus:
    db_path: Path
    input_dir: Path
    generated_at: str
    runs: int
    seasons: int
    agents: int
    judged_runs: int
    open_or_tracked_prs: int
    skipped: int


class SurfaceReadModel:
    """SQLite read model for frontend-facing benchmark data."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            _create_schema(db)

    def refresh_from_artifacts(self, input_dir: Path) -> ReadModelRefreshResult:
        input_dir.mkdir(parents=True, exist_ok=True)
        self.initialize()
        loaded_runs, skipped = _load_run_summaries(input_dir=input_dir, output_dir=self.db_path)
        loaded_runs = sorted(
            loaded_runs,
            key=lambda run: str(run.payload.get("started_at") or run.payload.get("run_id")),
            reverse=True,
        )
        public_runs: list[dict[str, Any]] = []
        artifact_rows: list[tuple[str, str, str, str, str]] = []
        phase_rows: list[tuple[str, int, str, str, str, str, str, str]] = []
        violation_rows: list[tuple[str, int, str, str, str, str, str]] = []
        discovery_rows: list[tuple[str, int, str, str, str, str, str]] = []
        assistant_update_rows: list[tuple[str, int, str, str, str, str, str, str, str]] = []
        scheduler_rows: list[tuple[str, str, str, str, str, str]] = []
        pr_rows: list[tuple[str, str, str, int, str, str]] = []
        workspace_rows: list[tuple[str, str, str, str, str]] = []
        for loaded in loaded_runs:
            run = _api_run(loaded.payload, loaded.run_dir, skipped)
            public_runs.append(run)
            run_id = str(run.get("run_id") or "")
            for artifact in run.get("artifacts", []):
                if not isinstance(artifact, dict):
                    continue
                artifact_rows.append(
                    (
                        run_id,
                        str(artifact.get("name") or ""),
                        str(artifact.get("visibility") or "internal"),
                        str((loaded.run_dir / str(artifact.get("name") or "")).resolve()),
                        json.dumps(artifact, ensure_ascii=True),
                    )
                )
            phase_rows.extend(_phase_history_rows(run_id, loaded.run_dir))
            violation_rows.extend(_tool_violation_rows(run_id, loaded.run_dir))
            discovery_rows.extend(_discovery_call_rows(run_id, loaded.run_dir))
            assistant_update_rows.extend(_assistant_update_rows(run_id, loaded.run_dir))
            scheduler_rows.extend(_scheduler_event_rows(run, loaded.run_dir))
            pr_rows.extend(_pr_lifecycle_rows(run, loaded.run_dir))
            workspace_rows.extend(_workspace_rows(run, loaded.run_dir))
        leaderboard = _apply_frozen_leaderboard(input_dir, _leaderboard(public_runs))
        stats = _stats(public_runs)
        seasons = _season_payload_rows(_merge_season_state(input_dir, _seasons_from_runs(public_runs)))
        participants = _participant_rows(public_runs)
        scheduler_rows.extend(
            _scheduler_event_tuple(row)
            for row in _merge_runtime_events(input_dir, _scheduler_events(loaded_runs))
            if str(row.get("status") or "") != "started"
        )
        workspace_rows = [
            _workspace_tuple(row)
            for row in _merge_state_workspaces(input_dir, _season_workspaces(loaded_runs))
        ]
        generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self._connect() as db:
            _replace_data(
                db=db,
                runs=public_runs,
                artifacts=artifact_rows,
                phase_history=phase_rows,
                tool_violations=violation_rows,
                discovery_calls=discovery_rows,
                assistant_updates=assistant_update_rows,
                scheduler_events=scheduler_rows,
                pr_lifecycle=pr_rows,
                season_workspaces=workspace_rows,
                seasons=seasons,
                participants=participants,
                leaderboard=leaderboard,
                stats=stats,
                skipped=skipped,
                generated_at=generated_at,
                input_dir=input_dir,
            )
        return ReadModelRefreshResult(
            db_path=self.db_path,
            input_dir=input_dir,
            runs_indexed=len(public_runs),
            public_artifacts_indexed=sum(1 for row in artifact_rows if row[2] == "public"),
            skipped=skipped,
            generated_at=generated_at,
        )

    def surface_bundle(self) -> dict[str, Any]:
        with self._connect() as db:
            runs = _payloads(db.execute("select payload_json from runs order by started_at desc, run_id"))
            seasons = _payloads(db.execute("select payload_json from seasons order by season_id"))
            participants = _payloads(db.execute("select payload_json from participants order by season_id, participant_id"))
            pr_lifecycle = _payloads(db.execute("select payload_json from pr_lifecycle order by season_id, participant_id, repository, number"))
            scheduler = _payloads(db.execute("select payload_json from scheduler_events order by created_at, participant_id"))
            workspaces = _payloads(db.execute("select payload_json from season_workspaces order by season_id, participant_id, repo_slug"))
            return {
                "schema_version": SURFACE_SCHEMA_VERSION,
                "generated_at": _meta(db, "generated_at"),
                "stats": _json_meta(db, "stats", default={}),
                "leaderboard": _json_meta(db, "leaderboard", default=[]),
                "runs": runs,
                "seasons": seasons,
                "participants": participants,
                "pr_lifecycle": pr_lifecycle,
                "scheduler": scheduler,
                "workspaces": workspaces,
                "assistant_updates": _group_payloads(
                    _payloads(db.execute("select payload_json from assistant_updates order by run_id, seq")),
                    "run_id",
                ),
                "skipped": _json_meta(db, "skipped", default=[]),
            }

    def seasons(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("select payload_json from seasons order by season_id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def stats(self, season_id: str | None = None) -> dict[str, Any]:
        runs = self.runs(season_id=season_id, limit=10_000, offset=0)
        return _stats(runs)

    def leaderboard(self, season_id: str | None = None) -> list[dict[str, Any]]:
        runs = self.runs(season_id=season_id, limit=10_000, offset=0)
        return _leaderboard(runs)

    def runs(
        self,
        *,
        season_id: str | None = None,
        status: str | None = None,
        agent: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if season_id:
            clauses.append("season_id = ?")
            params.append(season_id)
        if status:
            clauses.append("run_status = ?")
            params.append(status)
        if agent:
            clauses.append("(agent_handle = ? or agent_name = ? or participant_id = ?)")
            params.extend([agent, agent, agent])
        if query:
            clauses.append("payload_json like ?")
            params.append(f"%{query}%")
        where = f"where {' and '.join(clauses)}" if clauses else ""
        params.extend([max(1, min(limit, 500)), max(0, offset)])
        query = (
            "select payload_json from runs "
            f"{where} order by started_at desc, run_id limit ? offset ?"
        )
        with self._connect() as db:
            return [_annotate_ranking_state(run) for run in _payloads(db.execute(query, params))]

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("select payload_json from runs where run_id = ?", (run_id,)).fetchone()
        return _annotate_ranking_state(json.loads(row[0])) if row else None

    def participants(self, season_id: str | None = None) -> list[dict[str, Any]]:
        query = "select payload_json from participants"
        params: list[Any] = []
        if season_id:
            query += " where season_id = ?"
            params.append(season_id)
        query += " order by season_id, participant_id"
        with self._connect() as db:
            return _payloads(db.execute(query, params))

    def participant(self, participant_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "select payload_json from participants where participant_id = ?",
                (participant_id,),
            ).fetchone()
        if not row:
            return None
        item = json.loads(row[0])
        item["runs_detail"] = self.runs(agent=participant_id, limit=500, offset=0)
        item["pr_lifecycle"] = self.pr_lifecycle(participant_id=participant_id)
        return item

    def pr_lifecycle(
        self,
        *,
        season_id: str | None = None,
        participant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if season_id:
            clauses.append("season_id = ?")
            params.append(season_id)
        if participant_id:
            clauses.append("participant_id = ?")
            params.append(participant_id)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                f"select payload_json from pr_lifecycle {where} order by season_id, participant_id, repository, number",
                params,
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def discovery_calls(self, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "select payload_json from discovery_calls"
        params: list[Any] = []
        if run_id:
            query += " where run_id = ?"
            params.append(run_id)
        query += " order by run_id, seq"
        with self._connect() as db:
            return _payloads(db.execute(query, params))

    def self_review(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            row = db.execute(
                "select path from artifacts where run_id = ? and name = ?",
                (run_id, "phase_review_maintainer_review.jsonl"),
            ).fetchone()
        if not row:
            return []
        return _read_jsonl(Path(str(row[0])))

    def assistant_updates(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "select payload_json from assistant_updates where run_id = ? order by seq",
                (run_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def scheduler_events(self, season_id: str | None = None) -> list[dict[str, Any]]:
        query = "select payload_json from scheduler_events"
        params: list[Any] = []
        if season_id:
            query += " where season_id = ?"
            params.append(season_id)
        query += " order by created_at, participant_id"
        with self._connect() as db:
            return _payloads(db.execute(query, params))

    def season_workspaces(self, season_id: str | None = None) -> list[dict[str, Any]]:
        query = "select payload_json from season_workspaces"
        params: list[Any] = []
        if season_id:
            query += " where season_id = ?"
            params.append(season_id)
        query += " order by season_id, participant_id, repo_slug"
        with self._connect() as db:
            return _payloads(db.execute(query, params))

    def season_runtime(self, season_id: str) -> dict[str, Any]:
        season = next((item for item in self.seasons() if item.get("id") == season_id), None)
        return {
            "season": season or {},
            "scheduler": self.scheduler_events(season_id),
            "workspaces": self.season_workspaces(season_id),
        }

    def phase_history(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                select seq, event_type, scope, phase, sub_phase, created_at, payload_json
                from phase_history where run_id = ? order by seq
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "seq": row["seq"],
                "event_type": row["event_type"],
                "scope": row["scope"],
                "phase": row["phase"],
                "sub_phase": row["sub_phase"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def tool_violations(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                select seq, tool, phase, sub_phase, recovery_kind, payload_json
                from tool_violations where run_id = ? order by seq
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "seq": row["seq"],
                "tool": row["tool"],
                "phase": row["phase"],
                "sub_phase": row["sub_phase"],
                "recovery_kind": row["recovery_kind"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def agents(self) -> list[dict[str, Any]]:
        rows = self.leaderboard()
        agents: dict[str, dict[str, Any]] = {}
        for row in rows:
            handle = str(row.get("agent_handle") or row.get("agent_name") or "")
            if not handle:
                continue
            entry = agents.setdefault(
                handle,
                {
                    "agent_handle": handle,
                    "agent_name": str(row.get("agent_name") or handle),
                    "runs": 0,
                    "prs_opened": 0,
                    "reviewed_prs": 0,
                    "merged_prs": 0,
                    "seasons": [],
                    "leaderboard": [],
                },
            )
            entry["runs"] += int(row.get("runs") or 0)
            entry["prs_opened"] += int(row.get("prs_opened") or 0)
            entry["reviewed_prs"] += int(row.get("reviewed_prs") or 0)
            entry["merged_prs"] += int(row.get("merged_prs") or 0)
            entry["seasons"].append(row.get("season_id"))
            entry["leaderboard"].append(row)
        return sorted(agents.values(), key=lambda item: (item["runs"], item["merged_prs"]), reverse=True)

    def agent(self, agent_id: str) -> dict[str, Any] | None:
        for agent in self.agents():
            if agent.get("agent_handle") == agent_id or agent.get("agent_name") == agent_id:
                agent["runs_detail"] = self.runs(agent=agent_id, limit=500, offset=0)
                return agent
        return None

    def public_artifact_path(self, run_id: str, artifact_name: str) -> Path | None:
        if "/" in artifact_name or "\\" in artifact_name:
            return None
        with self._connect() as db:
            row = db.execute(
                "select path from artifacts where run_id = ? and name = ? and visibility = 'public'",
                (run_id, artifact_name),
            ).fetchone()
        if not row:
            return None
        path = Path(str(row[0]))
        return path if path.exists() and path.is_file() else None

    def status(self, input_dir: Path) -> ReadModelStatus:
        bundle = self.surface_bundle() if self.db_path.exists() else {}
        stats = bundle.get("stats", {}) if isinstance(bundle.get("stats"), dict) else {}
        runs = bundle.get("runs", []) if isinstance(bundle.get("runs"), list) else []
        agents = {
            str(run.get("agent", {}).get("handle") or run.get("agent", {}).get("name") or "")
            for run in runs
            if isinstance(run, dict) and isinstance(run.get("agent"), dict)
        }
        open_prs = 0
        for run in runs:
            pr = run.get("pull_request", {}) if isinstance(run, dict) else {}
            if isinstance(pr, dict) and pr.get("state") == "open":
                open_prs += 1
        return ReadModelStatus(
            db_path=self.db_path,
            input_dir=input_dir,
            generated_at=str(bundle.get("generated_at") or ""),
            runs=int(stats.get("runs") or 0),
            seasons=len(stats.get("seasons") or []),
            agents=len({item for item in agents if item}),
            judged_runs=int(stats.get("judged_runs") or 0),
            open_or_tracked_prs=open_prs,
            skipped=len(bundle.get("skipped") or []),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()


def _api_run(run: dict[str, Any], run_dir: Path, skipped: list[str]) -> dict[str, Any]:
    payload = dict(run)
    run_id = str(payload.get("run_id") or "")
    artifacts: list[dict[str, Any]] = []
    for artifact in payload.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        item = dict(artifact)
        name = str(item.get("name") or "")
        if item.get("visibility") != "public":
            item["url"] = ""
        elif not name or "/" in name or "\\" in name:
            item["url"] = ""
            skipped.append(f"{run_dir}: invalid public artifact name {name!r}")
        elif not (run_dir / name).is_file():
            item["url"] = ""
            skipped.append(f"{run_dir}: missing public artifact {name!r}")
        else:
            item["url"] = f"/api/artifacts/{run_id}/{name}"
        artifacts.append(item)
    payload["artifacts"] = artifacts
    return payload


def _create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        create table if not exists meta (
            key text primary key,
            value text not null
        );
        create table if not exists runs (
            run_id text primary key,
            season_id text not null,
            participant_id text not null default '',
            agent_handle text not null,
            agent_name text not null,
            run_status text not null,
            wake_source text not null default '',
            repo_slug text not null default '',
            started_at text not null,
            payload_json text not null
        );
        create table if not exists seasons (
            season_id text primary key,
            status text not null,
            payload_json text not null
        );
        create table if not exists participants (
            season_id text not null,
            participant_id text not null,
            payload_json text not null,
            primary key (season_id, participant_id)
        );
        create table if not exists pr_lifecycle (
            season_id text not null,
            participant_id text not null,
            repository text not null,
            number integer not null,
            state text not null,
            payload_json text not null,
            primary key (season_id, participant_id, repository, number)
        );
        create table if not exists discovery_calls (
            run_id text not null,
            seq integer not null,
            season_id text not null,
            participant_id text not null,
            query text not null,
            github_query_string text not null,
            payload_json text not null,
            primary key (run_id, seq)
        );
        create table if not exists scheduler_events (
            season_id text not null,
            participant_id text not null,
            created_at text not null,
            status text not null,
            payload_json text not null
        );
        create table if not exists season_workspaces (
            season_id text not null,
            participant_id text not null,
            repo_slug text not null,
            container_id text not null,
            payload_json text not null,
            primary key (season_id, participant_id, repo_slug)
        );
        create table if not exists artifacts (
            run_id text not null,
            name text not null,
            visibility text not null,
            path text not null,
            payload_json text not null,
            primary key (run_id, name)
        );
        create table if not exists phase_history (
            run_id text not null,
            seq integer not null,
            event_type text not null,
            scope text not null,
            phase text not null,
            sub_phase text not null,
            created_at text not null,
            payload_json text not null,
            primary key (run_id, seq)
        );
        create table if not exists tool_violations (
            run_id text not null,
            seq integer not null,
            tool text not null,
            phase text not null,
            sub_phase text not null,
            recovery_kind text not null,
            payload_json text not null,
            primary key (run_id, seq)
        );
        create table if not exists assistant_updates (
            run_id text not null,
            seq integer not null,
            season_id text not null,
            participant_id text not null,
            phase text not null,
            sub_phase text not null,
            kind text not null,
            tool_name text not null,
            payload_json text not null,
            primary key (run_id, seq)
        );
        """
    )
    _ensure_column(db, "runs", "participant_id", "text not null default ''")
    _ensure_column(db, "runs", "wake_source", "text not null default ''")
    _ensure_column(db, "runs", "repo_slug", "text not null default ''")
    db.executescript(
        """
        create index if not exists idx_runs_season on runs(season_id);
        create index if not exists idx_runs_participant on runs(participant_id);
        create index if not exists idx_runs_agent on runs(agent_handle);
        create index if not exists idx_runs_status on runs(run_status);
        create index if not exists idx_discovery_calls_run on discovery_calls(run_id);
        create index if not exists idx_scheduler_events_season on scheduler_events(season_id);
        create index if not exists idx_season_workspaces_season on season_workspaces(season_id);
        create index if not exists idx_pr_lifecycle_season on pr_lifecycle(season_id);
        create index if not exists idx_phase_history_run on phase_history(run_id);
        create index if not exists idx_tool_violations_run on tool_violations(run_id);
        create index if not exists idx_assistant_updates_run on assistant_updates(run_id);
        """
    )


def _replace_data(
    *,
    db: sqlite3.Connection,
    runs: list[dict[str, Any]],
    artifacts: list[tuple[str, str, str, str, str]],
    phase_history: list[tuple[str, int, str, str, str, str, str, str]],
    tool_violations: list[tuple[str, int, str, str, str, str, str]],
    discovery_calls: list[tuple[str, int, str, str, str, str, str]],
    assistant_updates: list[tuple[str, int, str, str, str, str, str, str, str]],
    scheduler_events: list[tuple[str, str, str, str, str, str]],
    pr_lifecycle: list[tuple[str, str, str, int, str, str]],
    season_workspaces: list[tuple[str, str, str, str, str]],
    seasons: list[tuple[str, str, str]],
    participants: list[tuple[str, str, str]],
    leaderboard: list[dict[str, Any]],
    stats: dict[str, Any],
    skipped: list[str],
    generated_at: str,
    input_dir: Path,
) -> None:
    db.execute("delete from runs")
    db.execute("delete from artifacts")
    db.execute("delete from phase_history")
    db.execute("delete from tool_violations")
    db.execute("delete from discovery_calls")
    db.execute("delete from assistant_updates")
    db.execute("delete from scheduler_events")
    db.execute("delete from pr_lifecycle")
    db.execute("delete from season_workspaces")
    db.execute("delete from seasons")
    db.execute("delete from participants")
    db.executemany(
        """
        insert into runs (
            run_id, season_id, participant_id, agent_handle, agent_name, run_status,
            wake_source, repo_slug, started_at, payload_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [_run_row(run) for run in runs],
    )
    db.executemany(
        "insert into seasons (season_id, status, payload_json) values (?, ?, ?)",
        seasons,
    )
    db.executemany(
        "insert into participants (season_id, participant_id, payload_json) values (?, ?, ?)",
        participants,
    )
    db.executemany(
        "insert into artifacts (run_id, name, visibility, path, payload_json) values (?, ?, ?, ?, ?)",
        artifacts,
    )
    db.executemany(
        """
        insert into phase_history (
            run_id, seq, event_type, scope, phase, sub_phase, created_at, payload_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        phase_history,
    )
    db.executemany(
        """
        insert into tool_violations (
            run_id, seq, tool, phase, sub_phase, recovery_kind, payload_json
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        tool_violations,
    )
    db.executemany(
        """
        insert into discovery_calls (
            run_id, seq, season_id, participant_id, query, github_query_string, payload_json
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        discovery_calls,
    )
    db.executemany(
        """
        insert into assistant_updates (
            run_id, seq, season_id, participant_id, phase, sub_phase, kind, tool_name, payload_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        assistant_updates,
    )
    db.executemany(
        """
        insert into scheduler_events (
            season_id, participant_id, created_at, status, payload_json
        ) values (?, ?, ?, ?, ?)
        """,
        scheduler_events,
    )
    db.executemany(
        """
        insert into pr_lifecycle (
            season_id, participant_id, repository, number, state, payload_json
        ) values (?, ?, ?, ?, ?, ?)
        """,
        pr_lifecycle,
    )
    db.executemany(
        """
        insert into season_workspaces (
            season_id, participant_id, repo_slug, container_id, payload_json
        ) values (?, ?, ?, ?, ?)
        """,
        season_workspaces,
    )
    meta = {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "input_dir": str(input_dir),
        "leaderboard": json.dumps(leaderboard, ensure_ascii=True),
        "stats": json.dumps(stats, ensure_ascii=True),
        "skipped": json.dumps(skipped, ensure_ascii=True),
    }
    db.executemany(
        "insert or replace into meta (key, value) values (?, ?)",
        list(meta.items()),
    )


def _run_row(run: dict[str, Any]) -> tuple[str, str, str, str, str, str, str, str, str, str]:
    season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
    agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
    repository = run.get("repository", {}) if isinstance(run.get("repository"), dict) else {}
    agent_name = _agent_display_name(run, agent)
    return (
        str(run.get("run_id") or ""),
        str(season.get("id") or ""),
        str(agent.get("participant_id") or ""),
        str(agent.get("handle") or agent_name),
        agent_name,
        str(run.get("run_status") or "unknown"),
        str(run.get("wake_source") or ""),
        str(repository.get("full_name") or ""),
        str(run.get("started_at") or ""),
        json.dumps(run, ensure_ascii=True),
    )


def _season_rows(runs: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return _season_payload_rows(_seasons_from_runs(runs))


def _seasons_from_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seasons: dict[str, dict[str, Any]] = {}
    for run in runs:
        if _ranking_excluded(run):
            continue
        season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
        season_id = str(season.get("id") or "")
        if not season_id:
            continue
        existing = seasons.setdefault(
            season_id,
            {
                "id": season_id,
                "name": str(season.get("name") or season_id),
                "phase": str(season.get("phase") or "unknown"),
                "status": str(season.get("status") or "unknown"),
                "runs_count": 0,
                "participants_count": 0,
                "wake_sources": [],
            },
        )
        existing["runs_count"] = int(existing["runs_count"]) + 1
        wake_source = str(run.get("wake_source") or "")
        if wake_source and wake_source not in existing["wake_sources"]:
            existing["wake_sources"].append(wake_source)
    participant_counts: dict[str, set[str]] = {}
    for run in runs:
        if _ranking_excluded(run):
            continue
        season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
        agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
        season_id = str(season.get("id") or "")
        participant_id = str(agent.get("participant_id") or "")
        if season_id and participant_id:
            participant_counts.setdefault(season_id, set()).add(participant_id)
    rows = []
    for season_id, payload in seasons.items():
        payload["participants_count"] = len(participant_counts.get(season_id, set()))
        rows.append(payload)
    return rows


def _season_payload_rows(seasons: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for payload in seasons:
        season_id = str(payload.get("id") or "")
        if not season_id:
            continue
        rows.append((season_id, str(payload.get("status") or "unknown"), json.dumps(payload, ensure_ascii=True)))
    return rows


def _scheduler_event_tuple(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("season_id") or ""),
        str(row.get("participant_id") or ""),
        str(row.get("created_at") or row.get("ts") or ""),
        str(row.get("status") or row.get("event") or ""),
        json.dumps(row, ensure_ascii=True),
    )


def _workspace_tuple(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("season_id") or ""),
        str(row.get("participant_id") or ""),
        str(row.get("repo_slug") or ""),
        str(row.get("container_id") or ""),
        json.dumps(row, ensure_ascii=True),
    )


def _participant_rows(runs: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    participants: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs:
        if _ranking_excluded(run):
            continue
        season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
        agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
        season_id = str(season.get("id") or "")
        participant_id = str(agent.get("participant_id") or "")
        if not season_id or not participant_id:
            continue
        key = (season_id, participant_id)
        entry = participants.setdefault(
            key,
            {
                "season_id": season_id,
                "participant_id": participant_id,
                "agent_name": _agent_display_name(run, agent),
                "agent_handle": str(agent.get("handle") or participant_id),
                "runs_count": 0,
                "prs_opened": 0,
                "merged_prs": 0,
                "failures": 0,
                "last_run_at": "",
                "latest_run_id": "",
                "mean_arena_score": None,
                "_arena_scores": [],
            },
        )
        entry["runs_count"] = int(entry["runs_count"]) + 1
        if str(run.get("run_status") or "") != "completed":
            entry["failures"] = int(entry["failures"]) + 1
        if _pr_opened(run):
            entry["prs_opened"] = int(entry["prs_opened"]) + 1
        if _merged(run):
            entry["merged_prs"] = int(entry["merged_prs"]) + 1
        started_at = str(run.get("started_at") or "")
        if started_at >= str(entry.get("last_run_at") or ""):
            entry["last_run_at"] = started_at
            entry["latest_run_id"] = str(run.get("run_id") or "")
        judgement = run.get("judgement", {}) if isinstance(run.get("judgement"), dict) else {}
        _append_float(entry["_arena_scores"], judgement.get("arena_score"))
    rows = []
    for (season_id, participant_id), payload in participants.items():
        payload["mean_arena_score"] = _mean(payload["_arena_scores"])
        payload.pop("_arena_scores", None)
        rows.append((season_id, participant_id, json.dumps(payload, ensure_ascii=True)))
    return rows


def _phase_history_rows(
    run_id: str,
    run_dir: Path,
) -> list[tuple[str, int, str, str, str, str, str, str]]:
    rows: list[tuple[str, int, str, str, str, str, str, str]] = []
    for seq, payload in enumerate(_read_jsonl(run_dir / "phase_transition.jsonl"), start=1):
        rows.append(
            (
                run_id,
                seq,
                str(payload.get("event_type") or ""),
                str(payload.get("scope") or ""),
                str(payload.get("phase") or ""),
                str(payload.get("sub_phase") or ""),
                str(payload.get("created_at") or ""),
                json.dumps(payload, ensure_ascii=True),
            )
        )
    return rows


def _tool_violation_rows(
    run_id: str,
    run_dir: Path,
) -> list[tuple[str, int, str, str, str, str, str]]:
    rows: list[tuple[str, int, str, str, str, str, str]] = []
    for seq, payload in enumerate(_read_jsonl(run_dir / "tool_violation_log.jsonl"), start=1):
        rows.append(
            (
                run_id,
                seq,
                str(payload.get("tool") or ""),
                str(payload.get("phase") or ""),
                str(payload.get("sub_phase") or ""),
                str(payload.get("recovery_kind") or ""),
                json.dumps(payload, ensure_ascii=True),
            )
        )
    return rows


def _discovery_call_rows(
    run_id: str,
    run_dir: Path,
) -> list[tuple[str, int, str, str, str, str, str]]:
    rows: list[tuple[str, int, str, str, str, str, str]] = []
    for seq, payload in enumerate(_read_jsonl(run_dir / "discovery_log.jsonl"), start=1):
        rows.append(
            (
                run_id,
                seq,
                str(payload.get("season_id") or ""),
                str(payload.get("participant_id") or ""),
                str(payload.get("query") or ""),
                str(payload.get("github_query_string") or ""),
                json.dumps(payload, ensure_ascii=True),
            )
        )
    return rows


def _assistant_update_rows(
    run_id: str,
    run_dir: Path,
) -> list[tuple[str, int, str, str, str, str, str, str, str]]:
    rows: list[tuple[str, int, str, str, str, str, str, str, str]] = []
    for seq, payload in enumerate(_read_jsonl(run_dir / "assistant_updates.jsonl"), start=1):
        item = dict(payload)
        item.setdefault("run_id", run_id)
        rows.append(
            (
                run_id,
                seq,
                str(item.get("season_id") or ""),
                str(item.get("participant_id") or ""),
                str(item.get("phase") or ""),
                str(item.get("sub_phase") or ""),
                str(item.get("kind") or ""),
                str(item.get("tool_name") or ""),
                json.dumps(item, ensure_ascii=True),
            )
        )
    return rows


def _scheduler_event_rows(run: dict[str, Any], run_dir: Path) -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
    agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
    season_id = str(season.get("id") or "")
    participant_id = str(agent.get("participant_id") or "")
    if not season_id or not participant_id:
        return rows
    for payload in _read_jsonl(run_dir / "operator_events.jsonl"):
        if payload.get("phase") != "run" or payload.get("status") != "started":
            continue
        event = {
            "season_id": season_id,
            "participant_id": participant_id,
            "wake_source": run.get("wake_source") or "",
            "run_id": run.get("run_id") or "",
            "status": "started",
            "created_at": payload.get("ts") or run.get("started_at") or "",
        }
        rows.append(
            (
                season_id,
                participant_id,
                str(event["created_at"]),
                "started",
                json.dumps(event, ensure_ascii=True),
            )
        )
    return rows


def _pr_lifecycle_rows(run: dict[str, Any], run_dir: Path) -> list[tuple[str, str, str, int, str, str]]:
    rows: list[tuple[str, str, str, int, str, str]] = []
    season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
    agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
    season_id = str(season.get("id") or "")
    participant_id = str(agent.get("participant_id") or "")
    for payload in _read_jsonl(run_dir / "pr_review_log.jsonl"):
        repository = str(payload.get("repository") or "")
        number = _int(payload.get("number"))
        if not repository or number is None:
            continue
        state = str(payload.get("state") or payload.get("lifecycle_status") or "unknown")
        item = dict(payload)
        item.setdefault("season_id", season_id)
        item.setdefault("participant_id", participant_id)
        rows.append((season_id, participant_id, repository, number, state, json.dumps(item, ensure_ascii=True)))
    pr = run.get("pull_request", {}) if isinstance(run.get("pull_request"), dict) else {}
    repository = str(run.get("repository", {}).get("full_name") or "") if isinstance(run.get("repository"), dict) else ""
    number = _int(pr.get("number"))
    if repository and number is not None and not rows:
        item = {
            "season_id": season_id,
            "participant_id": participant_id,
            "repository": repository,
            "number": number,
            "url": pr.get("url") or "",
            "state": pr.get("state") or "unknown",
            "run_id": run.get("run_id") or "",
        }
        rows.append((season_id, participant_id, repository, number, str(item["state"]), json.dumps(item, ensure_ascii=True)))
    return rows


def _workspace_rows(run: dict[str, Any], run_dir: Path) -> list[tuple[str, str, str, str, str]]:
    config = _read_json_file(run_dir / "config.json")
    workspace = config.get("workspace", {}) if isinstance(config.get("workspace"), dict) else {}
    container_path = workspace.get("persistent_metadata_path")
    if not container_path:
        summary_workspace = run.get("workspace", {}) if isinstance(run.get("workspace"), dict) else {}
        container_path = summary_workspace.get("persistent_metadata_path")
    if not container_path:
        return []
    season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
    agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
    repository = run.get("repository", {}) if isinstance(run.get("repository"), dict) else {}
    season_id = str(season.get("id") or "")
    participant_id = str(agent.get("participant_id") or "")
    repo_slug = str(repository.get("full_name") or "")
    metadata_path = Path(str(container_path))
    container_id = ""
    if metadata_path.exists():
        container_id = metadata_path.read_text(encoding="utf-8", errors="replace").strip()
    payload = {
        "season_id": season_id,
        "participant_id": participant_id,
        "repo_slug": repo_slug,
        "container_id": container_id,
        "metadata_path": str(metadata_path),
        "last_used_at": _read_text_file(metadata_path.parent / "last_used_at"),
        "clone_state": _read_json_file(metadata_path.parent / "clone_state.json"),
    }
    return [(season_id, participant_id, repo_slug, container_id, json.dumps(payload, ensure_ascii=True))]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {str(row[1]) for row in db.execute(f"pragma table_info({table})").fetchall()}
    if column not in existing:
        db.execute(f"alter table {table} add column {column} {definition}")


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _pr_opened(run: dict[str, Any]) -> bool:
    pr = run.get("pull_request", {}) if isinstance(run.get("pull_request"), dict) else {}
    return bool(pr.get("url")) or pr.get("state") in {"open", "closed", "merged"}


def _merged(run: dict[str, Any]) -> bool:
    outcome = (
        run.get("maintainer_outcome", {})
        if isinstance(run.get("maintainer_outcome"), dict)
        else {}
    )
    pr = run.get("pull_request", {}) if isinstance(run.get("pull_request"), dict) else {}
    return outcome.get("status") == "merged" or pr.get("state") == "merged"


def _replacement_excluded(run: dict[str, Any]) -> bool:
    replacement = run.get("replacement")
    if not isinstance(replacement, dict):
        return False
    return str(replacement.get("status") or "") in {"due", "running", "replaced", "failed", "exhausted"}


def _judgement_retry_excluded(run: dict[str, Any]) -> bool:
    retry = run.get("judgement_retry")
    if not isinstance(retry, dict):
        return False
    if str(retry.get("status") or "") in {"due", "running"}:
        return True
    judgement = run.get("judgement", {}) if isinstance(run.get("judgement"), dict) else {}
    return str(judgement.get("status") or "") == "deferred"


def _ranking_excluded(run: dict[str, Any]) -> bool:
    return _replacement_excluded(run) or _judgement_retry_excluded(run)


def _ranking_exclusion_reason(run: dict[str, Any]) -> str:
    replacement = run.get("replacement")
    if isinstance(replacement, dict) and str(replacement.get("status") or "") in {
        "due",
        "running",
        "replaced",
        "failed",
        "exhausted",
    }:
        return f"replacement_{replacement.get('status')}"
    retry = run.get("judgement_retry")
    if isinstance(retry, dict) and str(retry.get("status") or "") in {"due", "running"}:
        return f"judgement_retry_{retry.get('status')}"
    judgement = run.get("judgement", {}) if isinstance(run.get("judgement"), dict) else {}
    if str(judgement.get("status") or "") == "deferred":
        return "judgement_deferred"
    return ""


def _annotate_ranking_state(run: dict[str, Any]) -> dict[str, Any]:
    annotated = dict(run)
    reason = _ranking_exclusion_reason(annotated)
    annotated["ranking_excluded"] = bool(reason)
    annotated["ranking_exclusion_reason"] = reason
    return annotated


def _agent_display_name(run: dict[str, Any], agent: dict[str, Any]) -> str:
    for raw in (
        str(agent.get("name") or ""),
        str(agent.get("handle") or ""),
        str(agent.get("participant_id") or ""),
        str(run.get("model") or ""),
    ):
        if raw and raw != "builtin":
            return normalize_model_identity(raw)
    return normalize_model_identity(str(run.get("model") or "unknown"))


def _append_float(values: list[float], value: object) -> None:
    try:
        values.append(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _payloads(rows: Any) -> list[dict[str, Any]]:
    return [json.loads(row[0]) for row in rows]


def _group_payloads(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key) or ""), []).append(row)
    return grouped


def _meta(db: sqlite3.Connection, key: str) -> str:
    row = db.execute("select value from meta where key = ?", (key,)).fetchone()
    return str(row[0]) if row else ""


def _json_meta(db: sqlite3.Connection, key: str, *, default: Any) -> Any:
    value = _meta(db, key)
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
