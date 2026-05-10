from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar, cast

from contribarena.config.schema import RunConfig
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.models import CommandResult, PatchResult, RunState
from contribarena.trace import TraceWriter
from contribarena.tools.repo_eligibility import repo_check_eligibility
from contribarena.tools.repo_issues import repo_get_issues
from contribarena.tools.repo_metadata import repo_get_metadata
from contribarena.tools.repo_search import repo_search
from contribarena.tools.workspace_patch import workspace_apply_patch
from contribarena.tools.workspace_run import workspace_run

T = TypeVar("T")


@dataclass
class ToolRegistry:
    config: RunConfig
    workspace: DockerWorkspaceManager
    trace: TraceWriter
    budget: BudgetTracker
    capture: ArtifactCapture

    def repo_search(self, query: str = "", filters: object | None = None) -> object:
        return self._record(
            state=RunState.REPO_DISCOVERED,
            event="repo.search",
            fn=lambda: repo_search(self.config, query=query, filters=filters),
            payload={"query": query, "filters": str(filters)},
        )

    def repo_check_eligibility(self, candidate: object) -> object:
        return self._record(
            state=RunState.REPO_ELIGIBLE,
            event="repo.eligibility",
            fn=lambda: repo_check_eligibility(candidate),  # type: ignore[arg-type]
            payload={"candidate": str(candidate)},
        )

    def repo_get_metadata(self, candidate: object) -> object:
        return self._record(
            state=RunState.REPO_PROFILED,
            event="repo.metadata",
            fn=lambda: repo_get_metadata(candidate),  # type: ignore[arg-type]
            payload={"candidate": str(candidate)},
        )

    def repo_get_issues(self, candidate: object, filters: object | None = None) -> object:
        return self._record(
            state=RunState.OPPORTUNITIES_RANKED,
            event="repo.issues",
            fn=lambda: repo_get_issues(candidate, filters=filters),  # type: ignore[arg-type]
            payload={"candidate": str(candidate), "filters": str(filters)},
        )

    def workspace_run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
        result = cast(
            CommandResult,
            self._record(
                state=RunState.WORKSPACE_CHECKED,
                event="workspace.run",
                fn=lambda: workspace_run(self.workspace, cmd, timeout_seconds),
                payload={"cmd": cmd, "timeout_seconds": timeout_seconds},
            ),
        )
        self.capture.record_command(result)
        return result

    def workspace_apply_patch(self, diff: str) -> PatchResult:
        result = cast(
            PatchResult,
            self._record(
                state=RunState.WORKSPACE_CHECKED,
                event="workspace.apply_patch",
                fn=lambda: workspace_apply_patch(self.workspace, diff),
                payload={"diff_bytes": len(diff.encode("utf-8"))},
            ),
        )
        self.capture.record_patch(result)
        return result

    def _record(
        self,
        state: str,
        event: str,
        fn: Callable[[], T],
        payload: dict[str, Any],
    ) -> T:
        self.budget.record_step()
        self.trace.write(state, f"{event}.started", payload)
        try:
            result = fn()
        except Exception as exc:
            self.trace.write(state, f"{event}.failed", {"error": str(exc)})
            raise
        self.trace.write(state, f"{event}.finished", {"result": _safe_result(result)})
        return result


def _safe_result(result: object) -> object:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")  # type: ignore[no-any-return]
    if isinstance(result, list):
        return [_safe_result(item) for item in result]
    if isinstance(result, dict):
        return result
    return str(result)
