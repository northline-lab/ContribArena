from __future__ import annotations

import json
import shlex

from contribarena.config.schema import RepoCandidate
from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.models import CommandResult, RepoSetupProbeResult


def repo_setup_probe(
    workspace: DockerWorkspaceManager,
    candidate: RepoCandidate,
    *,
    max_probe_seconds: int = 60,
    install_dependencies: bool = False,
) -> tuple[RepoSetupProbeResult, CommandResult]:
    timeout = max(1, max_probe_seconds)
    branch = candidate.branch or "main"
    target = f"probe-{candidate.owner}-{candidate.repo}".replace("/", "-")
    clone_url = str(candidate.url)
    if clone_url.startswith("https://github.com/") and not clone_url.endswith(".git"):
        clone_url += ".git"
    install_hint = "true" if install_dependencies else "false"
    command = (
        f"rm -rf {shlex.quote(target)} && "
        f"git -c http.version=HTTP/1.1 clone --depth 1 --branch {shlex.quote(branch)} "
        f"{shlex.quote(clone_url)} {shlex.quote(target)} >/tmp/contribarena_probe_clone.log 2>&1 && "
        f"cd {shlex.quote(target)} && "
        "python - <<'PY'\n"
        "import json\n"
        "from pathlib import Path\n"
        "files = {p.as_posix() for p in Path('.').glob('**/*') if p.is_file()}\n"
        "managers = []\n"
        "tests = []\n"
        "ci = sorted([p for p in files if p.startswith('.github/workflows/') or p in {'.gitlab-ci.yml', 'tox.ini'}])\n"
        "if 'pyproject.toml' in files or 'setup.py' in files:\n"
        "    managers.append('python')\n"
        "    tests.append('python -m pytest')\n"
        "if 'package.json' in files:\n"
        "    managers.append('node')\n"
        "    tests.append('npm test')\n"
        "if 'Cargo.toml' in files:\n"
        "    managers.append('rust')\n"
        "    tests.append('cargo test')\n"
        "if 'go.mod' in files:\n"
        "    managers.append('go')\n"
        "    tests.append('go test ./...')\n"
        "difficulty = 'low' if managers or ci else 'unknown'\n"
        "print(json.dumps({'package_managers': managers, 'test_commands': tests, 'ci_files': ci[:20], 'setup_difficulty': difficulty}))\n"
        "PY\n"
        f"if {install_hint}; then echo 'dependency install intentionally skipped by lightweight probe contract' >&2; fi"
    )
    result = workspace.run(command, timeout_seconds=timeout)
    probe = _parse_probe_result(candidate, branch, result)
    return probe, result


def _parse_probe_result(
    candidate: RepoCandidate,
    branch: str,
    result: CommandResult,
) -> RepoSetupProbeResult:
    if result.exit_code != 0:
        return RepoSetupProbeResult(
            full_name=candidate.full_name,
            success=False,
            probe_failed=True,
            default_branch=branch,
            duration_seconds=result.duration_seconds,
            error=result.stderr or result.stdout,
        )
    try:
        payload = json.loads((result.stdout or "{}").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return RepoSetupProbeResult(
            full_name=candidate.full_name,
            success=False,
            probe_failed=True,
            default_branch=branch,
            duration_seconds=result.duration_seconds,
            error=f"probe output parse failed: {exc}",
        )
    return RepoSetupProbeResult(
        full_name=candidate.full_name,
        success=True,
        default_branch=branch,
        package_managers=[str(item) for item in payload.get("package_managers", [])],
        test_commands=[str(item) for item in payload.get("test_commands", [])],
        ci_files=[str(item) for item in payload.get("ci_files", [])],
        setup_difficulty=str(payload.get("setup_difficulty") or "unknown"),
        duration_seconds=result.duration_seconds,
    )
