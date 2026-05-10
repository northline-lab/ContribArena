from __future__ import annotations

from contribarena.models.tool_results import CommandResult, PatchResult


class ArtifactCapture:
    def __init__(self) -> None:
        self.commands: list[CommandResult] = []
        self.patches: list[PatchResult] = []

    def record_command(self, result: CommandResult) -> None:
        self.commands.append(result)

    def record_patch(self, result: PatchResult) -> None:
        self.patches.append(result)
