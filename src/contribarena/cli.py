from __future__ import annotations

import json
import sys
import shutil
import importlib.metadata
import subprocess
from pathlib import Path
from typing import Any

import typer

from contribarena import __version__
from contribarena.config import load_run_config, write_starter_config
from contribarena.config.schema import DEFAULT_READ_MODEL_RELATIVE
from contribarena.engine import LocalController, Runner
from contribarena.engine.api import create_app
from contribarena.engine.judge_refresh import refresh_judgement
from contribarena.engine.read_model import SurfaceReadModel
from contribarena.engine.surface_indexer import index_surface_data
from contribarena.errors import ContribArenaError

app = typer.Typer(help="ContribArena control plane commands.")
surface_app = typer.Typer(help="Build public read-only surface data.")


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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Execute a ContribArena agent run."""
    try:
        run_config = load_run_config(config)
        if model:
            run_config = _with_model_override(run_config, model)
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


@app.command()
def status(
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


@app.command("runs")
def runs_list(
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
    """List indexed benchmark runs."""
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
    typer.echo(f"{'Run ID':<28} {'Status':<11} {'Agent':<16} {'Repo':<26} {'Score':>5}")
    for row in rows:
        agent_info = row.get("agent", {}) if isinstance(row.get("agent"), dict) else {}
        repo = row.get("repository", {}) if isinstance(row.get("repository"), dict) else {}
        judgement = row.get("judgement", {}) if isinstance(row.get("judgement"), dict) else {}
        score = judgement.get("arena_score")
        typer.echo(
            f"{_clip(str(row.get('run_id') or ''), 28):<28} "
            f"{_clip(str(row.get('run_status') or 'unknown'), 11):<11} "
            f"{_clip(str(agent_info.get('handle') or agent_info.get('name') or ''), 16):<16} "
            f"{_clip(str(repo.get('full_name') or ''), 26):<26} "
            f"{_score_text(score):>5}"
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
    typer.echo(f"Run:         {run.get('run_id') or ''}")
    typer.echo(f"Status:      {run.get('run_status') or 'unknown'}")
    typer.echo(f"Agent:       {agent.get('handle') or agent.get('name') or ''}")
    typer.echo(f"Repository:  {repo.get('full_name') or ''}")
    typer.echo(f"Season:      {season.get('id') or ''}")
    typer.echo(f"Started:     {run.get('started_at') or ''}")
    typer.echo(f"Completed:   {run.get('completed_at') or ''}")
    typer.echo(f"Arena score: {_score_text(judgement.get('arena_score'))}")
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
