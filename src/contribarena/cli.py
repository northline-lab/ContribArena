from __future__ import annotations

import sys
import shutil
import importlib.metadata
import subprocess
from pathlib import Path

import typer

from contribarena import __version__
from contribarena.config import load_run_config, write_starter_config
from contribarena.engine import Runner
from contribarena.errors import ContribArenaError

app = typer.Typer(help="ContribArena control plane commands.")


@app.command()
def init(output: Path = typer.Option(Path("run_config.yaml"), "--output", "-o")) -> None:
    """Generate a starter M0.0 run config."""
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Execute an M0.0 agent run."""
    try:
        run_config = load_run_config(config)
        result = Runner().run(run_config, output_dir=output_dir, verbose=verbose)
    except ContribArenaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(f"Run completed: {result.run_dir}")
    typer.echo(f"  Status:      {result.status}")
    typer.echo(f"  Tool calls:  {result.tool_calls}")


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
