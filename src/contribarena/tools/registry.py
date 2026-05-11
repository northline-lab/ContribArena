from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, TypeVar, cast

from contribarena.config.schema import RunConfig
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.models import AciResult, AgentStep, CommandResult, PatchResult, RunState
from contribarena.trace import TraceWriter
from contribarena.tools.aci import (
    AciExecution,
    aci_create,
    aci_find_files,
    aci_insert,
    aci_replace,
    aci_search,
    aci_suggest_verification,
    aci_submit_patch,
    aci_undo,
    aci_verify,
    aci_view,
)
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

    def aci_view(
        self,
        path: str,
        start_line: int = 1,
        max_lines: int = 200,
    ) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.view",
            phase="exploration",
            tool="aci_view",
            fn=lambda: aci_view(self.workspace, path, start_line, max_lines),
            payload={"path": path, "start_line": start_line, "max_lines": max_lines},
        )

    def aci_search(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 80,
    ) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.search",
            phase="exploration",
            tool="aci_search",
            fn=lambda: aci_search(self.workspace, pattern, path, max_results),
            payload={"pattern": pattern, "path": path, "max_results": max_results},
        )

    def aci_find_files(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 80,
    ) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.find_files",
            phase="exploration",
            tool="aci_find_files",
            fn=lambda: aci_find_files(self.workspace, pattern, path, max_results),
            payload={"pattern": pattern, "path": path, "max_results": max_results},
        )

    def aci_replace(self, path: str, old_str: str, new_str: str) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.replace",
            phase="implementation",
            tool="aci_replace",
            fn=lambda: aci_replace(self.workspace, path, old_str, new_str),
            payload={
                "path": path,
                "old_bytes": len(old_str.encode("utf-8")),
                "new_bytes": len(new_str.encode("utf-8")),
            },
        )

    def aci_insert(self, path: str, insert_after_line: int, text: str) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.insert",
            phase="implementation",
            tool="aci_insert",
            fn=lambda: aci_insert(self.workspace, path, insert_after_line, text),
            payload={
                "path": path,
                "insert_after_line": insert_after_line,
                "text_bytes": len(text.encode("utf-8")),
            },
        )

    def aci_create(self, path: str, content: str) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.create",
            phase="implementation",
            tool="aci_create",
            fn=lambda: aci_create(self.workspace, path, content),
            payload={"path": path, "content_bytes": len(content.encode("utf-8"))},
        )

    def aci_undo(self) -> AciResult:
        result = self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.undo",
            phase="implementation",
            tool="aci_undo",
            fn=self._undo_execution,
            payload={"available_undos": len(self.capture.undo_stack)},
        )
        if result.success and self.capture.undo_stack:
            self.capture.undo_stack.pop()
        return result

    def aci_verify(
        self,
        command: str,
        path: str = "repo",
        timeout_seconds: int | None = None,
    ) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.verify",
            phase="verification",
            tool="aci_verify",
            fn=lambda: aci_verify(self.workspace, command, path, timeout_seconds),
            payload={"command": command, "path": path, "timeout_seconds": timeout_seconds},
        )

    def aci_suggest_verification(self, path: str = "repo") -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.suggest_verification",
            phase="verification",
            tool="aci_suggest_verification",
            fn=lambda: aci_suggest_verification(self.workspace, path),
            payload={"path": path},
        )

    def aci_submit_patch(self, path: str = "repo") -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.submit_patch",
            phase="submission",
            tool="aci_submit_patch",
            fn=lambda: aci_submit_patch(self.workspace, path),
            payload={"path": path},
        )

    def _undo_execution(self) -> AciExecution:
        if not self.capture.undo_stack:
            return AciExecution(
                result=AciResult(
                    tool="aci_undo",
                    success=False,
                    output="No ACI edit is available to undo.",
                    error="No ACI edit is available to undo.",
                )
            )
        return aci_undo(self.workspace, self.capture.undo_stack[-1])

    def _record(
        self,
        state: str,
        event: str,
        fn: Callable[[], T],
        payload: dict[str, Any],
    ) -> T:
        self.budget.record_step()
        start = time.monotonic()
        self.trace.write(state, f"{event}.started", payload)
        try:
            result = fn()
        except Exception as exc:
            duration = time.monotonic() - start
            self.trace.write(state, f"{event}.failed", {"error": str(exc)})
            self.capture.record_step(
                AgentStep(
                    step=len(self.capture.steps) + 1,
                    phase=_phase_for_event(event),
                    tool=event,
                    input_summary=_summary(payload),
                    result_summary="failed",
                    state=str(state),
                    duration_seconds=duration,
                    error=str(exc),
                )
            )
            raise
        duration = time.monotonic() - start
        self.trace.write(state, f"{event}.finished", {"result": _safe_result(result)})
        self.capture.record_step(
            AgentStep(
                step=len(self.capture.steps) + 1,
                phase=_phase_for_event(event),
                tool=event,
                input_summary=_summary(payload),
                result_summary=_result_summary(result),
                state=str(state),
                duration_seconds=duration,
            )
        )
        return result

    def _record_aci(
        self,
        state: str,
        event: str,
        phase: str,
        tool: str,
        fn: Callable[[], AciExecution],
        payload: dict[str, Any],
    ) -> AciResult:
        self.budget.record_step()
        start = time.monotonic()
        self.trace.write(state, f"{event}.started", payload)
        try:
            execution = fn()
        except Exception as exc:
            duration = time.monotonic() - start
            self.trace.write(state, f"{event}.failed", {"error": str(exc)})
            self.capture.record_step(
                AgentStep(
                    step=len(self.capture.steps) + 1,
                    phase=phase,
                    tool=tool,
                    input_summary=_summary(payload),
                    result_summary="failed",
                    state=str(state),
                    duration_seconds=duration,
                    error=str(exc),
                )
            )
            raise
        duration = time.monotonic() - start
        for command in execution.commands:
            self.capture.record_command(command)
        for patch in execution.patches:
            self.capture.record_patch(patch)
        self.capture.record_aci_result(execution.result)
        if execution.undo_diff:
            self.capture.record_undo_diff(execution.undo_diff)
        self.trace.write(state, f"{event}.finished", {"result": _safe_result(execution.result)})
        self.capture.record_step(
            AgentStep(
                step=len(self.capture.steps) + 1,
                phase=phase,
                tool=tool,
                input_summary=_summary(payload),
                result_summary=_result_summary(execution.result),
                state=str(state),
                duration_seconds=duration,
                error=execution.result.error,
            )
        )
        return execution.result


def _safe_result(result: object) -> object:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")  # type: ignore[no-any-return]
    if isinstance(result, list):
        return [_safe_result(item) for item in result]
    if isinstance(result, dict):
        return result
    return str(result)


def _phase_for_event(event: str) -> str:
    if event.startswith("repo."):
        return "discovery"
    if event.startswith("workspace."):
        return "workspace"
    return "agent"


def _summary(payload: dict[str, Any], max_chars: int = 300) -> str:
    text = str(payload)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def _result_summary(result: object, max_chars: int = 300) -> str:
    if isinstance(result, AciResult):
        text = result.output or result.error or ("ok" if result.success else "failed")
    elif isinstance(result, CommandResult):
        text = f"exit_code={result.exit_code}"
    elif isinstance(result, PatchResult):
        text = "patch applied" if result.success else result.error or "patch failed"
    elif isinstance(result, list):
        text = f"{len(result)} result(s)"
    else:
        text = str(result)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"
