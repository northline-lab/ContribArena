from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contribarena.engine.surface_indexer import (
    SURFACE_SCHEMA_VERSION,
    _leaderboard,
    _load_run_summaries,
    _stats,
)
from contribarena.errors import InfrastructureError


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
        leaderboard = _leaderboard(public_runs)
        stats = _stats(public_runs)
        generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self._connect() as db:
            _replace_data(
                db=db,
                runs=public_runs,
                artifacts=artifact_rows,
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
            return {
                "schema_version": SURFACE_SCHEMA_VERSION,
                "generated_at": _meta(db, "generated_at"),
                "stats": _json_meta(db, "stats", default={}),
                "leaderboard": _json_meta(db, "leaderboard", default=[]),
                "runs": runs,
                "skipped": _json_meta(db, "skipped", default=[]),
            }

    def seasons(self) -> list[dict[str, Any]]:
        seasons: dict[str, dict[str, Any]] = {}
        for run in self.runs(limit=10_000, offset=0):
            season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
            season_id = str(season.get("id") or "")
            if season_id and season_id not in seasons:
                seasons[season_id] = {
                    "id": season_id,
                    "name": str(season.get("name") or season_id),
                    "phase": str(season.get("phase") or "unknown"),
                }
        return sorted(seasons.values(), key=lambda item: item["id"])

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
            clauses.append("(agent_handle = ? or agent_name = ?)")
            params.extend([agent, agent])
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
            return _payloads(db.execute(query, params))

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("select payload_json from runs where run_id = ?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

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

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db


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
            agent_handle text not null,
            agent_name text not null,
            run_status text not null,
            started_at text not null,
            payload_json text not null
        );
        create table if not exists artifacts (
            run_id text not null,
            name text not null,
            visibility text not null,
            path text not null,
            payload_json text not null,
            primary key (run_id, name)
        );
        create index if not exists idx_runs_season on runs(season_id);
        create index if not exists idx_runs_agent on runs(agent_handle);
        create index if not exists idx_runs_status on runs(run_status);
        """
    )


def _replace_data(
    *,
    db: sqlite3.Connection,
    runs: list[dict[str, Any]],
    artifacts: list[tuple[str, str, str, str, str]],
    leaderboard: list[dict[str, Any]],
    stats: dict[str, Any],
    skipped: list[str],
    generated_at: str,
    input_dir: Path,
) -> None:
    db.execute("delete from runs")
    db.execute("delete from artifacts")
    db.executemany(
        """
        insert into runs (
            run_id, season_id, agent_handle, agent_name, run_status, started_at, payload_json
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        [_run_row(run) for run in runs],
    )
    db.executemany(
        "insert into artifacts (run_id, name, visibility, path, payload_json) values (?, ?, ?, ?, ?)",
        artifacts,
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


def _run_row(run: dict[str, Any]) -> tuple[str, str, str, str, str, str, str]:
    season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
    agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
    return (
        str(run.get("run_id") or ""),
        str(season.get("id") or ""),
        str(agent.get("handle") or agent.get("name") or "builtin"),
        str(agent.get("name") or "builtin"),
        str(run.get("run_status") or "unknown"),
        str(run.get("started_at") or ""),
        json.dumps(run, ensure_ascii=True),
    )


def _payloads(rows: Any) -> list[dict[str, Any]]:
    return [json.loads(row[0]) for row in rows]


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
