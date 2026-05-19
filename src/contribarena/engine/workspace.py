from __future__ import annotations

import subprocess
import shlex
import time
from datetime import UTC, datetime

from contribarena.config.schema import WorkspaceConfig
from contribarena.engine.artifacts import slugify
from contribarena.errors import InfrastructureError
from contribarena.models.tool_results import CommandResult, PatchResult


class DockerWorkspaceManager:
    def __init__(self, run_id: str, repo_slug: str, config: WorkspaceConfig) -> None:
        self.run_id = run_id
        self.repo_slug = repo_slug
        self.config = config
        key = config.persistent_key or f"{slugify(run_id)[:12]}-{slugify(repo_slug)}"
        self.container_name = f"contribarena-{slugify(key)}"

    def start(self) -> None:
        if self.config.persistent_key and self._container_exists():
            self._write_metadata()
            return
        command = [
            "docker",
            "run",
            "-d",
            "--name",
            self.container_name,
            "--cpus",
            self.config.resources.cpus,
            "--memory",
            self.config.resources.memory,
            "--storage-opt",
            f"size={self.config.resources.disk}",
            "--workdir",
            self.config.workdir,
            self.config.image,
            "sleep",
            "infinity",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise InfrastructureError("docker CLI not found in PATH") from exc
        if result.returncode != 0:
            raise InfrastructureError(
                f"docker run failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        self._write_metadata()

    def stop(self) -> CommandResult:
        if self.config.persistent_key:
            self._write_metadata()
            return CommandResult(
                command=f"retain persistent workspace {self.container_name}",
                stdout=self.container_name,
                stderr="",
                exit_code=0,
                duration_seconds=0.0,
            )
        timeout = min(max(self.config.command_timeout_seconds, 1), 30)
        try:
            completed = subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=f"docker rm -f {self.container_name}",
                stdout=exc.stdout or "",
                stderr=exc.stderr or f"docker cleanup timed out after {timeout} seconds",
                exit_code=124,
                duration_seconds=float(timeout),
                timed_out=True,
            )
        except FileNotFoundError:
            return CommandResult(
                command=f"docker rm -f {self.container_name}",
                stdout="",
                stderr="docker CLI not found in PATH",
                exit_code=127,
                duration_seconds=0.0,
            )
        return CommandResult(
            command=f"docker rm -f {self.container_name}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            duration_seconds=0.0,
        )

    def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
        return self._run(cmd, timeout_seconds=timeout_seconds)

    def run_with_env(
        self,
        cmd: str,
        env: dict[str, str],
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        return self._run(cmd, timeout_seconds=timeout_seconds, env=env)

    def _run(
        self,
        cmd: str,
        timeout_seconds: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        timeout = timeout_seconds or self.config.command_timeout_seconds
        env_args: list[str] = []
        for key, value in (env or {}).items():
            env_args.extend(["--env", f"{key}={value}"])
        start = time.monotonic()
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "exec",
                    *env_args,
                    self.container_name,
                    "bash",
                    "-c",
                    _logged_shell_command(self.config.workdir, cmd),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            duration = time.monotonic() - start
            return CommandResult(
                command=cmd,
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
                duration_seconds=duration,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start
            return CommandResult(
                command=cmd,
                stdout=exc.stdout or "",
                stderr=exc.stderr or f"command timed out after {timeout} seconds",
                exit_code=124,
                duration_seconds=duration,
                timed_out=True,
            )

        except FileNotFoundError as exc:
            raise InfrastructureError("docker CLI not found in PATH") from exc

    def apply_patch(self, diff: str) -> PatchResult:
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    self.container_name,
                    "bash",
                    "-c",
                    _logged_shell_command(self.config.workdir, "git apply -"),
                ],
                input=diff,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return PatchResult(
                success=False,
                files_modified=[],
                error=exc.stderr
                or f"patch timed out after {self.config.command_timeout_seconds} seconds",
            )
        except FileNotFoundError as exc:
            raise InfrastructureError("docker CLI not found in PATH") from exc

        return PatchResult(
            success=completed.returncode == 0,
            files_modified=_files_from_patch(diff),
            error=None if completed.returncode == 0 else completed.stderr or completed.stdout,
        )

    def _container_exists(self) -> bool:
        try:
            completed = subprocess.run(
                ["docker", "inspect", self.container_name],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def _write_metadata(self) -> None:
        path = self.config.persistent_metadata_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.container_name + "\n", encoding="utf-8")
        (path.parent / "last_used_at").write_text(
            datetime.now(UTC).isoformat() + "\n",
            encoding="utf-8",
        )


def _files_from_patch(diff: str) -> list[str]:
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                files.append(parts[3].removeprefix("b/"))
        elif line.startswith("+++ b/"):
            files.append(line.removeprefix("+++ b/"))
    return sorted(set(files))


def _logged_shell_command(workdir: str, cmd: str) -> str:
    command_label = shlex.quote(cmd[:300])
    return (
        f"printf '[contribarena] workspace command: %s\\n' {command_label} > /proc/1/fd/1; "
        "set -o pipefail; "
        f"cd {shlex.quote(workdir)} && "
        f"{{ {cmd}; }} > >(tee /proc/1/fd/1) 2> >(tee /proc/1/fd/2 >&2)"
    )
