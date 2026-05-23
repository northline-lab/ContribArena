from __future__ import annotations

import json
import sys
import shutil
import importlib.metadata
import subprocess
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contribarena import __version__
from contribarena.config import load_run_config, write_starter_config
from contribarena.config.schema import DEFAULT_READ_MODEL_RELATIVE
from contribarena.engine import LocalController, Runner
from contribarena.engine.gateway import (
    DoctorResult,
    build_status,
    restart_gateway,
    resolve_gateway_paths,
    run_doctor,
    run_gateway_loop,
    start_gateway,
    stop_gateway,
    tail_log,
)
from contribarena.engine.judge_refresh import refresh_judgement
from contribarena.engine.provider_preflight import (
    check_season_provider_connectivity,
    raise_for_provider_preflight,
)
from contribarena.engine.read_model import SurfaceReadModel
from contribarena.engine.seasons import (
    SeasonStore,
    cleanup_season_workspaces,
    parse_duration_seconds,
    write_leaderboard_snapshot,
)
from contribarena.engine.season_runtime import SeasonRuntime
from contribarena.engine.surface_indexer import index_surface_data
from contribarena.engine.surface_indexer import build_leaderboard_snapshot
from contribarena.errors import ContribArenaError

app = typer.Typer(help="ContribArena control plane commands.")
surface_app = typer.Typer(help="Build public read-only surface data.")
season_app = typer.Typer(help="Manage season state and participant admission.")
season_workspace_app = typer.Typer(help="Manage persistent season workspaces.")
pr_app = typer.Typer(help="Inspect and refresh tracked PR lifecycle state.")
runs_app = typer.Typer(help="Inspect run summaries and run logs.")
console = Console()


@app.command()
def init(output: Path = typer.Option(Path("run_config.yaml"), "--output", "-o")) -> None:
    """Generate a starter shadow run config."""
    try:
        write_starter_config(output)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Wrote {output}")


@app.command()
def validate(config: Path = typer.Option(..., "--config", "-c")) -> None:
    """Validate a run config without executing a run."""
    try:
        load_run_config(config)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Config valid: {config}")


@app.command()
def up(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = typer.Option(None, "--season-id"),
    heartbeat_interval: str | None = typer.Option(None, "--heartbeat-interval"),
    skip_doctor: bool = typer.Option(False, "--skip-doctor"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start or resume the local ContribArena gateway in the background."""
    try:
        result = start_gateway(
            config_path=config,
            season_id=season_id,
            heartbeat_interval=heartbeat_interval,
            skip_doctor=skip_doctor,
            verbose=verbose,
        )
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    console.print(_gateway_command_panel(result.message, result.status, result.state))
    _print_status_snapshot(config, season_id=season_id)


@app.command()
def down(
    config: Path = typer.Option(..., "--config", "-c"),
    timeout_seconds: int = typer.Option(30, "--timeout-seconds", min=1),
) -> None:
    """Stop the local ContribArena gateway gracefully."""
    try:
        result = stop_gateway(config_path=config, timeout_seconds=timeout_seconds)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    console.print(_gateway_command_panel(result.message, result.status, result.state))
    _print_status_snapshot(config)


@app.command()
def restart(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = typer.Option(None, "--season-id"),
    heartbeat_interval: str | None = typer.Option(None, "--heartbeat-interval"),
    skip_doctor: bool = typer.Option(False, "--skip-doctor"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Restart the gateway and rehydrate season state before new dispatch."""
    try:
        result = restart_gateway(
            config_path=config,
            season_id=season_id,
            heartbeat_interval=heartbeat_interval,
            skip_doctor=skip_doctor,
            verbose=verbose,
        )
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    console.print(_gateway_command_panel(result.message, result.status, result.state))
    _print_status_snapshot(config, season_id=season_id)


@app.command()
def status(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = typer.Option(None, "--season-id"),
    refresh: bool = typer.Option(False, "--refresh"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show the gateway, season, participant, run, PR, and health status panel."""
    try:
        snapshot = build_status(config, season_id=season_id, refresh=refresh)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    if json_output:
        typer.echo(json.dumps(snapshot, indent=2, sort_keys=True))
        return
    if not console.is_terminal:
        typer.echo(_plain_status(snapshot))
        return
    console.print(_status_panel(snapshot))


@app.command()
def dashboard(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = typer.Option(None, "--season-id"),
    refresh_seconds: float = typer.Option(5.0, "--refresh-seconds", min=1.0),
) -> None:
    """Open a live terminal dashboard over the gateway status schema."""
    try:
        initial = build_status(config, season_id=season_id)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    if not sys.stdout.isatty():
        typer.echo(_plain_status(initial), nl=False)
        return
    try:
        with Live(_status_panel(initial), console=console, refresh_per_second=2, screen=False) as live:
            while True:
                time.sleep(refresh_seconds)
                live.update(_status_panel(build_status(config, season_id=season_id)))
    except KeyboardInterrupt:
        return


@app.command()
def logs(
    config: Path = typer.Option(..., "--config", "-c"),
    target: str = typer.Option("all", "--target"),
    run_id: str | None = typer.Option(None, "--run"),
    latest_run: bool = typer.Option(False, "--latest-run"),
    lines: int = typer.Option(80, "--lines", min=1, max=1000),
    follow: bool = typer.Option(False, "--follow", "-f"),
) -> None:
    """Tail gateway, season, API, or run logs without remembering file paths."""
    try:
        run_config = load_run_config(config)
        paths = resolve_gateway_paths(config, run_config)
        selected_run = run_id
        if latest_run or run_id == "latest":
            recent = build_status(config).get("runs", {}).get("recent", [])
            if recent:
                selected_run = str(recent[0].get("run_id") or "")
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    try:
        while True:
            console.clear() if follow and console.is_terminal else None
            typer.echo(tail_log(paths, target=target, run_id=selected_run, lines=lines), nl=False)
            if not follow:
                break
            time.sleep(2)
    except KeyboardInterrupt:
        return


@app.command()
def doctor(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = typer.Option(None, "--season-id"),
    repair: bool = typer.Option(False, "--repair"),
    skip_provider_preflight: bool = typer.Option(False, "--skip-provider-preflight"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run gateway preflight checks and explain actionable failures."""
    result = run_doctor(
        config_path=config,
        season_id=season_id,
        repair=repair,
        skip_provider_preflight=skip_provider_preflight,
    )
    if json_output:
        typer.echo(
            json.dumps(
                {"checks": [check.__dict__ for check in result.checks]},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        console.print(_doctor_panel(result))
    if result.failed:
        raise typer.Exit(1)


@app.command("gateway-run", hidden=True)
def gateway_run(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = typer.Option(None, "--season-id"),
    heartbeat_interval: str | None = typer.Option(None, "--heartbeat-interval"),
    skip_doctor: bool = typer.Option(False, "--skip-doctor"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Internal gateway loop entrypoint used by `contribarena up`."""
    raise typer.Exit(
        run_gateway_loop(
            config_path=config,
            season_id=season_id,
            heartbeat_interval=heartbeat_interval,
            skip_doctor=skip_doctor,
            verbose=verbose,
        )
    )


@pr_app.command("ps")
def pr_ps(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Show tracked PR lifecycle state for the selected season."""
    try:
        snapshot = build_status(config, season_id=season_id)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    console.print(_pr_table(snapshot))


@pr_app.command("refresh")
def pr_refresh(config: Path = typer.Option(..., "--config", "-c")) -> None:
    """Observe tracked PR lifecycle without scheduling new agent work."""
    try:
        run_config = load_run_config(config)
        tick = LocalController()._run_external_lifecycle_tick(run_config)
        artifact_root = _config_relative(config, run_config.artifacts.output_root)
        read_model_path = _read_model_path(config, run_config, artifact_root)
        SurfaceReadModel(read_model_path).refresh_from_artifacts(artifact_root)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    status_text = tick.status if tick is not None else "no_lifecycle_work"
    console.print(Panel(f"PR lifecycle refresh: {_label(status_text)}", title="PR Refresh"))


@runs_app.command("ls")
def runs_ls(
    config: Path = typer.Option(..., "--config", "-c"),
    input_dir: Path | None = typer.Option(None, "--input-dir"),
    season_id: str | None = typer.Option(None, "--season-id"),
    status_filter: str | None = typer.Option(None, "--status"),
    agent: str | None = typer.Option(None, "--agent"),
    query: str | None = typer.Option(None, "--query", "-q"),
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    offset: int = typer.Option(0, "--offset", min=0),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """List runs with ranking inclusion and exclusion state."""
    _runs_list_impl(
        config=config,
        input_dir=input_dir,
        season_id=season_id,
        status_filter=status_filter,
        agent=agent,
        query=query,
        limit=limit,
        offset=offset,
        refresh=refresh,
    )


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(...),
    config: Path = typer.Option(..., "--config", "-c"),
    input_dir: Path | None = typer.Option(None, "--input-dir"),
    artifact: str | None = typer.Option(None, "--artifact"),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """Show one run summary or artifact."""
    _show_run_impl(
        run_id=run_id,
        config=config,
        input_dir=input_dir,
        artifact=artifact,
        refresh=refresh,
    )


@runs_app.command("tail")
def runs_tail(
    run_id: str = typer.Argument(...),
    config: Path = typer.Option(..., "--config", "-c"),
    lines: int = typer.Option(80, "--lines", min=1, max=1000),
    follow: bool = typer.Option(False, "--follow", "-f"),
) -> None:
    """Tail one run's operator events."""
    logs(config=config, run_id=run_id, lines=lines, follow=follow)


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c"),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o"),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Override run.model for this invocation, for example compatible/qwen36plus.",
    ),
    max_candidate_repos: int | None = typer.Option(None, "--max-candidate-repos", min=1),
    max_opportunities: int | None = typer.Option(None, "--max-opportunities", min=1),
    max_duplicate_checks: int | None = typer.Option(None, "--max-duplicate-checks", min=1),
    max_repo_switches: int | None = typer.Option(None, "--max-repo-switches", min=0),
    max_opportunity_switches: int | None = typer.Option(None, "--max-opportunity-switches", min=0),
    max_review_rounds: int | None = typer.Option(None, "--max-review-rounds", min=0),
    season_id: str | None = typer.Option(None, "--season-id"),
    participant_id: str | None = typer.Option(None, "--participant-id"),
    wake_source: str = typer.Option("manual", "--wake-source"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Execute a ContribArena agent run."""
    try:
        run_config = load_run_config(config)
        if model:
            run_config = _with_model_override(run_config, model)
        if season_id or participant_id:
            run_config = _with_season_run_override(
                run_config,
                season_id=season_id,
                participant_id=participant_id,
                wake_source=wake_source,
            )
        run_config = _with_budget_overrides(
            run_config,
            max_candidate_repos=max_candidate_repos,
            max_opportunities=max_opportunities,
            max_duplicate_checks=max_duplicate_checks,
            max_repo_switches=max_repo_switches,
            max_opportunity_switches=max_opportunity_switches,
            max_review_rounds=max_review_rounds,
        )
        result = Runner().run(run_config, output_dir=output_dir, verbose=verbose)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Run completed: {result.run_dir}")
    typer.echo(f"  Status:      {result.status}")
    typer.echo(f"  Tool calls:  {result.tool_calls}")


@season_app.command("activate")
def season_activate(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Activate a configured season."""
    _season_transition(config, season_id, "active")


@season_app.command("start")
def season_start(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = None,
    heartbeat_interval: str | None = typer.Option(None, "--heartbeat-interval"),
    max_heartbeats: int | None = typer.Option(None, "--max-heartbeats", min=1),
    skip_provider_preflight: bool = typer.Option(False, "--skip-provider-preflight"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start or resume the long-running season heartbeat."""
    try:
        run_config = load_run_config(config)
        interval_seconds = (
            parse_duration_seconds(heartbeat_interval)
            if heartbeat_interval is not None
            else None
        )
        if not skip_provider_preflight:
            typer.echo("Checking season provider connectivity...")
            preflight = check_season_provider_connectivity(run_config, season_id=season_id)
            raise_for_provider_preflight(preflight)
            checked = [check for check in preflight.checks if check.status != "skipped"]
            skipped = [check for check in preflight.checks if check.status == "skipped"]
            typer.echo(
                f"Provider preflight passed: {len(checked)} checked, {len(skipped)} skipped."
            )
        result = SeasonRuntime().start(
            run_config,
            season_id=season_id,
            heartbeat_interval_seconds=interval_seconds,
            max_heartbeats=max_heartbeats,
            verbose=verbose,
        )
    except KeyboardInterrupt:
        typer.echo("Season runtime interrupted; runtime state was marked interrupted.")
        return
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Season {result.season_id}: {result.status}")
    typer.echo(f"Heartbeats:  {len(result.heartbeats)}")
    for index, heartbeat in enumerate(result.heartbeats, start=1):
        typer.echo(
            f"  {index}: {heartbeat.status} "
            f"(season={heartbeat.season_status}, paused={str(heartbeat.paused).lower()}, detail={heartbeat.detail})"
        )


@season_app.command("tick")
def season_tick(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = None,
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one season heartbeat."""
    try:
        run_config = load_run_config(config)
        result = SeasonRuntime().tick(run_config, season_id=season_id, verbose=verbose)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Season {result.season_id}: {result.status}")
    typer.echo(f"Status:      {result.season_status}")
    typer.echo(f"Paused:      {str(result.paused).lower()}")
    typer.echo(f"Detail:      {result.detail}")


@season_app.command("pause")
def season_pause(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Pause automatic participant wakes without changing lifecycle status."""
    _season_pause(config, season_id, paused=True)


@season_app.command("resume")
def season_resume(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Resume automatic participant wakes."""
    _season_pause(config, season_id, paused=False)


@season_app.command("observe")
def season_observe(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Stop admitting new work while continuing observation."""
    _season_transition(config, season_id, "observing")


@season_app.command("complete")
def season_complete(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = None,
    force_with_open_prs: bool = typer.Option(False, "--force-with-open-prs"),
) -> None:
    """Freeze the season snapshot and mark it completed."""
    _season_transition(config, season_id, "completed", force_with_open_prs=force_with_open_prs)
    cleanup = _season_workspace_clean(config, season_id, quiet=True)
    typer.echo(f"Cleaned workspaces: {cleanup}")


@season_workspace_app.command("clean")
def season_workspace_clean(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Force cleanup of persistent season workspaces and participant memory."""
    count = _season_workspace_clean(config, season_id)
    typer.echo(f"Cleaned workspaces: {count}")


@season_app.command("status")
def season_status(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Show one season's state."""
    run_config = load_run_config(config)
    target = season_id or (run_config.season.id if run_config.season else "season_0")
    store = SeasonStore.from_config(run_config)
    season = store.load(target, run_config.season)
    state = store.state(target)
    heartbeat = state.get("heartbeat", {}) if isinstance(state.get("heartbeat"), dict) else {}
    typer.echo(f"Season:      {season.id}")
    typer.echo(f"Name:        {season.name}")
    typer.echo(f"Status:      {season.status}")
    typer.echo(f"Paused:      {str(bool(state.get('paused', False))).lower()}")
    typer.echo(f"Heartbeat:   {heartbeat.get('last_status') or 'never'}")
    typer.echo(f"Runtime:     {state.get('runtime_status') or ''}")
    typer.echo(f"Next tick:   {state.get('next_tick_at') or ''}")
    typer.echo(f"Last start:  {heartbeat.get('last_started_at') or ''}")
    typer.echo(f"Last end:    {heartbeat.get('last_completed_at') or ''}")
    typer.echo(f"Last error:  {heartbeat.get('last_error') or ''}")
    typer.echo(f"Participants:{len(season.participants):>3}")
    typer.echo(f"Discovery:   {season.discovery_profile.scope}")


@season_app.command("ps")
def season_ps(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Show participant due, retry, replacement, and score eligibility state."""
    try:
        snapshot = build_status(config, season_id=season_id)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    console.print(_participants_table(snapshot))


@season_app.command("events")
def season_events(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = None,
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """Show recent season heartbeat and scheduler events."""
    try:
        snapshot = build_status(config, season_id=season_id)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    events = snapshot.get("events", []) if isinstance(snapshot.get("events"), list) else []
    for event in events[-limit:]:
        if not isinstance(event, dict):
            continue
        typer.echo(
            f"{event.get('ts') or ''} "
            f"{event.get('source') or event.get('event') or '-'} "
            f"{event.get('status') or event.get('heartbeat_status') or '-'} "
            f"{event.get('message') or event.get('detail') or event.get('error') or ''}"
        )


@season_app.command("logs")
def season_logs(
    config: Path = typer.Option(..., "--config", "-c"),
    lines: int = typer.Option(80, "--lines", min=1, max=1000),
    follow: bool = typer.Option(False, "--follow", "-f"),
) -> None:
    """Tail season runtime logs."""
    logs(config=config, target="season", lines=lines, follow=follow)


@season_app.command("restart")
def season_restart(
    config: Path = typer.Option(..., "--config", "-c"),
    season_id: str | None = typer.Option(None, "--season-id"),
    heartbeat_interval: str | None = typer.Option(None, "--heartbeat-interval"),
    skip_doctor: bool = typer.Option(False, "--skip-doctor"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Restart the gateway-backed season worker."""
    restart(
        config=config,
        season_id=season_id,
        heartbeat_interval=heartbeat_interval,
        skip_doctor=skip_doctor,
        verbose=verbose,
    )


@season_app.command("inspect")
def season_inspect(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Show detailed season runtime state."""
    try:
        run_config = load_run_config(config)
        target = season_id or (run_config.season.id if run_config.season else "season_0")
        store = SeasonStore.from_config(run_config)
        season = store.load(target, run_config.season)
        state = store.state(target)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(json.dumps({"season": season.model_dump(mode="json"), "state": state}, indent=2, sort_keys=True))


@season_app.command("validate")
def season_validate(config: Path = typer.Option(..., "--config", "-c"), season_id: str | None = None) -> None:
    """Preflight season operation without launching runs."""
    try:
        run_config = load_run_config(config)
        target = season_id or (run_config.season.id if run_config.season else "season_0")
        season = SeasonStore.from_config(run_config).load(target, run_config.season)
        if not season.participants:
            raise ContribArenaError("season has no participants")
        if not any("agent" in participant.role for participant in season.participants):
            raise ContribArenaError("season has no agent participants")
        run_config.artifacts.output_root.mkdir(parents=True, exist_ok=True)
        run_config.backend.read_model_path.parent.mkdir(parents=True, exist_ok=True)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Season valid: {season.id}")
    typer.echo(f"Participants: {len(season.participants)}")
    typer.echo(f"Discovery:    {season.discovery_profile.scope}")


@season_app.command("list")
def season_list(config: Path = typer.Option(..., "--config", "-c")) -> None:
    """List configured seasons."""
    run_config = load_run_config(config)
    seasons = SeasonStore.from_config(run_config).list(run_config.season)
    if not seasons:
        typer.echo("No seasons configured.")
        return
    typer.echo(f"{'Season':<20} {'Status':<10} {'Participants':>12} {'Scope':<10}")
    for season in seasons:
        typer.echo(
            f"{season.id:<20} {season.status:<10} {len(season.participants):>12} "
            f"{season.discovery_profile.scope:<10}"
        )


@app.command("run-matrix")
def run_matrix(
    config: Path = typer.Option(..., "--config", "-c"),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o"),
    models: list[str] | None = typer.Option(
        None,
        "--model",
        help=(
            "Model to run. Repeat for multiple models. "
            "Defaults to all configured provider models, or run.model when none are configured."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the same benchmark config once for each selected model."""
    try:
        run_config = load_run_config(config)
        selected_models = models or _configured_models(run_config)
        if not selected_models:
            raise ContribArenaError("no models selected for run-matrix")
        results = []
        for model_name in selected_models:
            matrix_config = _with_model_override(run_config, model_name)
            result = Runner().run(matrix_config, output_dir=output_dir, verbose=verbose)
            results.append((model_name, result))
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo("Run matrix completed:")
    for model_name, result in results:
        typer.echo(f"  {model_name}: {result.status} ({result.run_dir})")


@app.command()
def controller(
    config: Path = typer.Option(..., "--config", "-c"),
    output_dir: Path | None = typer.Option(None, "--output-dir", "-o"),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Override run.model for controller-started runs.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the local ContribArena controller loop."""
    try:
        run_config = load_run_config(config)
        if model:
            run_config = _with_model_override(run_config, model)
        result = LocalController().run(run_config, output_dir=output_dir, verbose=verbose)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Controller completed: {result.status}")
    typer.echo(f"  Ticks: {len(result.ticks)}")
    for index, tick in enumerate(result.ticks, start=1):
        detail = tick.decision.status if tick.decision else "n/a"
        typer.echo(f"  Tick {index}: {tick.status} ({detail})")
        if tick.decision and tick.decision.reasons:
            for reason in tick.decision.reasons:
                typer.echo(f"    - {reason}")
        if tick.run_result:
            typer.echo(f"    Run: {tick.run_result.run_dir}")
            typer.echo(f"    Status: {tick.run_result.status}")


@app.command("backend-status")
def backend_status(
    config: Path = typer.Option(..., "--config", "-c"),
    input_dir: Path | None = typer.Option(None, "--input-dir"),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """Inspect benchmark backend state without starting new work."""
    try:
        run_config = load_run_config(config)
        artifact_root = input_dir or _config_relative(config, run_config.artifacts.output_root)
        read_model_path = _read_model_path(config, run_config, artifact_root)
        model = SurfaceReadModel(read_model_path)
        if refresh or not read_model_path.exists():
            refresh = model.refresh_from_artifacts(artifact_root)
            typer.echo(f"Read model refreshed: {refresh.runs_indexed} runs")
        summary = model.status(artifact_root)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo("Benchmark status:")
    typer.echo(f"  Input:       {summary.input_dir}")
    typer.echo(f"  Read model:  {summary.db_path}")
    typer.echo(f"  Generated:   {summary.generated_at or 'never'}")
    typer.echo(f"  Runs:        {summary.runs}")
    typer.echo(f"  Seasons:     {summary.seasons}")
    typer.echo(f"  Agents:      {summary.agents}")
    typer.echo(f"  Judged:      {summary.judged_runs}")
    typer.echo(f"  Open PRs:    {summary.open_or_tracked_prs}")
    typer.echo(f"  Skipped:     {summary.skipped}")


def _runs_list_impl(
    config: Path = typer.Option(..., "--config", "-c"),
    input_dir: Path | None = typer.Option(None, "--input-dir"),
    season_id: str | None = typer.Option(None, "--season-id"),
    status_filter: str | None = typer.Option(None, "--status"),
    agent: str | None = typer.Option(None, "--agent"),
    query: str | None = typer.Option(None, "--query", "-q"),
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    offset: int = typer.Option(0, "--offset", min=0),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    try:
        run_config = load_run_config(config)
        artifact_root = input_dir or _config_relative(config, run_config.artifacts.output_root)
        read_model_path = _read_model_path(config, run_config, artifact_root)
        model = SurfaceReadModel(read_model_path)
        if refresh or not read_model_path.exists():
            model.refresh_from_artifacts(artifact_root)
        rows = model.runs(
            season_id=season_id,
            status=status_filter,
            agent=agent,
            query=query,
            limit=limit,
            offset=offset,
        )
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    if not rows:
        typer.echo("No runs found.")
        return
    typer.echo(f"{'Run ID':<28} {'Status':<11} {'Agent':<16} {'Repo':<26} {'Score':>5} {'Ranking':<24}")
    for row in rows:
        agent_info = row.get("agent", {}) if isinstance(row.get("agent"), dict) else {}
        repo = row.get("repository", {}) if isinstance(row.get("repository"), dict) else {}
        judgement = row.get("judgement", {}) if isinstance(row.get("judgement"), dict) else {}
        score = judgement.get("arena_score")
        ranking = (
            f"excluded:{row.get('ranking_exclusion_reason')}"
            if row.get("ranking_excluded")
            else "counted"
        )
        typer.echo(
            f"{_clip(str(row.get('run_id') or ''), 28):<28} "
            f"{_clip(str(row.get('run_status') or 'unknown'), 11):<11} "
            f"{_clip(str(agent_info.get('handle') or agent_info.get('name') or ''), 16):<16} "
            f"{_clip(str(repo.get('full_name') or ''), 26):<26} "
            f"{_score_text(score):>5} "
            f"{_clip(ranking, 24):<24}"
        )


@app.command("show")
def show_run(
    run_id: str = typer.Argument(...),
    config: Path = typer.Option(..., "--config", "-c"),
    input_dir: Path | None = typer.Option(None, "--input-dir"),
    artifact: str | None = typer.Option(None, "--artifact"),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """Show one run summary or one artifact from the run directory."""
    _show_run_impl(
        run_id=run_id,
        config=config,
        input_dir=input_dir,
        artifact=artifact,
        refresh=refresh,
    )


def _show_run_impl(
    *,
    run_id: str,
    config: Path,
    input_dir: Path | None = None,
    artifact: str | None = None,
    refresh: bool = False,
) -> None:
    try:
        run_config = load_run_config(config)
        artifact_root = input_dir or _config_relative(config, run_config.artifacts.output_root)
        read_model_path = _read_model_path(config, run_config, artifact_root)
        model = SurfaceReadModel(read_model_path)
        if refresh or not read_model_path.exists():
            model.refresh_from_artifacts(artifact_root)
        run = model.run(run_id)
        if run is None:
            typer.echo(f"Run not found: {run_id}", err=True)
            raise typer.Exit(1)
        if artifact:
            path = _run_artifact_path(artifact_root, run_id, artifact)
            if path is None:
                typer.echo(f"Artifact not found: {artifact}", err=True)
                raise typer.Exit(1)
            typer.echo(path.read_text(encoding="utf-8", errors="replace"), nl=False)
            return
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc

    _print_run_summary(run)


@app.command("inspect-phases")
def inspect_phases(
    run_id: str = typer.Argument(...),
    config: Path = typer.Option(..., "--config", "-c"),
    input_dir: Path | None = typer.Option(None, "--input-dir"),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """Show phase transitions and phase-gate violations for one run."""
    try:
        run_config = load_run_config(config)
        artifact_root = input_dir or _config_relative(config, run_config.artifacts.output_root)
        read_model_path = _read_model_path(config, run_config, artifact_root)
        model = SurfaceReadModel(read_model_path)
        if refresh or not read_model_path.exists():
            model.refresh_from_artifacts(artifact_root)
        if model.run(run_id) is None:
            typer.echo(f"Run not found: {run_id}", err=True)
            raise typer.Exit(1)
        phases = model.phase_history(run_id)
        violations = model.tool_violations(run_id)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Run: {run_id}")
    typer.echo("Phase History:")
    if phases:
        for row in phases:
            sub_phase = f"/{row['sub_phase']}" if row.get("sub_phase") else ""
            typer.echo(
                f"  {row['seq']}. {row['phase']}{sub_phase} "
                f"{row['event_type']} scope={row['scope']} {row['created_at']}"
            )
    else:
        typer.echo("  none")
    typer.echo("Tool Violations:")
    if violations:
        for row in violations:
            sub_phase = f"/{row['sub_phase']}" if row.get("sub_phase") else ""
            typer.echo(
                f"  {row['seq']}. {row['tool']} in {row['phase']}{sub_phase} "
                f"({row['recovery_kind']})"
            )
    else:
        typer.echo("  none")


@app.command()
def judge(
    config: Path = typer.Option(..., "--config", "-c"),
    input_dir: Path | None = typer.Option(None, "--input-dir"),
    run_id: str | None = typer.Option(None, "--run-id"),
    all_unjudged: bool = typer.Option(False, "--all-unjudged"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Score completed runs with the configured judgement panel."""
    try:
        run_config = load_run_config(config)
        result = refresh_judgement(
            config=run_config,
            input_dir=input_dir or _config_relative(config, run_config.artifacts.output_root),
            run_id=run_id,
            all_unjudged=all_unjudged,
            force=force,
        )
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo("Judgement refresh completed:")
    typer.echo(f"  Runs judged: {result.runs_judged}")
    if result.skipped:
        typer.echo(f"  Skipped:     {len(result.skipped)}")


@app.command()
def serve(
    config: Path = typer.Option(..., "--config", "-c"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
    input_dir: Path | None = typer.Option(None, "--input-dir"),
    no_watch: bool = typer.Option(False, "--no-watch"),
) -> None:
    """Start the frontend-facing benchmark read API."""
    try:
        run_config = load_run_config(config)
        artifact_root = input_dir or _config_relative(config, run_config.artifacts.output_root)
        read_model_path = _read_model_path(config, run_config, artifact_root)
        from contribarena.engine.api import create_app

        app_obj = create_app(
            run_config,
            input_dir=artifact_root,
            db_path=read_model_path,
            watch=not no_watch,
        )
        import uvicorn

        uvicorn.run(app_obj, host=host, port=port)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@surface_app.command("index")
def surface_index(
    input_dir: Path = typer.Option(..., "--input-dir", "-i"),
    output_dir: Path = typer.Option(..., "--output-dir", "-o"),
    public_base_url: str = typer.Option("", "--public-base-url"),
) -> None:
    """Index run artifacts into sanitized JSON for the public surface."""
    try:
        result = index_surface_data(
            input_dir=input_dir,
            output_dir=output_dir,
            public_base_url=public_base_url,
        )
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Surface data written: {result.output_dir}")
    typer.echo(f"  Runs indexed: {result.runs_indexed}")
    typer.echo(f"  Files written: {len(result.files_written)}")
    typer.echo(f"  Public artifacts copied: {result.artifacts_copied}")
    if result.skipped:
        typer.echo(f"  Skipped: {len(result.skipped)}")


@app.command()
def version() -> None:
    """Print version and runtime availability."""
    typer.echo(f"contribarena {__version__}")
    typer.echo(f"python {sys.version.split()[0]}")
    typer.echo(f"docker {_docker_status()}")
    typer.echo(f"gh {_command_status('gh', ['gh', '--version'])}")
    typer.echo(f"openai-agents {_package_version('openai-agents')}")
    typer.echo(f"openai {_package_version('openai')}")


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def _gateway_command_panel(message: str, status: str, state: dict[str, Any]) -> Panel:
    text = Text()
    text.append(f"{message}\n", style=_state_style(status))
    if state:
        for key in ("status", "pid", "season_id", "next_tick_at", "last_error"):
            value = state.get(key)
            if value not in {"", None}:
                text.append(f"{key}: {value}\n")
    return Panel(text, title="ContribArena Gateway", border_style=_state_style(status))


def _doctor_panel(result: DoctorResult) -> Panel:
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Check", style="bold")
    table.add_column("State", width=10)
    table.add_column("Detail")
    table.add_column("Hint")
    for check in result.checks:
        label = _label(check.status)
        table.add_row(check.name, label, check.detail or "-", check.hint or "-")
    title = "Doctor"
    border = "red" if result.failed else "green"
    return Panel(table, title=title, border_style=border)


def _print_status_snapshot(config: Path, *, season_id: str | None = None) -> None:
    try:
        snapshot = build_status(config, season_id=season_id)
    except ContribArenaError as exc:
        console.print(Panel(str(exc), title="Status unavailable", border_style="red"))
        return
    console.print(_status_panel(snapshot))


def _status_panel(snapshot: dict[str, Any]) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_row(_header_table(snapshot))
    grid.add_row(_health_table(snapshot))
    grid.add_row(_season_table(snapshot))
    grid.add_row(_participants_table(snapshot))
    grid.add_row(_work_queue_table(snapshot))
    grid.add_row(_runs_table(snapshot))
    grid.add_row(_events_table(snapshot))
    grid.add_row(_next_actions(snapshot))
    gateway = snapshot.get("gateway", {}) if isinstance(snapshot.get("gateway"), dict) else {}
    border = "green" if gateway.get("status") == "running" else "yellow"
    return Panel(grid, title="ContribArena", border_style=border)


def _header_table(snapshot: dict[str, Any]) -> Table:
    paths = snapshot.get("paths", {}) if isinstance(snapshot.get("paths"), dict) else {}
    gateway = snapshot.get("gateway", {}) if isinstance(snapshot.get("gateway"), dict) else {}
    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_row(
        f"config={paths.get('config') or '-'}",
        f"generated={snapshot.get('generated_at') or '-'}",
    )
    table.add_row(
        (
            f"gateway={_label(str(gateway.get('status') or 'unknown'))} "
            f"pid={gateway.get('pid') or '-'} uptime={_duration_text(gateway.get('uptime_seconds'))}"
        ),
        (
            f"api={_label(str(gateway.get('api_status') or 'unknown'))} "
            f"pid={gateway.get('api_pid') or '-'} log={paths.get('gateway_log') or '-'}"
        ),
    )
    return table


def _health_table(snapshot: dict[str, Any]) -> Panel:
    health = snapshot.get("health", {}) if isinstance(snapshot.get("health"), dict) else {}
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Health")
    table.add_column("State")
    for key in ("gateway", "api", "read_model", "providers", "github", "docker", "paths"):
        table.add_row(key, _label(str(health.get(key) or "unknown")))
    return Panel(table, title="Health", border_style="blue")


def _season_table(snapshot: dict[str, Any]) -> Panel:
    gateway = snapshot.get("gateway", {}) if isinstance(snapshot.get("gateway"), dict) else {}
    season = snapshot.get("season", {}) if isinstance(snapshot.get("season"), dict) else {}
    heartbeat = season.get("heartbeat", {}) if isinstance(season.get("heartbeat"), dict) else {}
    runs = snapshot.get("runs", {}) if isinstance(snapshot.get("runs"), dict) else {}
    prs = snapshot.get("prs", {}) if isinstance(snapshot.get("prs"), dict) else {}
    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_row(f"season={season.get('id') or '-'}", f"status={_label(str(season.get('status') or '-'))}")
    table.add_row(
        f"runtime={_label(str(season.get('runtime_status') or 'unknown'))}",
        f"paused={str(bool(season.get('paused'))).lower()}",
    )
    table.add_row(
        f"heartbeat={heartbeat.get('count') or 0} last={heartbeat.get('last_status') or '-'}",
        f"next_tick={gateway.get('next_tick_at') or season.get('next_tick_at') or '-'}",
    )
    table.add_row(
        f"runs counted={runs.get('counted', 0)} excluded={runs.get('excluded', 0)} total={runs.get('total', 0)}",
        f"active_runs={season.get('active_runs', 0)} prs open={prs.get('open', 0)}",
    )
    return Panel(table, title=f"Season {season.get('id') or ''}", border_style="cyan")


def _participants_table(snapshot: dict[str, Any]) -> Panel:
    season = snapshot.get("season", {}) if isinstance(snapshot.get("season"), dict) else {}
    participants = season.get("participants", []) if isinstance(season.get("participants"), list) else []
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Model", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Next", no_wrap=True)
    table.add_column("Last Run", no_wrap=True)
    table.add_column("Retry / Replacement")
    table.add_column("Score State")
    if not participants:
        table.add_row("-", _label("NONE"), "-", "-", "-", "-")
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        replacement = participant.get("replacement", {}) if isinstance(participant.get("replacement"), dict) else {}
        retry = participant.get("judgement_retry", {}) if isinstance(participant.get("judgement_retry"), dict) else {}
        detail = "-"
        if replacement:
            detail = (
                f"replacement={replacement.get('status')} "
                f"{replacement.get('attempts')}/{replacement.get('max_attempts')}"
            )
        if retry:
            detail = (
                f"judge_retry={retry.get('status')} "
                f"{retry.get('attempts')}/{retry.get('max_attempts')}"
            )
        table.add_row(
            str(participant.get("display_name") or participant.get("model") or "-"),
            _label(str(participant.get("state") or "unknown")),
            str(participant.get("next_action") or "-"),
            _clip(str(participant.get("last_run_id") or "-"), 14),
            detail,
            str(participant.get("ranking_state") or "none"),
        )
    return Panel(table, title="Participants", border_style="cyan")


def _work_queue_table(snapshot: dict[str, Any]) -> Panel:
    queue = snapshot.get("work_queue", {}) if isinstance(snapshot.get("work_queue"), dict) else {}
    counts = queue.get("counts", {}) if isinstance(queue.get("counts"), dict) else {}
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Queue", no_wrap=True)
    table.add_column("Count", justify="right")
    table.add_column("Next Items")
    rows = [
        ("due_wakes", queue.get("due_wakes", [])),
        ("running_runs", queue.get("running_runs", [])),
        ("replacement", queue.get("replacement", [])),
        ("judgement_retries", queue.get("judgement_retries", [])),
        ("pr_polls", queue.get("pr_polls", [])),
        ("ranking_excluded", queue.get("ranking_excluded", [])),
        ("blocked", queue.get("blocked", [])),
    ]
    for name, raw_items in rows:
        items = raw_items if isinstance(raw_items, list) else []
        table.add_row(name, str(counts.get(name, len(items))), _queue_items_text(items))
    return Panel(table, title="Work Queue", border_style="yellow")


def _runs_table(snapshot: dict[str, Any]) -> Panel:
    runs = snapshot.get("runs", {}) if isinstance(snapshot.get("runs"), dict) else {}
    recent = runs.get("recent", []) if isinstance(runs.get("recent"), list) else []
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Run", no_wrap=True)
    table.add_column("Agent", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Reason")
    table.add_column("Judge")
    table.add_column("PR")
    table.add_column("Ranking")
    if not recent:
        table.add_row("-", "-", _label("NONE"), "-", "-", "-", "-")
    for run in recent[:6]:
        if not isinstance(run, dict):
            continue
        terminal = run.get("terminal", {}) if isinstance(run.get("terminal"), dict) else {}
        judgement = run.get("judgement", {}) if isinstance(run.get("judgement"), dict) else {}
        pr = run.get("pull_request", {}) if isinstance(run.get("pull_request"), dict) else {}
        reason = (
            run.get("terminal_reason")
            or terminal.get("reason")
            or run.get("reason")
            or "-"
        )
        ranking = (
            f"excluded:{run.get('ranking_exclusion_reason')}"
            if run.get("ranking_excluded")
            else "counted"
        )
        table.add_row(
            _clip(str(run.get("run_id") or "-"), 14),
            _clip(_run_display_name(run), 18),
            _label(str(run.get("run_status") or run.get("status") or "unknown")),
            _clip(str(reason), 24),
            _clip(str(judgement.get("status") or "-"), 14),
            _clip(str(pr.get("state") or pr.get("number") or "-"), 14),
            ranking,
        )
    return Panel(table, title="Recent Runs", border_style="magenta")


def _pr_table(snapshot: dict[str, Any]) -> Panel:
    prs = snapshot.get("prs", {}) if isinstance(snapshot.get("prs"), dict) else {}
    tracked = prs.get("tracked", []) if isinstance(prs.get("tracked"), list) else []
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Repository", no_wrap=True)
    table.add_column("PR", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Lifecycle", no_wrap=True)
    table.add_column("CI", no_wrap=True)
    table.add_column("Review", no_wrap=True)
    table.add_column("Next Poll")
    table.add_column("Run")
    if not tracked:
        table.add_row("-", "-", _label("NONE"), "-", "-", "-", "-", "-")
    for record in tracked:
        if not isinstance(record, dict):
            continue
        table.add_row(
            str(record.get("repository") or "-"),
            str(record.get("number") or "-"),
            _label(str(record.get("state") or "unknown")),
            str(record.get("lifecycle_status") or "-"),
            str(record.get("ci_status") or "-"),
            str(record.get("review_state") or record.get("review_cursor") or "-"),
            str(record.get("next_poll_at") or "-"),
            _clip(str(record.get("originating_run_dir") or "-"), 24),
        )
    return Panel(table, title="PR Lifecycle", border_style="cyan")


def _events_table(snapshot: dict[str, Any]) -> Panel:
    events = snapshot.get("events", []) if isinstance(snapshot.get("events"), list) else []
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Time", no_wrap=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Message")
    if not events:
        table.add_row("-", "-", _label("NONE"), "-")
    for event in events[-6:]:
        if not isinstance(event, dict):
            continue
        table.add_row(
            _clip(str(event.get("ts") or ""), 19),
            str(event.get("source") or event.get("event") or "-"),
            _label(str(event.get("status") or event.get("heartbeat_status") or "-")),
            _clip(str(event.get("message") or event.get("detail") or event.get("error") or "-"), 60),
        )
    return Panel(table, title="Latest Events", border_style="blue")


def _plain_status(snapshot: dict[str, Any]) -> str:
    paths = snapshot.get("paths", {}) if isinstance(snapshot.get("paths"), dict) else {}
    gateway = snapshot.get("gateway", {}) if isinstance(snapshot.get("gateway"), dict) else {}
    health = snapshot.get("health", {}) if isinstance(snapshot.get("health"), dict) else {}
    season = snapshot.get("season", {}) if isinstance(snapshot.get("season"), dict) else {}
    runs = snapshot.get("runs", {}) if isinstance(snapshot.get("runs"), dict) else {}
    queue = snapshot.get("work_queue", {}) if isinstance(snapshot.get("work_queue"), dict) else {}
    counts = queue.get("counts", {}) if isinstance(queue.get("counts"), dict) else {}
    participants = season.get("participants", []) if isinstance(season.get("participants"), list) else []
    recent = runs.get("recent", []) if isinstance(runs.get("recent"), list) else []
    lines = [
        "ContribArena Status",
        f"generated: {snapshot.get('generated_at') or '-'}",
        f"config: {paths.get('config') or '-'}",
        f"gateway: {gateway.get('status') or 'unknown'} pid={gateway.get('pid') or '-'} state={gateway.get('state') or '-'}",
        f"api: {gateway.get('api_status') or 'unknown'} pid={gateway.get('api_pid') or '-'} uptime={_duration_text(gateway.get('uptime_seconds'))}",
        (
            "health: "
            + " ".join(
                f"{key}={health.get(key) or 'unknown'}"
                for key in ("api", "read_model", "providers", "github", "docker", "paths")
            )
        ),
        (
            f"season: {season.get('id') or '-'} status={season.get('status') or '-'} "
            f"runtime={season.get('runtime_status') or '-'} paused={str(bool(season.get('paused'))).lower()} "
            f"active_runs={season.get('active_runs', 0)} next_tick={gateway.get('next_tick_at') or season.get('next_tick_at') or '-'}"
        ),
        (
            f"runs: total={runs.get('total', 0)} counted={runs.get('counted', 0)} "
            f"excluded={runs.get('excluded', 0)} judged={runs.get('judged', 0)}"
        ),
        (
            "queue: "
            + " ".join(
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
            )
        ),
        "",
        "Participants:",
    ]
    if not participants:
        lines.append("  none")
    for participant in participants[:12]:
        if not isinstance(participant, dict):
            continue
        lines.append(
            "  "
            f"{participant.get('display_name') or participant.get('model') or '-'} "
            f"state={participant.get('state') or '-'} "
            f"next={participant.get('next_action') or '-'} "
            f"last_run={participant.get('last_run_id') or '-'} "
            f"ranking={participant.get('ranking_state') or 'none'}"
        )
    lines.extend(["", "Recent Runs:"])
    if not recent:
        lines.append("  none")
    for run in recent[:8]:
        if not isinstance(run, dict):
            continue
        ranking = (
            f"excluded:{run.get('ranking_exclusion_reason')}"
            if run.get("ranking_excluded")
            else "counted"
        )
        lines.append(
            "  "
            f"{run.get('run_id') or '-'} "
            f"{_run_display_name(run)} "
            f"status={run.get('run_status') or run.get('status') or '-'} "
            f"ranking={ranking}"
        )
    return "\n".join(lines) + "\n"


def _next_actions(snapshot: dict[str, Any]) -> Panel:
    paths = snapshot.get("paths", {}) if isinstance(snapshot.get("paths"), dict) else {}
    config = paths.get("config") or "run_config.yaml"
    runs = snapshot.get("runs", {}) if isinstance(snapshot.get("runs"), dict) else {}
    recent = runs.get("recent", []) if isinstance(runs.get("recent"), list) else []
    lines = [
        f"contribarena logs --follow --config {config}",
        f"contribarena doctor --config {config}",
    ]
    if recent and isinstance(recent[0], dict) and recent[0].get("run_id"):
        lines.append(f"contribarena show {recent[0].get('run_id')} --config {config}")
    return Panel("\n".join(lines), title="Next", border_style="green")


def _queue_items_text(items: list[Any]) -> str:
    labels: list[str] = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        label = (
            item.get("display_name")
            or item.get("participant_id")
            or item.get("run_id")
            or item.get("repository")
            or item.get("repo")
            or "-"
        )
        suffix = item.get("next_action") or item.get("reason") or item.get("lifecycle_status") or ""
        labels.append(f"{label}:{suffix}" if suffix else str(label))
    return ", ".join(labels) if labels else "-"


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


def _label(value: str) -> str:
    text = value.upper() if value else "UNKNOWN"
    return f"[{_state_style(text)}]{text}[/{_state_style(text)}]"


def _state_style(value: str) -> str:
    text = value.lower()
    if text in {"ok", "running", "due", "started", "active", "counted"}:
        return "green"
    if text in {"waiting", "sleeping", "stopped", "already_running", "starting"}:
        return "blue"
    if text in {"retry", "deferred", "replacement", "warning", "stopping", "sleeping_after_error"}:
        return "yellow"
    if text in {"blocked"}:
        return "magenta"
    if text in {"failed", "error", "exhausted"}:
        return "red"
    if text in {"skipped", "none", "missing"}:
        return "dim"
    return "white"


def _duration_text(value: object) -> str:
    try:
        seconds = int(value) if value not in {"", None} else None
    except (TypeError, ValueError):
        seconds = None
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _docker_status() -> str:
    return _command_status("docker", ["docker", "--version"])


def _command_status(name: str, command: list[str]) -> str:
    if shutil.which(name) is None:
        return "missing"
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return result.stdout.strip().splitlines()[0]
    lines = (result.stderr or result.stdout).strip().splitlines()
    detail = lines[0] if lines else f"{name} returned exit code {result.returncode}"
    return f"unusable: {detail}"


def _config_relative(config_path: Path, path: Path) -> Path:
    return path if path.is_absolute() else config_path.resolve().parent / path


def _read_model_path(config_path: Path, run_config: Any, artifact_root: Path) -> Path:
    path = run_config.backend.read_model_path
    if path.is_absolute():
        return path
    if path == DEFAULT_READ_MODEL_RELATIVE:
        return artifact_root.parent / "read_model.sqlite"
    return _config_relative(config_path, path)


def _with_model_override(run_config: Any, model: str) -> Any:
    return run_config.model_copy(
        update={"run": run_config.run.model_copy(update={"model": model})},
        deep=True,
    )


def _with_budget_overrides(
    run_config: Any,
    *,
    max_candidate_repos: int | None = None,
    max_opportunities: int | None = None,
    max_duplicate_checks: int | None = None,
    max_repo_switches: int | None = None,
    max_opportunity_switches: int | None = None,
    max_review_rounds: int | None = None,
) -> Any:
    budget = run_config.run.budget
    scout_updates = {
        key: value
        for key, value in {
            "max_candidate_repos_considered": max_candidate_repos,
            "max_opportunities_considered": max_opportunities,
            "max_duplicate_checks": max_duplicate_checks,
        }.items()
        if value is not None
    }
    work_updates = {
        key: value
        for key, value in {
            "max_repo_switches": max_repo_switches,
            "max_opportunity_switches": max_opportunity_switches,
        }.items()
        if value is not None
    }
    review_updates = {
        key: value
        for key, value in {"max_review_rounds": max_review_rounds}.items()
        if value is not None
    }
    if not scout_updates and not work_updates and not review_updates:
        return run_config
    updated_budget = budget.model_copy(
        update={
            "scout": budget.scout.model_copy(update=scout_updates),
            "work": budget.work.model_copy(update=work_updates),
            "review": budget.review.model_copy(update=review_updates),
        },
        deep=True,
    )
    return run_config.model_copy(
        update={"run": run_config.run.model_copy(update={"budget": updated_budget})},
        deep=True,
    )


def _with_season_run_override(
    run_config: Any,
    *,
    season_id: str | None,
    participant_id: str | None,
    wake_source: str,
) -> Any:
    if wake_source not in {"manual", "auto", "unranked"}:
        raise ContribArenaError("wake_source must be manual, auto, or unranked")
    resolved_season_id = season_id or run_config.run.season_id
    if not resolved_season_id and participant_id and ":" in participant_id:
        resolved_season_id = participant_id.split(":", 1)[0]
    updates = {
        "season_id": resolved_season_id,
        "participant_id": participant_id or run_config.run.participant_id,
        "wake_source": wake_source,
    }
    return run_config.model_copy(update={"run": run_config.run.model_copy(update=updates)})


def _season_transition(
    config: Path,
    season_id: str | None,
    status: str,
    *,
    force_with_open_prs: bool = False,
) -> None:
    try:
        run_config = load_run_config(config)
        target = season_id or (run_config.season.id if run_config.season else "season_0")
        store = SeasonStore.from_config(run_config)
        if status == "completed":
            artifact_root = _config_relative(config, run_config.artifacts.output_root)
            snapshot = build_leaderboard_snapshot(input_dir=artifact_root, season_id=target)
            write_leaderboard_snapshot(store, target, snapshot)
        state = store.transition(
            target,
            status,  # type: ignore[arg-type]
            run_config.season,
            force_with_open_prs=force_with_open_prs,
        )
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Season {target}: {state.get('status')}")


def _season_pause(config: Path, season_id: str | None, *, paused: bool) -> None:
    try:
        run_config = load_run_config(config)
        target = season_id or (run_config.season.id if run_config.season else "season_0")
        state = SeasonStore.from_config(run_config).set_paused(
            target,
            paused,
            run_config.season,
        )
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Season {target}: {'paused' if state.get('paused') else 'resumed'}")


def _season_workspace_clean(config: Path, season_id: str | None, *, quiet: bool = False) -> int:
    try:
        run_config = load_run_config(config)
        target = season_id or (run_config.season.id if run_config.season else "season_0")
        results = cleanup_season_workspaces(
            SeasonStore.from_config(run_config),
            target,
            run_config.season,
        )
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    if not quiet:
        for result in results:
            typer.echo(
                f"{result['participant_id']} {result['container']} exit={result['exit_code']}"
            )
    return len(results)


def _configured_models(run_config: Any) -> list[str]:
    providers = run_config.models.providers
    models = [
        *(f"compatible/{name}" for name in providers.compatible),
        *(f"responses/{name}" for name in providers.responses),
        *(f"anthropic/{name}" for name in providers.anthropic),
        *(f"gemini/{name}" for name in providers.gemini),
    ]
    return models or [run_config.run.model]


def _print_run_summary(run: dict[str, Any]) -> None:
    agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
    repo = run.get("repository", {}) if isinstance(run.get("repository"), dict) else {}
    season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
    judgement = run.get("judgement", {}) if isinstance(run.get("judgement"), dict) else {}
    pr = run.get("pull_request", {}) if isinstance(run.get("pull_request"), dict) else {}
    terminal = run.get("terminal", {}) if isinstance(run.get("terminal"), dict) else {}
    replacement = run.get("replacement", {}) if isinstance(run.get("replacement"), dict) else {}
    retry = run.get("judgement_retry", {}) if isinstance(run.get("judgement_retry"), dict) else {}
    ranking = (
        f"excluded:{run.get('ranking_exclusion_reason')}"
        if run.get("ranking_excluded")
        else "counted"
    )
    typer.echo(f"Run:         {run.get('run_id') or ''}")
    typer.echo(f"Status:      {run.get('run_status') or 'unknown'}")
    typer.echo(f"Terminal:    {run.get('terminal_reason') or terminal.get('reason') or '-'}")
    typer.echo(f"Layer:       {run.get('terminal_layer') or terminal.get('layer') or '-'}")
    typer.echo(f"Agent:       {_run_display_name(run) or agent.get('handle') or agent.get('name') or ''}")
    typer.echo(f"Repository:  {repo.get('full_name') or ''}")
    typer.echo(f"Season:      {season.get('id') or ''}")
    typer.echo(f"Started:     {run.get('started_at') or ''}")
    typer.echo(f"Completed:   {run.get('completed_at') or ''}")
    typer.echo(f"Judgement:   {judgement.get('status') or '-'}")
    typer.echo(f"Arena score: {_score_text(judgement.get('arena_score'))}")
    typer.echo(f"Ranking:     {ranking}")
    if replacement:
        typer.echo(
            "Replacement: "
            f"{replacement.get('status') or '-'} "
            f"attempt={replacement.get('attempts') or '-'}/{replacement.get('max_attempts') or '-'}"
        )
    if retry:
        typer.echo(
            "Judge retry: "
            f"{retry.get('status') or '-'} "
            f"attempt={retry.get('attempts') or '-'}/{retry.get('max_attempts') or '-'}"
        )
    if pr.get("url"):
        typer.echo(f"PR:          {pr.get('url')}")
    artifacts = [item for item in run.get("artifacts", []) if isinstance(item, dict)]
    if artifacts:
        typer.echo("Artifacts:")
        for item in artifacts:
            visibility = str(item.get("visibility") or "internal")
            typer.echo(f"  - {item.get('name') or ''} ({visibility})")


def _run_artifact_path(input_dir: Path, run_id: str, artifact_name: str) -> Path | None:
    if "/" in artifact_name or "\\" in artifact_name:
        return None
    for summary_path in sorted(input_dir.rglob("run_summary.json")):
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or str(payload.get("run_id") or "") != run_id:
            continue
        path = summary_path.parent / artifact_name
        return path if path.is_file() else None
    return None


def _clip(value: str, width: int) -> str:
    return value if len(value) <= width else value[: max(0, width - 3)] + "..."


def _score_text(value: Any) -> str:
    return "-" if value is None or value == "" else str(value)


app.add_typer(surface_app, name="surface")
app.add_typer(runs_app, name="runs")
season_app.add_typer(season_workspace_app, name="workspace")
app.add_typer(season_app, name="season")
app.add_typer(pr_app, name="pr")
