from __future__ import annotations

from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.models import CommandResult


def workspace_run(
    workspace: DockerWorkspaceManager,
    cmd: str,
    timeout_seconds: int | None = None,
) -> CommandResult:
    return workspace.run(cmd, timeout_seconds=timeout_seconds)
