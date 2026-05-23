from __future__ import annotations

from contribarena.models.assistant_updates import AssistantUpdate
from contribarena.models.tool_results import AciResult, AgentStep, CommandResult, PatchResult


class ArtifactCapture:
    def __init__(self) -> None:
        self.commands: list[CommandResult] = []
        self.patches: list[PatchResult] = []
        self.aci_results: list[AciResult] = []
        self.steps: list[AgentStep] = []
        self.undo_stack: list[str] = []
        self.tool_violations: list[dict[str, object]] = []
        self.phase_scout_project_rows: list[dict[str, object]] = []
        self.phase_scout_opportunity_rows: list[dict[str, object]] = []
        self.phase_scout_duplicate_rows: list[dict[str, object]] = []
        self.phase_review_maintainer_rows: list[dict[str, object]] = []
        self.phase_review_response_rows: list[dict[str, object]] = []
        self.discovery_rows: list[dict[str, object]] = []
        self.assistant_updates: list[AssistantUpdate] = []
        self.live_action_rows: list[dict[str, object]] = []

    def record_command(self, result: CommandResult) -> None:
        self.commands.append(result)

    def record_patch(self, result: PatchResult) -> None:
        self.patches.append(result)

    def record_aci_result(self, result: AciResult) -> None:
        self.aci_results.append(result)

    def record_step(self, step: AgentStep) -> None:
        self.steps.append(step)

    def record_undo_diff(self, diff: str) -> None:
        self.undo_stack.append(diff)

    def record_tool_violation(self, payload: dict[str, object]) -> None:
        self.tool_violations.append(payload)

    def record_phase_artifact(self, name: str, payload: dict[str, object]) -> None:
        if name == "phase_scout_project_comparison":
            self.phase_scout_project_rows.append(payload)
        elif name == "phase_scout_opportunity_comparison":
            self.phase_scout_opportunity_rows.append(payload)
        elif name == "phase_scout_duplicate_check":
            self.phase_scout_duplicate_rows.append(payload)
        elif name == "phase_review_maintainer_review":
            self.phase_review_maintainer_rows.append(payload)
        elif name == "phase_review_response":
            self.phase_review_response_rows.append(payload)

    def record_discovery(self, payload: dict[str, object]) -> None:
        self.discovery_rows.append(payload)

    def record_assistant_update(self, update: AssistantUpdate) -> None:
        self.assistant_updates.append(update)

    def record_live_action(self, payload: dict[str, object]) -> None:
        self.live_action_rows.append(payload)
