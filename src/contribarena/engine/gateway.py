from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contribarena.config import load_run_config
from contribarena.config.schema import DEFAULT_READ_MODEL_RELATIVE, RunConfig, SeasonConfig
from contribarena.engine.provider_preflight import (
    ProviderPreflightResult,
    check_season_provider_connectivity,
)
from contribarena.engine.read_model import SurfaceReadModel
from contribarena.engine.season_runtime import SeasonRuntime
from contribarena.engine.seasons import (
    SeasonStore,
    load_participant_state,
    parse_duration_seconds,
    participant_id_for,
    participant_is_due,
    participant_next_wake_at,
    tracked_open_prs,
)
from contribarena.errors import ContribArenaError


@dataclass(frozen=True)
class GatewayPaths:
    config_path: Path
    artifact_root: Path
    read_model_path: Path
    control_root: Path
    log_root: Path
    pid_path: Path
    state_path: Path
    events_path: Path
    gateway_log_path: Path
    season_log_path: Path
    api_log_path: Path


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


@dataclass(frozen=True)
class DoctorResult:
    checks: list[DoctorCheck]

    @property
    def failed(self) -> list[DoctorCheck]:
        return [check for check in self.checks if check.status in {"failed", "blocked"}]


@dataclass(frozen=True)
class GatewayCommandResult:
    status: str
    message: str
    paths: GatewayPaths
    pid: int | None = None
    state: dict[str, Any] = field(default_factory=dict)


def resolve_gateway_paths(config_path: Path, config: RunConfig) -> GatewayPaths:
    resolved_config = config_path.resolve()
    artifact_root = _config_relative(resolved_config, config.artifacts.output_root)
    read_model_path = _read_model_path(resolved_config, config, artifact_root)
    base = artifact_root.parent
    control_root = base / "control"
    log_root = base / "logs"
    return GatewayPaths(
        config_path=resolved_config,
        artifact_root=artifact_root,
        read_model_path=read_model_path,
        control_root=control_root,
        log_root=log_root,
        pid_path=control_root / "gateway.pid",
        state_path=control_root / "gateway_state.json",
        events_path=control_root / "gateway_events.jsonl",
        gateway_log_path=log_root / "gateway.log",
        season_log_path=log_root / "season.log",
        api_log_path=log_root / "api.log",
    )


def start_gateway(
    *,
    config_path: Path,
    season_id: str | None = None,
    heartbeat_interval: str | None = None,
    skip_doctor: bool = False,
    verbose: bool = False,
) -> GatewayCommandResult:
    config = load_run_config(config_path)
    paths = resolve_gateway_paths(config_path, config)
    existing = _read_pid(paths.pid_path)
    if existing is not None and _pid_alive(existing):
        return GatewayCommandResult(
            status="already_running",
            message=f"Gateway already running pid={existing}",
            paths=paths,
            pid=existing,
            state=load_gateway_state(paths),
        )
    if existing is not None:
        _safe_unlink(paths.pid_path)
    _ensure_operational_dirs(paths)
    doctor: DoctorResult | None = None
    if not skip_doctor:
        doctor = run_doctor(config_path=config_path, season_id=season_id, repair=False)
        if doctor.failed:
            detail = "; ".join(f"{item.name}: {item.detail}" for item in doctor.failed[:5])
            raise ContribArenaError(f"gateway doctor failed before startup: {detail}")
    command = [
        sys.executable,
        "-m",
        "contribarena",
        "gateway-run",
        "--config",
        str(config_path),
    ]
    if season_id:
        command.extend(["--season-id", season_id])
    if heartbeat_interval:
        command.extend(["--heartbeat-interval", heartbeat_interval])
    command.append("--skip-doctor")
    if verbose:
        command.append("--verbose")
    log = paths.gateway_log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log.close()
    _write_pid(paths.pid_path, process.pid)
    _write_gateway_state(
        paths,
        {
            "status": "starting",
            "pid": process.pid,
            "started_at": _now(),
            "config_path": str(paths.config_path),
            "season_id": season_id or (config.season.id if config.season else ""),
            "message": "gateway process spawned",
            "provider_checks": [
                check.__dict__
                for check in (doctor.checks if doctor is not None else [])
                if check.name == "providers" or check.name.startswith("provider:")
            ],
            "doctor_checks": [check.__dict__ for check in (doctor.checks if doctor is not None else [])],
        },
    )
    return GatewayCommandResult(
        status="started",
        message=f"Gateway started pid={process.pid}",
        paths=paths,
        pid=process.pid,
        state=load_gateway_state(paths),
    )


def stop_gateway(*, config_path: Path, timeout_seconds: int = 30) -> GatewayCommandResult:
    config = load_run_config(config_path)
    paths = resolve_gateway_paths(config_path, config)
    api_pid = _pid_from_value(load_gateway_state(paths).get("api_pid"))
    pid = _read_pid(paths.pid_path)
    if pid is None:
        _terminate_pid(api_pid, timeout_seconds=5)
        if api_pid is not None:
            _write_gateway_state(paths, {"api_pid": None, "updated_at": _now()})
        state = load_gateway_state(paths)
        return GatewayCommandResult(
            "stopped",
            _gateway_stop_message("Gateway is not running.", config, paths),
            paths,
            state=state,
        )
    if not _pid_alive(pid):
        _safe_unlink(paths.pid_path)
        _terminate_pid(api_pid, timeout_seconds=5)
        if api_pid is not None:
            _write_gateway_state(paths, {"api_pid": None, "updated_at": _now()})
        state = load_gateway_state(paths)
        return GatewayCommandResult(
            "stale_cleaned",
            _gateway_stop_message(f"Removed stale gateway pid={pid}.", config, paths),
            paths,
            state=state,
        )
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(1, timeout_seconds)
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            _safe_unlink(paths.pid_path)
            _terminate_pid(api_pid, timeout_seconds=5)
            if api_pid is not None:
                _write_gateway_state(paths, {"api_pid": None, "updated_at": _now()})
            state = load_gateway_state(paths)
            return GatewayCommandResult(
                "stopped",
                _gateway_stop_message(f"Gateway stopped pid={pid}.", config, paths),
                paths,
                state=state,
            )
        time.sleep(0.25)
    return GatewayCommandResult(
        "stopping",
        _gateway_stop_message(f"Gateway stop requested pid={pid}; process is still exiting.", config, paths),
        paths,
        pid=pid,
        state=load_gateway_state(paths),
    )


def restart_gateway(
    *,
    config_path: Path,
    season_id: str | None = None,
    heartbeat_interval: str | None = None,
    skip_doctor: bool = False,
    verbose: bool = False,
) -> GatewayCommandResult:
    stopped = stop_gateway(config_path=config_path)
    if stopped.status == "stopping":
        raise ContribArenaError(stopped.message)
    return start_gateway(
        config_path=config_path,
        season_id=season_id,
        heartbeat_interval=heartbeat_interval,
        skip_doctor=skip_doctor,
        verbose=verbose,
    )


def _gateway_stop_message(prefix: str, config: RunConfig, paths: GatewayPaths) -> str:
    try:
        target = _target_season_id(config, None)
        snapshot = build_status(paths.config_path, season_id=target, refresh=False)
    except Exception:  # noqa: BLE001 - stop should remain best-effort.
        return prefix
    queue = snapshot.get("work_queue", {})
    summary = _queue_log_summary(queue)
    running = []
    if isinstance(queue, dict):
        items = queue.get("running_runs", [])
        if isinstance(items, list):
            running = [
                str(item.get("display_name") or item.get("participant_id") or item.get("run_id") or "")
                for item in items
                if isinstance(item, dict)
            ]
    suffix = f" recoverable_running={','.join(item for item in running if item) or 'none'} {summary}"
    return f"{prefix} {suffix}"


def load_gateway_state(paths: GatewayPaths) -> dict[str, Any]:
    if not paths.state_path.exists():
        return {}
    try:
        payload = json.loads(paths.state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_status(config_path: Path, *, season_id: str | None = None, refresh: bool = False) -> dict[str, Any]:
    config = load_run_config(config_path)
    paths = resolve_gateway_paths(config_path, config)
    target = _target_season_id(config, season_id)
    if refresh:
        _refresh_read_model(config, paths)
    pid = _read_pid(paths.pid_path)
    pid_alive = pid is not None and _pid_alive(pid)
    gateway_state = load_gateway_state(paths)
    season_payload = _season_status(config, target)
    read_model_status = _read_model_status(config, paths)
    recent_runs = _recent_runs(paths, target)
    pr_rows = _tracked_prs(config, target)
    work_queue = _work_queue(season_payload, pr_rows, recent_runs)
    status = {
        "generated_at": _now(),
        "paths": {
            "config": str(paths.config_path),
            "artifact_root": str(paths.artifact_root),
            "read_model": str(paths.read_model_path),
            "control_root": str(paths.control_root),
            "log_root": str(paths.log_root),
            "gateway_log": str(paths.gateway_log_path),
            "season_log": str(paths.season_log_path),
            "api_log": str(paths.api_log_path),
        },
        "gateway": {
            "status": "running" if pid_alive else "stopped",
            "pid": pid if pid_alive else None,
            "api_pid": gateway_state.get("api_pid"),
            "api_status": _api_status(gateway_state.get("api_pid")),
            "state": gateway_state.get("status") or "",
            "started_at": gateway_state.get("started_at") or "",
            "uptime_seconds": _elapsed_seconds(gateway_state.get("started_at")) if pid_alive else None,
            "last_heartbeat_at": gateway_state.get("last_heartbeat_at") or "",
            "next_tick_at": gateway_state.get("next_tick_at") or season_payload.get("next_tick_at") or "",
            "last_error": gateway_state.get("last_error") or "",
        },
        "health": _health_summary(config, paths, pid_alive, read_model_status),
        "season": season_payload,
        "runs": {
            "recent": recent_runs,
            "total": read_model_status.get("runs", 0),
            "judged": read_model_status.get("judged_runs", 0),
            "open_or_tracked_prs": read_model_status.get("open_or_tracked_prs", 0),
            "skipped": read_model_status.get("skipped", 0),
            "counted": sum(1 for run in recent_runs if not run.get("ranking_excluded")),
            "excluded": sum(1 for run in recent_runs if run.get("ranking_excluded")),
        },
        "prs": {"tracked": pr_rows, "open": len(pr_rows)},
        "work_queue": work_queue,
        "events": _latest_events(paths, config, target),
    }
    return status


def run_doctor(
    *,
    config_path: Path,
    season_id: str | None = None,
    repair: bool = False,
    skip_provider_preflight: bool = False,
) -> DoctorResult:
    checks: list[DoctorCheck] = []
    try:
        config = load_run_config(config_path)
        paths = resolve_gateway_paths(config_path, config)
        checks.append(DoctorCheck("config", "ok", f"loaded {config_path}"))
    except Exception as exc:  # noqa: BLE001 - doctor reports any loader failure.
        return DoctorResult([DoctorCheck("config", "failed", f"{type(exc).__name__}: {exc}")])
    _doctor_path(checks, "artifact_root", paths.artifact_root, repair=repair)
    if config.season and config.season.state_root is not None:
        _doctor_path(checks, "season_root", _config_relative(config_path.resolve(), config.season.state_root), repair=repair)
    _doctor_path(checks, "control_root", paths.control_root, repair=repair)
    _doctor_path(checks, "log_root", paths.log_root, repair=repair)
    _doctor_path(checks, "read_model_parent", paths.read_model_path.parent, repair=repair)
    target = _target_season_id(config, season_id)
    try:
        season = SeasonStore.from_config(config).load(target, config.season)
        if not season.participants:
            checks.append(DoctorCheck("season", "failed", f"{target} has no participants"))
        elif not any("agent" in participant.role for participant in season.participants):
            checks.append(DoctorCheck("season", "failed", f"{target} has no agent participants"))
        else:
            checks.append(DoctorCheck("season", "ok", f"{target}: {len(season.participants)} participants"))
        _doctor_runtime_state(checks, config, target)
    except Exception as exc:  # noqa: BLE001
        checks.append(DoctorCheck("season", "failed", f"{type(exc).__name__}: {exc}"))
    pid = _read_pid(paths.pid_path)
    if pid is None:
        checks.append(DoctorCheck("pid", "ok", "no gateway pid file"))
    elif _pid_alive(pid):
        checks.append(DoctorCheck("pid", "ok", f"gateway pid={pid} is running"))
    elif repair:
        _safe_unlink(paths.pid_path)
        checks.append(DoctorCheck("pid", "ok", f"removed stale pid={pid}"))
    else:
        checks.append(DoctorCheck("pid", "blocked", f"stale pid file pid={pid}", "run doctor --repair"))
    _doctor_command(checks, "docker", ["docker", "--version"])
    _doctor_workspace_image(checks, config.workspace.image)
    _doctor_read_api(checks, paths)
    if config.governance.live_enabled:
        token_env = config.governance.bot_identity.token_env
        actor = config.governance.bot_identity.actor or "unset"
        if os.environ.get(token_env):
            checks.append(DoctorCheck("github_token", "ok", f"{token_env} set actor={actor}"))
        else:
            checks.append(DoctorCheck("github_token", "failed", f"{token_env} is not set"))
        _doctor_pr_strategy(checks, config)
    else:
        checks.append(DoctorCheck("github_token", "skipped", "governance live mode disabled"))
        checks.append(DoctorCheck("pr_strategy", "skipped", "governance live mode disabled"))
    if skip_provider_preflight:
        checks.append(DoctorCheck("providers", "skipped", "provider preflight skipped"))
    else:
        try:
            preflight = check_season_provider_connectivity(config, season_id=target)
            checks.extend(_provider_checks(preflight))
        except Exception as exc:  # noqa: BLE001
            checks.append(DoctorCheck("providers", "failed", f"{type(exc).__name__}: {exc}"))
    _doctor_ranking_exclusions(checks, paths, target)
    return DoctorResult(checks)


def run_gateway_loop(
    *,
    config_path: Path,
    season_id: str | None = None,
    heartbeat_interval: str | None = None,
    skip_doctor: bool = False,
    verbose: bool = False,
) -> int:
    config = load_run_config(config_path)
    paths = resolve_gateway_paths(config_path, config)
    try:
        return _run_gateway_loop_inner(
            config=config,
            paths=paths,
            config_path=config_path,
            season_id=season_id,
            heartbeat_interval=heartbeat_interval,
            skip_doctor=skip_doctor,
            verbose=verbose,
        )
    except Exception as exc:  # noqa: BLE001 - background process must leave diagnosable state.
        _write_gateway_state(
            paths,
            {
                "status": "crashed",
                "pid": os.getpid(),
                "updated_at": _now(),
                "last_error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise


def _run_gateway_loop_inner(
    *,
    config: RunConfig,
    paths: GatewayPaths,
    config_path: Path,
    season_id: str | None,
    heartbeat_interval: str | None,
    skip_doctor: bool,
    verbose: bool,
) -> int:
    _ensure_operational_dirs(paths)
    _write_pid(paths.pid_path, os.getpid())
    logger = GatewayLogger(paths)
    target = _target_season_id(config, season_id)
    stop_requested = False

    def _request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        logger.event("gateway", "stop_requested", "warning", f"received signal {signum}")
        _write_gateway_state(paths, {"status": "stopping", "pid": os.getpid(), "updated_at": _now()})

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    logger.section("ContribArena gateway starting")
    logger.event("gateway", "starting", "info", "loading configuration", config=str(paths.config_path))
    logger.event(
        "gateway",
        "paths",
        "info",
        "resolved operational paths",
        artifact_root=paths.artifact_root,
        control_root=paths.control_root,
        log_root=paths.log_root,
        read_model=paths.read_model_path,
    )
    _write_gateway_state(
        paths,
        {
            "status": "starting",
            "pid": os.getpid(),
            "started_at": _now(),
            "updated_at": _now(),
            "config_path": str(paths.config_path),
            "season_id": target,
            "artifact_root": str(paths.artifact_root),
            "log_root": str(paths.log_root),
        },
    )
    if not skip_doctor:
        doctor = run_doctor(config_path=config_path, season_id=target, repair=True)
        for check in doctor.checks:
            logger.event("doctor", check.name, check.status, check.detail, hint=check.hint)
        _write_gateway_state(
            paths,
            {
                "pid": os.getpid(),
                "updated_at": _now(),
                "provider_checks": [
                    check.__dict__
                    for check in doctor.checks
                    if check.name == "providers" or check.name.startswith("provider:")
                ],
                "doctor_checks": [check.__dict__ for check in doctor.checks],
            },
        )
        if doctor.failed:
            message = "; ".join(f"{check.name}: {check.detail}" for check in doctor.failed[:5])
            logger.event("gateway", "doctor_failed", "error", message)
            _write_gateway_state(
                paths,
                {"status": "blocked", "pid": os.getpid(), "updated_at": _now(), "last_error": message},
            )
            return 2
    interval = (
        parse_duration_seconds(heartbeat_interval)
        if heartbeat_interval
        else max(1, int(config.controller.interval_seconds))
    )
    runtime = SeasonRuntime()
    api_process = _start_api_process(paths, logger)
    store = SeasonStore.from_config(config)
    season = store.load(target, config.season)
    if season.status == "draft":
        store.transition(target, "active", config.season)
        logger.event("season", "activated", "info", f"activated draft season {target}")
    heartbeat_count = 0
    _write_gateway_state(
        paths,
        {
            "status": "running",
            "pid": os.getpid(),
            "api_pid": api_process.pid if api_process else None,
            "updated_at": _now(),
            "heartbeat_interval": interval,
        },
    )
    while not stop_requested:
        if api_process is not None and api_process.poll() is not None:
            logger.event("api", "exited", "warning", f"read API exited code={api_process.returncode}")
            api_process = None
        if api_process is None:
            api_process = _start_api_process(paths, logger)
            _write_gateway_state(
                paths,
                {
                    "pid": os.getpid(),
                    "api_pid": api_process.pid if api_process else None,
                    "updated_at": _now(),
                },
            )
        heartbeat_count += 1
        started = time.monotonic()
        logger.section(f"Heartbeat {heartbeat_count}")
        logger.event("season", "heartbeat_started", "info", f"season={target}", heartbeat=heartbeat_count)
        try:
            before = build_status(config_path, season_id=target, refresh=False)
            logger.event(
                "season",
                "queue_before_tick",
                "info",
                _queue_log_summary(before.get("work_queue")),
            )
            heartbeat = runtime.tick(config, season_id=target, verbose=verbose)
            _refresh_read_model(config, paths)
            after = build_status(config_path, season_id=target, refresh=False)
            elapsed = time.monotonic() - started
            logger.event(
                "season",
                "heartbeat_completed",
                heartbeat.status,
                heartbeat.detail,
                season_status=heartbeat.season_status,
                paused=heartbeat.paused,
                elapsed_seconds=f"{elapsed:.1f}",
            )
            logger.event(
                "season",
                "queue_after_tick",
                "info",
                _queue_log_summary(after.get("work_queue")),
            )
            _write_gateway_state(
                paths,
                {
                    "status": "sleeping" if heartbeat.season_status != "completed" else "completed",
                    "pid": os.getpid(),
                    "api_pid": api_process.pid if api_process else None,
                    "updated_at": _now(),
                    "last_heartbeat_at": _now(),
                    "last_heartbeat_status": heartbeat.status,
                    "last_heartbeat_detail": heartbeat.detail,
                    "heartbeat_count": heartbeat_count,
                    "next_tick_at": _timestamp_after(interval),
                    "last_error": "",
                },
            )
            if heartbeat.season_status == "completed":
                break
        except Exception as exc:  # noqa: BLE001 - gateway logs and continues after runtime failures.
            logger.event("season", "heartbeat_failed", "error", f"{type(exc).__name__}: {exc}")
            try:
                _refresh_read_model(config, paths)
            except Exception as refresh_exc:  # noqa: BLE001
                logger.event(
                    "read_model",
                    "refresh_after_error_failed",
                    "warning",
                    f"{type(refresh_exc).__name__}: {refresh_exc}",
                )
            _write_gateway_state(
                paths,
                {
                    "status": "sleeping_after_error",
                    "pid": os.getpid(),
                    "api_pid": api_process.pid if api_process else None,
                    "updated_at": _now(),
                    "last_heartbeat_at": _now(),
                    "last_error": f"{type(exc).__name__}: {exc}",
                    "next_tick_at": _timestamp_after(interval),
                },
            )
        for _ in range(interval):
            if stop_requested:
                break
            time.sleep(1)
    logger.event("gateway", "stopped", "info", "gateway loop exited")
    if api_process is not None and api_process.poll() is None:
        api_process.terminate()
        try:
            api_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api_process.kill()
    _write_gateway_state(
        paths,
        {"status": "stopped", "pid": os.getpid(), "api_pid": None, "updated_at": _now()},
    )
    current_pid = _read_pid(paths.pid_path)
    if current_pid == os.getpid():
        _safe_unlink(paths.pid_path)
    return 0


def _start_api_process(paths: GatewayPaths, logger: GatewayLogger) -> subprocess.Popen[str] | None:
    command = [
        sys.executable,
        "-m",
        "contribarena",
        "serve",
        "--config",
        str(paths.config_path),
        "--host",
        "127.0.0.1",
        "--port",
        "8787",
    ]
    log = paths.api_log_path.open("a", encoding="utf-8")
    try:
        process: subprocess.Popen[str] = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            close_fds=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.event("api", "start_failed", "warning", f"{type(exc).__name__}: {exc}")
        return None
    finally:
        log.close()
    logger.event("api", "started", "info", "read API child process started", pid=process.pid)
    return process


class GatewayLogger:
    def __init__(self, paths: GatewayPaths) -> None:
        self.paths = paths

    def section(self, title: str) -> None:
        line = f"\n{'=' * 78}\n{_now()}  {title}\n{'=' * 78}"
        self._append(self.paths.gateway_log_path, line)

    def event(self, source: str, event: str, status: str, message: str, **fields: object) -> None:
        clean_fields = {key: _jsonable(value) for key, value in fields.items()}
        payload = {
            "ts": _now(),
            "source": source,
            "event": event,
            "status": status,
            "message": message,
            "fields": clean_fields,
        }
        suffix = " ".join(f"{key}={value}" for key, value in clean_fields.items() if value not in {"", None})
        text = f"{payload['ts']} [{status.upper():<7}] {source}.{event}: {message}"
        if suffix:
            text = f"{text}  {suffix}"
        self._append(self.paths.gateway_log_path, text)
        if source == "season":
            self._append(self.paths.season_log_path, text)
        self.paths.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")

    @staticmethod
    def _append(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")


def tail_log(paths: GatewayPaths, *, target: str = "all", run_id: str | None = None, lines: int = 80) -> str:
    selected: list[tuple[str, Path]] = []
    if run_id:
        run_path = _run_log_path(paths, run_id)
        if run_path:
            selected.append((f"run:{run_id}", run_path))
    elif target == "gateway":
        selected.append(("gateway", paths.gateway_log_path))
    elif target == "season":
        selected.append(("season", paths.season_log_path))
    elif target == "api":
        selected.append(("api", paths.api_log_path))
    else:
        selected.extend(
            [
                ("gateway", paths.gateway_log_path),
                ("season", paths.season_log_path),
            ]
        )
    chunks: list[str] = []
    for label, path in selected:
        chunks.append(f"==> {label} ({path}) <==")
        chunks.extend(_tail(path, lines))
    return "\n".join(chunks) + ("\n" if chunks else "")


def _season_status(config: RunConfig, season_id: str) -> dict[str, Any]:
    store = SeasonStore.from_config(config)
    season = store.load(season_id, config.season)
    state = store.state(season_id)
    heartbeat = state.get("heartbeat", {}) if isinstance(state.get("heartbeat"), dict) else {}
    participants = [_participant_status(store, season, participant) for participant in season.participants]
    active_runs = sum(int(participant.get("active_runs") or 0) for participant in participants)
    return {
        "id": season.id,
        "name": season.name,
        "status": season.status,
        "paused": bool(state.get("paused", False)),
        "runtime_status": state.get("runtime_status") or "",
        "next_tick_at": state.get("next_tick_at") or "",
        "active_runs": active_runs,
        "heartbeat": heartbeat,
        "participants": participants,
    }


def _participant_status(store: SeasonStore, season: SeasonConfig, participant: Any) -> dict[str, Any]:
    participant_id = participant_id_for(season, participant)
    state = load_participant_state(store, season.id, participant_id)
    replacement = state.get("replacement") if isinstance(state.get("replacement"), dict) else {}
    pending = state.get("pending_run") if isinstance(state.get("pending_run"), dict) else {}
    judgement_retry = state.get("judgement_retry") if isinstance(state.get("judgement_retry"), dict) else {}
    now = datetime.now(UTC)
    is_due = participant_is_due(
        season=season,
        participant=participant,
        state=state,
        now=now,
    )
    next_at = participant_next_wake_at(
        season=season,
        participant=participant,
        participant_id=participant_id,
        state=state,
        now=now,
    )
    status = "WAITING"
    next_action = _relative_time(next_at, now)
    if bool(state.get("active_runs")) or pending.get("status") == "running":
        status = "RUNNING"
        next_action = f"run {pending.get('run_id') or 'pending'}"
    elif replacement.get("status") == "due":
        status = "REPLACEMENT"
        next_action = f"attempt {replacement.get('attempts')}/{replacement.get('max_attempts')}"
    elif replacement.get("status") == "exhausted":
        status = "EXHAUSTED"
        next_action = f"replacement attempts {replacement.get('attempts')}"
    elif judgement_retry.get("status") in {"due", "running"}:
        status = "RETRY"
        next_action = f"judge {judgement_retry.get('attempts')}/{judgement_retry.get('max_attempts')}"
    elif is_due:
        status = "DUE"
        next_action = "queued"
    return {
        "id": participant_id,
        "display_name": _display_name(participant.model),
        "model": participant.model,
        "roles": participant.role,
        "state": status,
        "next_action": next_action,
        "next_at": next_at.isoformat(),
        "is_due": is_due,
        "last_run_id": state.get("last_run_id") or pending.get("run_id") or "",
        "last_run_status": state.get("last_run_status") or "",
        "active_runs": int(state.get("active_runs") or 0),
        "replacement": replacement,
        "judgement_retry": judgement_retry,
        "ranking_state": _participant_ranking_state(status, replacement, judgement_retry),
    }


def _read_model_status(config: RunConfig, paths: GatewayPaths) -> dict[str, Any]:
    if not paths.read_model_path.exists():
        return {"status": "missing", "runs": 0, "judged_runs": 0, "open_or_tracked_prs": 0, "skipped": 0}
    try:
        summary = SurfaceReadModel(paths.read_model_path).status(paths.artifact_root)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": "ok",
        "generated_at": summary.generated_at,
        "runs": summary.runs,
        "seasons": summary.seasons,
        "agents": summary.agents,
        "judged_runs": summary.judged_runs,
        "open_or_tracked_prs": summary.open_or_tracked_prs,
        "skipped": summary.skipped,
    }


def _recent_runs(paths: GatewayPaths, season_id: str) -> list[dict[str, Any]]:
    if paths.read_model_path.exists():
        try:
            return SurfaceReadModel(paths.read_model_path).runs(season_id=season_id, limit=8, offset=0)
        except Exception:
            pass
    runs: list[dict[str, Any]] = []
    for path in sorted(paths.artifact_root.rglob("run_summary.json"), reverse=True)[:20]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            runs.append(payload)
    return runs[:8]


def _tracked_prs(config: RunConfig, season_id: str) -> list[dict[str, Any]]:
    try:
        return tracked_open_prs(SeasonStore.from_config(config), season_id, config.season)
    except Exception:
        return []


def _work_queue(
    season_payload: dict[str, Any],
    pr_rows: list[dict[str, Any]],
    recent_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    participants = season_payload.get("participants", [])
    participant_rows = participants if isinstance(participants, list) else []
    due_wakes = []
    running_runs = []
    replacement = []
    judgement_retries = []
    blocked = []
    for participant in participant_rows:
        if not isinstance(participant, dict):
            continue
        state = str(participant.get("state") or "")
        row = {
            "participant_id": participant.get("id") or "",
            "display_name": participant.get("display_name") or participant.get("model") or "",
            "state": state,
            "next_action": participant.get("next_action") or "",
            "run_id": participant.get("last_run_id") or "",
            "ranking_state": participant.get("ranking_state") or "",
        }
        if state == "DUE":
            due_wakes.append(row)
        elif state == "RUNNING":
            running_runs.append(row)
        elif state == "REPLACEMENT":
            replacement.append(row)
        elif state == "RETRY":
            judgement_retries.append(row)
        elif state == "EXHAUSTED":
            blocked.append(row)
    ranking_excluded = [
        {
            "run_id": run.get("run_id") or "",
            "display_name": _run_display_name(run),
            "reason": run.get("ranking_exclusion_reason") or "excluded",
        }
        for run in recent_runs
        if isinstance(run, dict) and run.get("ranking_excluded")
    ]
    return {
        "due_wakes": due_wakes,
        "running_runs": running_runs,
        "replacement": replacement,
        "judgement_retries": judgement_retries,
        "pr_polls": pr_rows,
        "ranking_excluded": ranking_excluded,
        "blocked": blocked,
        "counts": {
            "due_wakes": len(due_wakes),
            "running_runs": len(running_runs),
            "replacement": len(replacement),
            "judgement_retries": len(judgement_retries),
            "pr_polls": len(pr_rows),
            "ranking_excluded": len(ranking_excluded),
            "blocked": len(blocked),
        },
    }


def _participant_ranking_state(
    status: str,
    replacement: dict[str, Any],
    judgement_retry: dict[str, Any],
) -> str:
    if replacement.get("status") in {"due", "running", "replaced", "failed", "exhausted"}:
        return f"excluded:replacement_{replacement.get('status')}"
    if judgement_retry.get("status") in {"due", "running"}:
        return f"excluded:judgement_retry_{judgement_retry.get('status')}"
    if status in {"RUNNING", "DUE", "WAITING"}:
        return "none"
    return status.lower()


def _queue_log_summary(queue: object) -> str:
    if not isinstance(queue, dict):
        return "queue unavailable"
    counts = queue.get("counts", {}) if isinstance(queue.get("counts"), dict) else {}
    parts = [
        f"{key}={counts.get(key, 0)}"
        for key in (
            "due_wakes",
            "running_runs",
            "replacement",
            "judgement_retries",
            "pr_polls",
            "ranking_excluded",
            "blocked",
        )
    ]
    return " ".join(parts)


def _latest_events(paths: GatewayPaths, config: RunConfig, season_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in _tail(paths.events_path, 20):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    try:
        state = SeasonStore.from_config(config).state(season_id)
        runtime_events = state.get("runtime_events", [])
        if isinstance(runtime_events, list):
            events.extend(item for item in runtime_events[-10:] if isinstance(item, dict))
    except Exception:
        pass
    return events[-20:]


def _health_summary(
    config: RunConfig,
    paths: GatewayPaths,
    pid_alive: bool,
    read_model_status: dict[str, Any],
) -> dict[str, Any]:
    state = load_gateway_state(paths)
    provider_state = _provider_health_from_state(state)
    api_state = _api_status(state.get("api_pid"))
    return {
        "gateway": "OK" if pid_alive else "STOPPED",
        "api": api_state,
        "read_model": read_model_status.get("status", "missing"),
        "providers": provider_state,
        "github": "OK" if (not config.governance.live_enabled or os.environ.get(config.governance.bot_identity.token_env)) else "BLOCKED",
        "docker": "OK" if _command_ok(["docker", "--version"]) else "BLOCKED",
        "paths": "OK" if paths.artifact_root.parent.exists() else "BLOCKED",
    }


def _provider_checks(preflight: ProviderPreflightResult) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for check in preflight.checks:
        status = "ok" if check.status == "ok" else check.status
        checks.append(DoctorCheck(f"provider:{check.model}", status, check.detail or check.status))
    return checks


def _api_status(pid_value: object) -> str:
    try:
        pid = int(pid_value) if pid_value not in {"", None} else None
    except (TypeError, ValueError):
        pid = None
    if pid is None:
        return "MISSING"
    return "OK" if _pid_alive(pid) else "STOPPED"


def _elapsed_seconds(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        started = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - started).total_seconds()))


def _provider_health_from_state(state: dict[str, Any]) -> str:
    provider_checks = state.get("provider_checks")
    if not isinstance(provider_checks, list) or not provider_checks:
        return "unchecked"
    statuses = {
        str(item.get("status") or "").lower()
        for item in provider_checks
        if isinstance(item, dict)
    }
    if statuses and statuses <= {"ok", "skipped"} and "ok" in statuses:
        return "OK"
    if "failed" in statuses or "blocked" in statuses:
        return "BLOCKED"
    if "skipped" in statuses:
        return "SKIPPED"
    return "unknown"


def _doctor_runtime_state(checks: list[DoctorCheck], config: RunConfig, season_id: str) -> None:
    try:
        snapshot = _season_status(config, season_id)
    except Exception as exc:  # noqa: BLE001
        checks.append(DoctorCheck("runtime_state", "failed", f"{type(exc).__name__}: {exc}"))
        return
    participants = snapshot.get("participants", [])
    rows = participants if isinstance(participants, list) else []
    stale_pending: list[str] = []
    replacements: list[str] = []
    judgement_retries: list[str] = []
    exhausted: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("display_name") or row.get("id") or "")
        state = str(row.get("state") or "")
        replacement = row.get("replacement") if isinstance(row.get("replacement"), dict) else {}
        retry = row.get("judgement_retry") if isinstance(row.get("judgement_retry"), dict) else {}
        if state == "RUNNING":
            stale_pending.append(f"{name}:{row.get('next_action') or 'running'}")
        if replacement:
            replacements.append(
                f"{name}:{replacement.get('status')} {replacement.get('attempts')}/{replacement.get('max_attempts')}"
            )
            if replacement.get("status") == "exhausted":
                exhausted.append(name)
        if retry:
            judgement_retries.append(
                f"{name}:{retry.get('status')} {retry.get('attempts')}/{retry.get('max_attempts')}"
            )
            if retry.get("status") == "failed":
                exhausted.append(f"{name}:judge")
    checks.append(
        DoctorCheck(
            "pending_runs",
            "warning" if stale_pending else "ok",
            ", ".join(stale_pending[:5]) if stale_pending else "none",
            "gateway startup will reconcile stale pending runs",
        )
    )
    checks.append(
        DoctorCheck(
            "replacement_queue",
            "warning" if replacements else "ok",
            ", ".join(replacements[:5]) if replacements else "none",
        )
    )
    checks.append(
        DoctorCheck(
            "judgement_retry_queue",
            "warning" if judgement_retries else "ok",
            ", ".join(judgement_retries[:5]) if judgement_retries else "none",
        )
    )
    checks.append(
        DoctorCheck(
            "exhausted_work",
            "warning" if exhausted else "ok",
            ", ".join(exhausted[:5]) if exhausted else "none",
        )
    )


def _doctor_workspace_image(checks: list[DoctorCheck], image: str) -> None:
    if not image:
        checks.append(DoctorCheck("workspace_image", "failed", "workspace.image is empty"))
        return
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks.append(DoctorCheck("workspace_image", "failed", f"{type(exc).__name__}: {exc}"))
        return
    if result.returncode == 0:
        checks.append(DoctorCheck("workspace_image", "ok", image))
        return
    try:
        manifest = subprocess.run(
            ["docker", "manifest", "inspect", image],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks.append(DoctorCheck("workspace_image", "failed", f"{type(exc).__name__}: {exc}"))
        return
    if manifest.returncode == 0:
        checks.append(DoctorCheck("workspace_image", "ok", f"{image} pullable"))
        return
    detail = (manifest.stderr or manifest.stdout or f"{image} not available").strip().splitlines()
    checks.append(
        DoctorCheck(
            "workspace_image",
            "blocked",
            detail[0] if detail else f"{image} not available locally or remotely",
            f"docker pull {image}",
        )
    )


def _doctor_read_api(checks: list[DoctorCheck], paths: GatewayPaths) -> None:
    state = load_gateway_state(paths)
    api_status = _api_status(state.get("api_pid"))
    if api_status == "OK":
        checks.append(DoctorCheck("read_api", "ok", f"api pid={state.get('api_pid')} healthy"))
        return
    if state.get("api_pid") not in {"", None}:
        checks.append(
            DoctorCheck(
                "read_api",
                "warning",
                f"api pid={state.get('api_pid')} is not alive",
                "gateway will restart the API child on the next heartbeat",
            )
        )
        return
    if paths.read_model_path.exists():
        checks.append(DoctorCheck("read_api", "ok", f"read model exists: {paths.read_model_path}"))
    else:
        checks.append(
            DoctorCheck(
                "read_api",
                "warning",
                f"read model missing: {paths.read_model_path}",
                "run status --refresh after first artifacts exist",
            )
        )


def _doctor_pr_strategy(checks: list[DoctorCheck], config: RunConfig) -> None:
    actor = config.governance.bot_identity.actor or ""
    if not config.governance.owned_repositories:
        checks.append(DoctorCheck("pr_strategy", "skipped", "no owned repositories configured"))
        return
    problems: list[str] = []
    for policy in config.governance.owned_repositories:
        strategy = policy.pr_submission.strategy
        fork_owner = policy.pr_submission.fork_owner
        if strategy == "fork" and not (fork_owner or actor):
            problems.append(f"{policy.full_name}: fork strategy needs fork_owner or bot actor")
        if strategy == "upstream_branch" and actor and actor != policy.owner:
            problems.append(f"{policy.full_name}: upstream_branch actor={actor} owner={policy.owner}")
    if problems:
        checks.append(DoctorCheck("pr_strategy", "blocked", "; ".join(problems[:3])))
    else:
        detail = ", ".join(
            f"{policy.full_name}:{policy.pr_submission.strategy}"
            for policy in config.governance.owned_repositories[:3]
        )
        checks.append(DoctorCheck("pr_strategy", "ok", detail or "ok"))


def _doctor_ranking_exclusions(checks: list[DoctorCheck], paths: GatewayPaths, season_id: str) -> None:
    excluded: list[str] = []
    try:
        if paths.read_model_path.exists():
            rows = SurfaceReadModel(paths.read_model_path).runs(season_id=season_id, limit=100, offset=0)
        else:
            rows = _recent_runs(paths, season_id)
    except Exception as exc:  # noqa: BLE001
        checks.append(DoctorCheck("ranking_exclusions", "warning", f"{type(exc).__name__}: {exc}"))
        return
    for run in rows:
        if isinstance(run, dict) and run.get("ranking_excluded"):
            excluded.append(f"{run.get('run_id') or '-'}:{run.get('ranking_exclusion_reason') or 'excluded'}")
    checks.append(
        DoctorCheck(
            "ranking_exclusions",
            "warning" if excluded else "ok",
            ", ".join(excluded[:5]) if excluded else "none",
        )
    )


def _doctor_path(checks: list[DoctorCheck], name: str, path: Path, *, repair: bool) -> None:
    try:
        if repair:
            path.mkdir(parents=True, exist_ok=True)
        writable = path.exists() and os.access(path, os.W_OK)
        checks.append(DoctorCheck(name, "ok" if writable else "failed", str(path)))
    except OSError as exc:
        checks.append(DoctorCheck(name, "failed", f"{path}: {exc}"))


def _doctor_command(checks: list[DoctorCheck], name: str, command: list[str]) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks.append(DoctorCheck(name, "failed", f"{type(exc).__name__}: {exc}"))
        return
    if result.returncode == 0:
        checks.append(DoctorCheck(name, "ok", (result.stdout or "").strip().splitlines()[0] if result.stdout else "ok"))
    else:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip().splitlines()[0]
        checks.append(DoctorCheck(name, "failed", detail))


def _refresh_read_model(config: RunConfig, paths: GatewayPaths) -> None:
    paths.read_model_path.parent.mkdir(parents=True, exist_ok=True)
    SurfaceReadModel(paths.read_model_path).refresh_from_artifacts(paths.artifact_root)


def _write_gateway_state(paths: GatewayPaths, updates: dict[str, Any]) -> None:
    state = load_gateway_state(paths)
    state.update({key: _jsonable(value) for key, value in updates.items()})
    state.setdefault("schema_version", "1")
    state["updated_at"] = updates.get("updated_at") or _now()
    paths.state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    tmp_path = paths.state_path.with_name(f"{paths.state_path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, paths.state_path)


def _ensure_operational_dirs(paths: GatewayPaths) -> None:
    for path in (paths.artifact_root, paths.control_root, paths.log_root, paths.read_model_path.parent):
        path.mkdir(parents=True, exist_ok=True)


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def _write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_from_value(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _terminate_pid(pid: int | None, *, timeout_seconds: int = 5) -> bool:
    if pid is None or not _pid_alive(pid):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    deadline = time.monotonic() + max(1, timeout_seconds)
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return [f"(missing: {path})"]
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, lines):]
    except OSError as exc:
        return [f"(unreadable: {path}: {exc})"]


def _run_log_path(paths: GatewayPaths, run_id: str) -> Path | None:
    if paths.read_model_path.exists():
        run_dir = SurfaceReadModel(paths.read_model_path).run_dir(run_id)
        if run_dir is not None:
            events = run_dir / "operator_events.jsonl"
            return events if events.exists() else run_dir / "run_summary.json"
    for path in paths.artifact_root.rglob("run_summary.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and str(payload.get("run_id") or "") == run_id:
            events = path.parent / "operator_events.jsonl"
            return events if events.exists() else path
    return None


def _target_season_id(config: RunConfig, season_id: str | None) -> str:
    target = season_id or (config.season.id if config.season else "")
    if not target:
        raise ContribArenaError("season_id is required")
    return target


def _config_relative(config_path: Path, path: Path) -> Path:
    return path if path.is_absolute() else config_path.parent / path


def _read_model_path(config_path: Path, config: RunConfig, artifact_root: Path) -> Path:
    path = config.backend.read_model_path
    if path.is_absolute():
        return path
    if path == DEFAULT_READ_MODEL_RELATIVE:
        return artifact_root.parent / "read_model.sqlite"
    return _config_relative(config_path, path)


def _display_name(model: str) -> str:
    if "/" in model:
        return model.rsplit("/", 1)[-1]
    return model


def _run_display_name(run: dict[str, Any]) -> str:
    agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
    model = str(run.get("model") or "")
    raw = str(agent.get("handle") or agent.get("name") or model or "-")
    if raw.startswith("season_") and ":" in raw:
        raw = raw.rsplit(":", 1)[-1]
    if raw == "builtin" and model:
        raw = model
    if "/" in raw:
        raw = raw.rsplit("/", 1)[-1]
    return raw


def _relative_time(target: datetime, now: datetime) -> str:
    delta = int((target - now).total_seconds())
    if delta <= 0:
        return "now"
    if delta < 60:
        return f"+{delta}s"
    if delta < 3600:
        return f"+{delta // 60}m"
    return f"+{delta // 3600}h{(delta % 3600) // 60:02d}m"


def _timestamp_after(seconds: int) -> str:
    return datetime.fromtimestamp(time.time() + seconds, tz=UTC).isoformat()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _command_ok(command: list[str]) -> bool:
    try:
        return subprocess.run(command, capture_output=True, check=False, timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _jsonable(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
