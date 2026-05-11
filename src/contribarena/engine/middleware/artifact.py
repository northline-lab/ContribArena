from __future__ import annotations

from contribarena.models.tool_results import AciResult, AgentStep, CommandResult, PatchResult


class ArtifactCapture:
    def __init__(self) -> None:
        self.commands: list[CommandResult] = []
        self.patches: list[PatchResult] = []
        self.aci_results: list[AciResult] = []
        self.steps: list[AgentStep] = []

    def record_command(self, result: CommandResult) -> None:
        self.commands.append(result)

    def record_patch(self, result: PatchResult) -> None:
        self.patches.append(result)

    def record_aci_result(self, result: AciResult) -> None:
        self.aci_results.append(result)

    def record_step(self, step: AgentStep) -> None:
        self.steps.append(step)
