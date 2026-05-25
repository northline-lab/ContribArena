from __future__ import annotations

import json
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from contribarena.config.schema import RunConfig
from contribarena.memory.redact import redact_payload, redact_text
from contribarena.engine.read_model import SurfaceReadModel


def create_app(
    config: RunConfig,
    *,
    input_dir: Path | None = None,
    db_path: Path | None = None,
    watch: bool | None = None,
) -> FastAPI:
    artifact_root = input_dir or config.artifacts.output_root
    model = SurfaceReadModel(db_path or config.backend.read_model_path)
    app = FastAPI(title="ContribArena Benchmark API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.backend.api_cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    watcher = _ReadModelWatcher(
        model=model,
        input_dir=artifact_root,
        debounce_seconds=config.backend.refresh_debounce_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        model.refresh_from_artifacts(artifact_root)
        if config.backend.watch_enabled if watch is None else watch:
            watcher.start()
        try:
            yield
        finally:
            watcher.stop()

    app.router.lifespan_context = lifespan

    @app.get("/api/health")
    def health() -> dict[str, object]:
        status = model.status(artifact_root)
        return {
            "ok": True,
            "generated_at": status.generated_at,
            "runs": status.runs,
            "db_path": str(status.db_path),
            "input_dir": str(status.input_dir),
        }

    @app.get("/api/surface")
    def surface(season_id: str | None = None) -> dict[str, object]:
        season_id = season_id or _default_season_id(model)
        return _surface_bundle_for_season(model, season_id)

    @app.get("/api/seasons")
    def seasons() -> dict[str, object]:
        return {"seasons": model.seasons()}

    @app.get("/api/seasons/{season_id}")
    def season(season_id: str) -> dict[str, object]:
        for item in model.seasons():
            if item["id"] == season_id:
                return {
                    "season": item,
                    "stats": model.stats(season_id),
                    "participants": model.participants(season_id),
                    "scheduler": model.scheduler_events(season_id),
                    "workspaces": model.season_workspaces(season_id),
                }
        raise HTTPException(status_code=404, detail="season not found")

    @app.get("/api/seasons/{season_id}/participants")
    def season_participants(season_id: str) -> dict[str, object]:
        return {"participants": model.participants(season_id)}

    @app.get("/api/participants/{participant_id}")
    def participant(participant_id: str) -> dict[str, object]:
        item = model.participant(participant_id)
        if item is None:
            raise HTTPException(status_code=404, detail="participant not found")
        return {"participant": item}

    @app.get("/api/seasons/{season_id}/pr-lifecycle")
    def season_pr_lifecycle(season_id: str) -> dict[str, object]:
        return {"pr_lifecycle": model.pr_lifecycle(season_id=season_id)}

    @app.get("/api/seasons/{season_id}/scheduler")
    def season_scheduler(season_id: str) -> dict[str, object]:
        return {"scheduler": model.scheduler_events(season_id)}

    @app.get("/api/seasons/{season_id}/workspaces")
    def season_workspaces(season_id: str) -> dict[str, object]:
        return {"workspaces": model.season_workspaces(season_id)}

    @app.get("/api/seasons/{season_id}/runtime")
    def season_runtime(season_id: str) -> dict[str, object]:
        payload = model.season_runtime(season_id)
        if not payload.get("season"):
            raise HTTPException(status_code=404, detail="season not found")
        return payload

    @app.get("/api/stats")
    def stats(season_id: str | None = None) -> dict[str, object]:
        season_id = season_id or _default_season_id(model)
        return model.stats(season_id)

    @app.get("/api/leaderboard")
    def leaderboard(season_id: str | None = None) -> dict[str, object]:
        season_id = season_id or _default_season_id(model)
        return {"leaderboard": model.leaderboard(season_id)}

    @app.get("/api/seasons/{season_id}/leaderboard")
    def season_leaderboard(season_id: str) -> dict[str, object]:
        return {"leaderboard": model.leaderboard(season_id)}

    @app.get("/api/runs")
    def runs(
        season_id: str | None = None,
        status: str | None = None,
        agent: str | None = None,
        q: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        season_id = season_id or _default_season_id(model)
        return {
            "runs": model.runs(
                season_id=season_id,
                status=status,
                agent=agent,
                query=q,
                limit=limit,
                offset=offset,
            )
        }

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> dict[str, object]:
        item = model.run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail="run not found")
        return item

    @app.get("/api/runs/{run_id}/discovery")
    def run_discovery(run_id: str) -> dict[str, object]:
        return {"discovery": model.discovery_calls(run_id)}

    @app.get("/api/runs/{run_id}/self-review")
    def run_self_review(run_id: str) -> dict[str, object]:
        item = model.run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {"self_review": model.self_review(run_id)}

    @app.get("/api/runs/{run_id}/assistant-updates")
    def run_assistant_updates(run_id: str) -> dict[str, object]:
        item = model.run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {"updates": model.assistant_updates(run_id)}

    @app.get("/api/operator/summary")
    def operator_summary(season_id: str | None = None) -> dict[str, object]:
        season_id = season_id or _default_season_id(model)
        status = model.status(artifact_root)
        runs = model.runs(season_id=season_id, limit=500, offset=0)
        leaderboard_rows = model.leaderboard(season_id)
        runtime = model.season_runtime(season_id) if season_id else {"season": {}}
        heartbeat = _heartbeat(runtime)
        latest_run = _compact_run(runs[0]) if runs else {}
        return {
            "season_id": season_id,
            "read_model": {
                "generated_at": status.generated_at,
                "age_seconds": _age_seconds(status.generated_at),
                "runs": status.runs,
                "db_path": str(status.db_path),
                "input_dir": str(status.input_dir),
                "refresh": watcher.diagnostics(),
            },
            "heartbeat": {
                **heartbeat,
                "last_started_age_seconds": _age_seconds(heartbeat.get("last_started_at")),
                "last_completed_age_seconds": _age_seconds(heartbeat.get("last_completed_at")),
            },
            "counts": _operator_counts(runs),
            "latest_run": latest_run,
            "leaderboard": {
                "entries": len(leaderboard_rows),
                "top": _operator_summary_leaderboard_top(leaderboard_rows),
            },
            "stuck": _stuck_diagnosis(runtime, runs, stale_after_seconds=1800),
        }

    @app.get("/api/operator/runs")
    def operator_runs(
        season_id: str | None = None,
        status: str | None = None,
        agent: str | None = None,
        q: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        season_id = season_id or _default_season_id(model)
        rows = model.runs(
            season_id=season_id,
            status=status,
            agent=agent,
            query=q,
            limit=limit,
            offset=offset,
        )
        return {"season_id": season_id, "runs": [_compact_run(row) for row in rows]}

    @app.get("/api/operator/stuck")
    def operator_stuck(
        season_id: str | None = None,
        stale_after_seconds: int = Query(default=1800, ge=60, le=86400),
    ) -> dict[str, object]:
        season_id = season_id or _default_season_id(model)
        runtime = model.season_runtime(season_id) if season_id else {"season": {}}
        runs = model.runs(season_id=season_id, limit=20, offset=0)
        return {
            "season_id": season_id,
            "read_model_generated_at": model.status(artifact_root).generated_at,
            "diagnosis": _stuck_diagnosis(runtime, runs, stale_after_seconds=stale_after_seconds),
            "latest_run": _compact_run(runs[0]) if runs else {},
            "latest_scheduler_event": _last_item(runtime.get("scheduler")),
        }

    @app.get("/api/operator/run/{run_id}")
    def operator_run(
        run_id: str,
        event_limit: int = Query(default=30, ge=1, le=100),
        q: str | None = None,
    ) -> dict[str, object]:
        item = model.run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail="run not found")
        run_dir = model.run_dir(run_id) or _find_run_dir(artifact_root, run_id)
        detail: dict[str, object] = {
            "run": _compact_run(item),
            "summary": item,
            "run_dir_found": run_dir is not None,
        }
        if run_dir is None:
            return detail
        detail.update(
            {
                "artifact_summary": _artifact_summary(run_dir),
                "core_files": _core_file_status(run_dir),
                "excerpts": _run_excerpts(run_dir),
                "judgement": _judgement_summary(run_dir / "judgement.json"),
                "operator_events": _operator_events(run_dir / "operator_events.jsonl", limit=event_limit, query=q),
                "phase_events": _jsonl_tail(run_dir / "phase_transition.jsonl", limit=20),
                "goal_events": _jsonl_tail(run_dir / "goal_events.jsonl", limit=20),
            }
        )
        return detail

    @app.get("/api/agents")
    def agents() -> dict[str, object]:
        return {"agents": model.agents()}

    @app.get("/api/agents/{agent_id}")
    def agent(agent_id: str) -> dict[str, object]:
        item = model.agent(agent_id)
        if item is None:
            raise HTTPException(status_code=404, detail="agent not found")
        return item

    @app.get("/api/artifacts/{run_id}/{artifact_name}")
    def artifact(run_id: str, artifact_name: str) -> FileResponse:
        path = model.public_artifact_path(run_id, artifact_name)
        if path is None:
            raise HTTPException(status_code=404, detail="public artifact not found")
        return FileResponse(path)

    return app


class _ReadModelWatcher:
    def __init__(
        self,
        *,
        model: SurfaceReadModel,
        input_dir: Path,
        debounce_seconds: float,
    ) -> None:
        self.model = model
        self.input_dir = input_dir
        self.debounce_seconds = debounce_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_refresh_at = ""
        self._last_success_at = ""
        self._last_error_at = ""
        self._last_error = ""
        self._last_error_type = ""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="contribarena-read-model-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        try:
            from watchfiles import watch

            for _changes in watch(self.input_dir, stop_event=self._stop):
                if self._stop.wait(self.debounce_seconds):
                    break
                self._refresh_safely()
        except Exception:
            self._poll()

    def _poll(self) -> None:
        last_signature = ""
        while not self._stop.wait(max(2.0, self.debounce_seconds)):
            signature = _artifact_signature(self.input_dir)
            if signature != last_signature:
                last_signature = signature
                self._refresh_safely()

    def _refresh_safely(self) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._last_refresh_at = now
        try:
            self.model.refresh_from_artifacts(self.input_dir)
        except Exception as exc:
            with self._lock:
                self._last_error_at = now
                self._last_error_type = type(exc).__name__
                self._last_error = str(exc)[:1000]
            return
        with self._lock:
            self._last_success_at = now
            self._last_error_at = ""
            self._last_error_type = ""
            self._last_error = ""

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "watch_enabled": self._thread is not None,
                "last_refresh_at": self._last_refresh_at,
                "last_success_at": self._last_success_at,
                "last_error_at": self._last_error_at,
                "last_error_type": self._last_error_type,
                "last_error": redact_text(self._last_error, max_chars=1000) if self._last_error else "",
            }


def _artifact_signature(path: Path) -> str:
    if not path.exists():
        return ""
    latest = 0
    count = 0
    for summary in path.rglob("run_summary.json"):
        try:
            stat = summary.stat()
        except OSError:
            continue
        count += 1
        latest = max(latest, int(stat.st_mtime_ns))
    return f"{count}:{latest}"


def _default_season_id(model: SurfaceReadModel) -> str | None:
    seasons = model.seasons()
    if not seasons:
        return None
    active = [
        str(season.get("id") or "")
        for season in seasons
        if str(season.get("status") or "") in {"active", "observing", "completed"}
        and season.get("id")
    ]
    if active:
        return active[0]
    first = str(seasons[0].get("id") or "")
    return first or None


def _surface_bundle_for_season(
    model: SurfaceReadModel,
    season_id: str | None,
) -> dict[str, Any]:
    bundle = model.surface_bundle()
    if not season_id:
        return bundle
    runs = model.runs(season_id=season_id, limit=500, offset=0)
    run_ids = {str(run.get("run_id") or "") for run in runs}
    updates = bundle.get("assistant_updates", {})
    if isinstance(updates, dict):
        updates = {run_id: rows for run_id, rows in updates.items() if run_id in run_ids}
    else:
        updates = {}
    return {
        **bundle,
        "stats": model.stats(season_id),
        "leaderboard": model.leaderboard(season_id),
        "runs": runs,
        "seasons": [
            season for season in model.seasons() if str(season.get("id") or "") == season_id
        ],
        "participants": model.participants(season_id),
        "pr_lifecycle": model.pr_lifecycle(season_id=season_id),
        "scheduler": model.scheduler_events(season_id),
        "workspaces": model.season_workspaces(season_id),
        "assistant_updates": updates,
    }


def _compact_run(run: dict[str, Any]) -> dict[str, Any]:
    agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
    judgement = run.get("judgement", {}) if isinstance(run.get("judgement"), dict) else {}
    replacement = run.get("replacement", {}) if isinstance(run.get("replacement"), dict) else {}
    pr = run.get("pull_request", {}) if isinstance(run.get("pull_request"), dict) else {}
    return {
        "run_id": run.get("run_id"),
        "season_id": _nested(run, "season", "id"),
        "participant_id": agent.get("participant_id"),
        "agent_name": agent.get("name") or agent.get("handle"),
        "model": run.get("model"),
        "repository": _nested(run, "repository", "full_name"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "run_status": run.get("run_status"),
        "terminal_layer": run.get("terminal_layer"),
        "terminal_reason": run.get("terminal_reason"),
        "wake_source": run.get("wake_source"),
        "pr": {
            "number": pr.get("number"),
            "state": pr.get("state"),
            "url": pr.get("url"),
        },
        "judgement": {
            "status": judgement.get("status"),
            "judge_score": judgement.get("judge_score"),
            "arena_score": judgement.get("arena_score"),
        },
        "replacement": {
            "status": replacement.get("status"),
            "reason": replacement.get("reason"),
            "layer": replacement.get("layer"),
        },
        "judgement_retry": run.get("judgement_retry") or {},
        "ranking_excluded": run.get("ranking_excluded"),
        "ranking_exclusion_reason": run.get("ranking_exclusion_reason"),
    }


def _operator_summary_leaderboard_top(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "agent_name": row.get("agent_name"),
            "participant_id": row.get("participant_id"),
            "mean_arena_score": row.get("mean_arena_score"),
            "runs": row.get("runs"),
        }
        for row in rows[:5]
    ]


def _operator_counts(runs: list[dict[str, Any]]) -> dict[str, object]:
    by_status: dict[str, int] = {}
    replacement: dict[str, int] = {}
    judgement_retry: dict[str, int] = {}
    excluded = 0
    for run in runs:
        _count(by_status, str(run.get("run_status") or "unknown"))
        repl = run.get("replacement", {}) if isinstance(run.get("replacement"), dict) else {}
        retry = run.get("judgement_retry", {}) if isinstance(run.get("judgement_retry"), dict) else {}
        if repl.get("status"):
            _count(replacement, str(repl.get("status")))
        if retry.get("status"):
            _count(judgement_retry, str(retry.get("status")))
        if run.get("ranking_excluded"):
            excluded += 1
    return {
        "runs": len(runs),
        "by_status": by_status,
        "replacement": replacement,
        "judgement_retry": judgement_retry,
        "ranking_excluded": excluded,
    }


def _stuck_diagnosis(
    runtime: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    stale_after_seconds: int,
) -> dict[str, object]:
    heartbeat = _heartbeat(runtime)
    last_status = str(heartbeat.get("last_status") or "")
    started_age = _age_seconds(heartbeat.get("last_started_at"))
    completed_age = _age_seconds(heartbeat.get("last_completed_at"))
    stale = bool(
        last_status == "running"
        and started_age is not None
        and started_age >= stale_after_seconds
    )
    reasons: list[str] = []
    if stale:
        reasons.append("heartbeat_running_too_long")
    if completed_age is not None and completed_age >= stale_after_seconds:
        reasons.append("last_completed_stale")
    latest = runs[0] if runs else {}
    if latest:
        latest_completed_age = _age_seconds(latest.get("completed_at"))
        if latest_completed_age is not None and latest_completed_age >= stale_after_seconds:
            reasons.append("latest_run_stale")
    return {
        "stuck": stale,
        "reasons": reasons,
        "heartbeat": heartbeat,
        "last_started_age_seconds": started_age,
        "last_completed_age_seconds": completed_age,
        "stale_after_seconds": stale_after_seconds,
    }


def _find_run_dir(artifact_root: Path, run_id: str) -> Path | None:
    if not run_id:
        return None
    for summary_path in artifact_root.rglob("run_summary.json"):
        payload = _read_json_file(summary_path)
        if isinstance(payload, dict) and str(payload.get("run_id") or "") == run_id:
            return summary_path.parent
    return None


def _artifact_summary(run_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(run_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append(
            {
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            }
        )
    return rows


def _core_file_status(run_dir: Path) -> dict[str, object]:
    names = [
        "run_summary.json",
        "terminal_state.json",
        "replacement_state.json",
        "judgement_retry_state.json",
        "judgement.json",
        "postmortem.md",
        "selected_task.md",
        "quality_gate.json",
        "governance_decision.json",
        "pr_lifecycle_state.json",
        "operator_events.jsonl",
        "goal_events.jsonl",
        "trajectory.json",
        "trace.jsonl",
    ]
    return {name: _file_status(run_dir / name) for name in names}


def _run_excerpts(run_dir: Path) -> dict[str, object]:
    return {
        "terminal_state": _safe_json_payload(run_dir / "terminal_state.json"),
        "replacement_state": _safe_json_payload(run_dir / "replacement_state.json"),
        "judgement_retry_state": _safe_json_payload(run_dir / "judgement_retry_state.json"),
        "quality_gate": _safe_json_payload(run_dir / "quality_gate.json"),
        "governance_decision": _safe_json_payload(run_dir / "governance_decision.json"),
        "pr_lifecycle_state": _safe_json_payload(run_dir / "pr_lifecycle_state.json"),
        "selected_task": _safe_text_excerpt(run_dir / "selected_task.md", max_chars=1200),
        "postmortem": _safe_text_excerpt(run_dir / "postmortem.md", max_chars=1200),
        "pr_description": _safe_text_excerpt(run_dir / "pr_description.md", max_chars=1200),
        "quality_report": _safe_text_excerpt(run_dir / "quality_report.md", max_chars=1200),
    }


def _judgement_summary(path: Path) -> dict[str, object]:
    payload = _read_json_file(path)
    if not isinstance(payload, dict):
        return {"exists": path.exists()}
    judges = payload.get("judges", [])
    judge_rows: list[dict[str, object]] = []
    if isinstance(judges, list):
        for judge in judges:
            if not isinstance(judge, dict):
                continue
            rubric = judge.get("rubric", [])
            judge_rows.append(
                {
                    "judge_id": judge.get("judge_id"),
                    "model": judge.get("model"),
                    "judge_score": judge.get("judge_score"),
                    "error": redact_text(str(judge.get("error") or ""), max_chars=240),
                    "dimensions": [
                        {
                            "dimension": score.get("dimension"),
                            "score": score.get("score"),
                            "source": score.get("source"),
                        }
                        for score in rubric
                        if isinstance(score, dict)
                    ],
                }
            )
    return {
        "status": payload.get("status"),
        "judge_score": payload.get("judge_score"),
        "arena_score": payload.get("arena_score"),
        "real_world_adjustment": payload.get("real_world_adjustment"),
        "judges": judge_rows,
    }


def _operator_events(path: Path, *, limit: int, query: str | None) -> list[object]:
    rows = _read_jsonl_file(path)
    if query:
        needle = query.lower()
        rows = [row for row in rows if needle in json.dumps(row, ensure_ascii=True).lower()]
    return [redact_payload(row) for row in rows[-limit:]]


def _jsonl_tail(path: Path, *, limit: int) -> list[object]:
    return [redact_payload(row) for row in _read_jsonl_file(path)[-limit:]]


def _safe_json_payload(path: Path) -> object:
    payload = _read_json_file(path)
    if payload is None:
        return {"exists": path.exists()}
    return redact_payload(payload)


def _safe_text_excerpt(path: Path, *, max_chars: int) -> dict[str, object]:
    if not path.exists() or not path.is_file():
        return {"exists": False, "text": ""}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"exists": True, "error": str(exc), "text": ""}
    return {
        "exists": True,
        "truncated": len(text) > max_chars,
        "text": redact_text(text, max_chars=max_chars),
    }


def _read_json_file(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl_file(path: Path) -> list[object]:
    if not path.exists() or not path.is_file():
        return []
    rows: list[object] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"malformed": True, "line": redact_text(line, max_chars=500)})
    except OSError:
        return []
    return rows


def _file_status(path: Path) -> dict[str, object]:
    if not path.exists() or not path.is_file():
        return {"exists": False, "size_bytes": 0}
    try:
        stat = path.stat()
    except OSError:
        return {"exists": True, "size_bytes": None}
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


def _heartbeat(runtime: dict[str, Any]) -> dict[str, Any]:
    season = runtime.get("season", {}) if isinstance(runtime.get("season"), dict) else {}
    heartbeat = season.get("heartbeat", {}) if isinstance(season.get("heartbeat"), dict) else {}
    return dict(heartbeat)


def _age_seconds(value: object) -> int | None:
    dt = _parse_time(value)
    if dt is None:
        return None
    return max(0, int((datetime.now(UTC) - dt).total_seconds()))


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _nested(payload: dict[str, Any], key: str, child: str) -> object:
    value = payload.get(key)
    if not isinstance(value, dict):
        return None
    return value.get(child)


def _last_item(value: object) -> object:
    if isinstance(value, list) and value:
        return value[-1]
    return {}


def _count(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1
