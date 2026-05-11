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

    def aci_recover_invalid_action(
        self,
        recovery_kind: str,
        message: str,
        attempted_tool: str = "",
    ) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.recover_invalid_action",
            phase="recovery",
            tool="aci_recover_invalid_action",
            fn=lambda: AciExecution(
                result=AciResult(
                    tool="aci_recover_invalid_action",
                    success=False,
                    output=message,
                    error=message,
                    recovery_kind=recovery_kind,
                    review_notes=(
                        f"attempted_tool={attempted_tool}" if attempted_tool else ""
                    ),
                )
            ),
            payload={
                "recovery_kind": recovery_kind,
                "message": message,
                "attempted_tool": attempted_tool,
            },
        )

    def aci_submit_patch(
        self,
        path: str = "repo",
        no_command_verification_rationale: str = "",
    ) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.submit_patch",
            phase="submission",
            tool="aci_submit_patch",
            fn=lambda: self._submit_patch_execution(
                path, no_command_verification_rationale
            ),
            payload={
                "path": path,
                "no_command_verification_rationale": _summary(
                    {"rationale": no_command_verification_rationale}
                ),
            },
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

    def _submit_patch_execution(
        self,
        path: str,
        no_command_verification_rationale: str = "",
    ) -> AciExecution:
        execution = aci_submit_patch(self.workspace, path)
        review_notes = _review_submission(
            self.capture,
            execution.result,
            no_command_verification_rationale,
        )
        if review_notes:
            execution.result = execution.result.model_copy(
                update={
                    "success": False,
                    "error": review_notes,
                    "review_notes": review_notes,
                    "recovery_kind": "submit_review_failed",
                    "terminal_status": "blocked",
                }
            )
        else:
            notes = "submit-time review passed"
            if no_command_verification_rationale.strip():
                notes += (
                    "; no-command verification rationale accepted: "
                    + no_command_verification_rationale.strip()
                )
            execution.result = execution.result.model_copy(
                update={"review_notes": notes}
            )
        return execution

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
        result = _annotate_recovery_retry(
            self.capture,
            _annotate_aci_result(tool, execution.result),
        )
        self.capture.record_aci_result(result)
        if execution.undo_diff:
            self.capture.record_undo_diff(execution.undo_diff)
        self.trace.write(state, f"{event}.finished", {"result": _safe_result(result)})
        self.capture.record_step(
            AgentStep(
                step=len(self.capture.steps) + 1,
                phase=phase,
                tool=tool,
                input_summary=_summary(payload),
                result_summary=_result_summary(result),
                state=str(state),
                duration_seconds=duration,
                error=result.error,
                accepted=_step_accepted(result),
                recovery_kind=result.recovery_kind,
                terminal_status=result.terminal_status,
                retry_count=result.retry_count,
                terminal_after_retries=result.terminal_after_retries,
            )
        )
        return result


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


def _annotate_aci_result(tool: str, result: AciResult) -> AciResult:
    if result.success or result.recovery_kind or result.terminal_status:
        return result
    recovery_kind = _classify_recovery(result.error or result.output)
    terminal_status = _terminal_status_for_recovery(recovery_kind)
    return result.model_copy(
        update={"recovery_kind": recovery_kind, "terminal_status": terminal_status}
    )


def _annotate_recovery_retry(capture: ArtifactCapture, result: AciResult) -> AciResult:
    if result.success or not result.recovery_kind:
        return result
    retry_count = (
        sum(1 for item in capture.aci_results if item.recovery_kind == result.recovery_kind)
        + 1
    )
    terminal_after_retries = retry_count >= 3
    terminal_status = result.terminal_status
    if terminal_after_retries and terminal_status is None:
        terminal_status = (
            "format_exhausted"
            if result.recovery_kind
            in {"invalid_tool_arguments", "malformed_action", "multi_tool_action", "unknown_tool"}
            else "blocked"
        )
    return result.model_copy(
        update={
            "retry_count": retry_count,
            "terminal_after_retries": terminal_after_retries,
            "terminal_status": terminal_status,
        }
    )


def _classify_recovery(text: str) -> str:
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "command_timeout"
    if "output truncated" in lowered or "result limit reached" in lowered:
        return "too_large_output"
    if "must match exactly once" in lowered or "patch failed" in lowered:
        return "patch_failure"
    if "command not found" in lowered or "no such file or directory" in lowered:
        return "missing_dependency"
    if "path must" in lowered or "missing" in lowered or "unexpected" in lowered:
        return "invalid_tool_arguments"
    if "syntax error" in lowered:
        return "bash_syntax_error"
    return "tool_failure"


def _terminal_status_for_recovery(recovery_kind: str) -> str | None:
    if recovery_kind in {"command_timeout", "missing_dependency"}:
        return "blocked"
    return None


def _step_accepted(result: AciResult) -> bool:
    return result.recovery_kind not in {
        "invalid_tool_arguments",
        "malformed_action",
        "multi_tool_action",
        "submit_review_failed",
        "unknown_tool",
    }


def _review_submission(
    capture: ArtifactCapture,
    result: AciResult,
    no_command_verification_rationale: str = "",
) -> str:
    blockers: list[str] = []
    patch = result.output or ""
    if not result.success:
        blockers.append(result.error or "submit command failed")
    if not _has_patch_diff(patch):
        blockers.append("submit-time review rejected an empty or non-git diff")
    if _edited_after_last_successful_verification(capture):
        if not _valid_no_command_rationale(no_command_verification_rationale):
            blockers.append(
                "submit-time review requires successful focused verification after the last "
                "edit or a specific no-command verification rationale"
            )
    suspicious = _suspicious_patch_paths(_patch_paths(patch))
    if suspicious:
        blockers.append(
            "submit-time review rejected suspicious generated or temporary files: "
            + ", ".join(suspicious)
        )
    return "; ".join(blockers)


def _has_patch_diff(patch: str) -> bool:
    return patch.strip().startswith("diff --git ") or "\ndiff --git " in patch


def _valid_no_command_rationale(text: str) -> bool:
    stripped = text.strip().lower()
    if len(stripped) < 40:
        return False
    return any(
        marker in stripped
        for marker in (
            "unavailable",
            "not available",
            "not feasible",
            "no command",
            "no verifier",
            "directly inspectable",
            "reviewed the exact diff",
        )
    )


def _edited_after_last_successful_verification(capture: ArtifactCapture) -> bool:
    last_edit_index = -1
    last_verify_index = -1
    for index, item in enumerate(capture.aci_results):
        if item.tool in {"aci_replace", "aci_insert", "aci_create", "aci_undo"} and item.success:
            last_edit_index = index
        if item.tool == "aci_verify" and item.success:
            last_verify_index = index
    return last_edit_index >= 0 and last_verify_index < last_edit_index


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            path = parts[3].removeprefix("b/")
            paths.append(path)
    return paths


def _suspicious_patch_paths(paths: list[str]) -> list[str]:
    suspicious_markers = (
        "__pycache__/",
        ".pytest_cache/",
        "node_modules/",
        ".mypy_cache/",
        ".ruff_cache/",
        ".tox/",
        ".nox/",
        "dist/",
        "build/",
        ".egg-info/",
    )
    suspicious_suffixes = (".pyc", ".pyo", ".tmp", ".temp", ".log")
    rejected: list[str] = []
    for path in paths:
        lowered = path.lower()
        if lowered.startswith(("tmp/", "temp/")):
            rejected.append(path)
            continue
        if any(marker in lowered for marker in suspicious_markers):
            rejected.append(path)
            continue
        if lowered.endswith(suspicious_suffixes):
            rejected.append(path)
    return rejected
