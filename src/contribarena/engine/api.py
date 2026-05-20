from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from contribarena.config.schema import RunConfig
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
    def surface() -> dict[str, object]:
        return model.surface_bundle()

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
        return model.stats(season_id)

    @app.get("/api/leaderboard")
    def leaderboard(season_id: str | None = None) -> dict[str, object]:
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
        try:
            self.model.refresh_from_artifacts(self.input_dir)
        except Exception:
            return


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
