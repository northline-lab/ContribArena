from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from contribarena.config.schema import RunConfig
from contribarena.engine.controller import ControllerTickResult, LocalController
from contribarena.engine.read_model import SurfaceReadModel
from contribarena.engine.runtime_config import apply_output_dir
from contribarena.engine.seasons import SeasonStore
from contribarena.errors import ConfigError


@dataclass(frozen=True)
class SeasonHeartbeatResult:
    season_id: str
    status: str
    season_status: str
    paused: bool
    tick: ControllerTickResult | None = None
    detail: str = ""


@dataclass(frozen=True)
class SeasonRuntimeResult:
    season_id: str
    status: str
    heartbeats: list[SeasonHeartbeatResult]


class SeasonRuntime:
    """Operator-facing runtime for one season heartbeat loop."""

    def __init__(
        self,
        *,
        controller: LocalController | None = None,
        read_model: SurfaceReadModel | None = None,
    ) -> None:
        self.controller = controller or LocalController()
        self.read_model = read_model

    def start(
        self,
        config: RunConfig,
        *,
        season_id: str | None = None,
        output_dir: Path | None = None,
        heartbeat_interval_seconds: int | None = None,
        max_heartbeats: int | None = None,
        verbose: bool = False,
    ) -> SeasonRuntimeResult:
        run_config = apply_output_dir(config, output_dir)
        target = _target_season_id(run_config, season_id)
        store = SeasonStore.from_config(run_config)
        season = store.load(target, run_config.season)
        if season.status == "draft":
            store.transition(target, "active", run_config.season)
        store.record_runtime_status(target, runtime_status="running", fallback=run_config.season)
        heartbeats: list[SeasonHeartbeatResult] = []
        count = 0
        try:
            while True:
                store.record_runtime_status(target, runtime_status="running", fallback=run_config.season)
                heartbeat = self.tick(
                    run_config,
                    season_id=target,
                    output_dir=output_dir,
                    verbose=verbose,
                )
                heartbeats.append(heartbeat)
                count += 1
                if heartbeat.season_status == "completed":
                    store.record_runtime_status(target, runtime_status="completed", fallback=run_config.season)
                    return SeasonRuntimeResult(target, "completed", heartbeats)
                if max_heartbeats is not None and count >= max_heartbeats:
                    store.record_runtime_status(target, runtime_status="stopped", fallback=run_config.season)
                    return SeasonRuntimeResult(target, heartbeat.status, heartbeats)
                interval = heartbeat_interval_seconds
                if interval is None:
                    interval = run_config.controller.interval_seconds
                next_tick = datetime.now(UTC) + timedelta(seconds=max(1, interval))
                store.record_runtime_status(
                    target,
                    runtime_status="sleeping",
                    next_tick_at=next_tick.isoformat(),
                    fallback=run_config.season,
                )
                time.sleep(max(1, interval))
        except KeyboardInterrupt:
            store.record_runtime_status(target, runtime_status="interrupted", fallback=run_config.season)
            raise

    def tick(
        self,
        config: RunConfig,
        *,
        season_id: str | None = None,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> SeasonHeartbeatResult:
        run_config = apply_output_dir(config, output_dir)
        target = _target_season_id(run_config, season_id)
        store = SeasonStore.from_config(run_config)
        store.record_heartbeat_started(target, run_config.season)
        try:
            season = store.load(target, run_config.season)
            state = store.state(target)
            paused = bool(state.get("paused", False))
            if season.status == "completed":
                tick = self.controller._run_external_lifecycle_tick(run_config)
                self._refresh_read_model(run_config)
                detail = tick.status if tick is not None else "season_completed"
                store.record_heartbeat_completed(
                    target,
                    status="completed",
                    detail=detail,
                    fallback=run_config.season,
                )
                return SeasonHeartbeatResult(
                    season_id=target,
                    status="completed",
                    season_status=season.status,
                    paused=paused,
                    tick=tick,
                    detail=detail,
                )
            if season.status == "observing" or paused:
                tick = self.controller._run_external_lifecycle_tick(run_config)
                self._refresh_read_model(run_config)
                detail = "paused" if paused else "observing"
                if tick is not None:
                    detail = tick.status
                store.record_heartbeat_completed(
                    target,
                    status="ok",
                    detail=detail,
                    fallback=run_config.season,
                )
                return SeasonHeartbeatResult(
                    season_id=target,
                    status="ok",
                    season_status=season.status,
                    paused=paused,
                    tick=tick,
                    detail=detail,
                )
            if season.status != "active":
                raise ConfigError(f"season_not_active: {target}")
            tick = self.controller.run_once(run_config, output_dir=output_dir, verbose=verbose)
            self._refresh_read_model(run_config)
            status = "ok" if tick.status not in {"run_failed", "blocked"} else tick.status
            store.record_heartbeat_completed(
                target,
                status=status,
                detail=tick.status,
                fallback=run_config.season,
            )
            return SeasonHeartbeatResult(
                season_id=target,
                status=status,
                season_status=season.status,
                paused=paused,
                tick=tick,
                detail=tick.status,
            )
        except Exception as exc:
            store.record_heartbeat_completed(
                target,
                status="error",
                error=str(exc),
                fallback=run_config.season,
            )
            raise

    def _refresh_read_model(self, config: RunConfig) -> None:
        model = self.read_model or SurfaceReadModel(config.backend.read_model_path)
        model.refresh_from_artifacts(config.artifacts.output_root)


def _target_season_id(config: RunConfig, season_id: str | None) -> str:
    target = season_id or (config.season.id if config.season else "")
    if not target:
        raise ConfigError("season_id is required")
    return target
