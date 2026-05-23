from __future__ import annotations

from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.models import PatchResult


def workspace_apply_patch(workspace: DockerWorkspaceManager, diff: str) -> PatchResult:
    return workspace.apply_patch(diff)
