from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable, TypeVar, cast

from contribarena.config.schema import RunConfig
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.engine.operator_events import (
    OperatorProgressWriter,
    truncate_for_operator,
)
from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.memory import MemoryService
from contribarena.models import AciResult, AgentStep, CommandResult, PatchResult, RunState
from contribarena.trace import TraceWriter
from contribarena.tools.aci import (
    AciExecution,
    aci_apply_patch,
    aci_clean_generated,
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
    operator: OperatorProgressWriter | None = None
    memory: MemoryService | None = None

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

    def aci_apply_patch(
        self,
        operations: list[dict[str, object]],
        rationale: str = "",
        expected_files: list[str] | None = None,
    ) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.apply_patch",
            phase="implementation",
            tool="aci_apply_patch",
            fn=lambda: aci_apply_patch(
                self.workspace,
                operations,
                rationale,
                expected_files or [],
            ),
            payload={
                "operation_count": len(operations) if isinstance(operations, list) else 0,
                "operation_types": _operation_values(operations, "type"),
                "paths": _operation_values(operations, "path"),
                "expected_files": expected_files or [],
                "rationale": truncate_for_operator(rationale, 240),
            },
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

    def aci_clean_generated(self, path: str = "repo") -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.clean_generated",
            phase="recovery",
            tool="aci_clean_generated",
            fn=lambda: aci_clean_generated(self.workspace, path),
            payload={"path": path},
        )

    def operator_report_progress(
        self,
        phase: str,
        status: str,
        summary: str,
        evidence_refs: str = "",
    ) -> AciResult:
        self.budget.record_step()
        start = time.monotonic()
        operator_phase = _clean_operator_phase(phase)
        operator_status = _clean_operator_status(status)
        operator_summary = truncate_for_operator(summary, 240)
        evidence = _parse_evidence_refs(evidence_refs)
        payload = {
            "phase": operator_phase,
            "status": operator_status,
            "summary": operator_summary,
            "evidence_refs": evidence,
        }
        self.trace.write(RunState.AGENT_ACTING, "operator.progress.started", payload)
        if self.operator is not None:
            self.operator.write(
                operator_phase,
                operator_status,
                operator_summary,
                source="agent",
                evidence=evidence,
                payload={"tool": "operator_report_progress"},
            )
        result = AciResult(
            tool="operator_report_progress",
            success=True,
            output="operator progress recorded",
        )
        duration = time.monotonic() - start
        self.trace.write(
            RunState.AGENT_ACTING,
            "operator.progress.finished",
            {"result": result.model_dump(mode="json")},
        )
        self.capture.record_aci_result(result)
        self.capture.record_step(
            AgentStep(
                step=len(self.capture.steps) + 1,
                phase=operator_phase,
                tool="operator_report_progress",
                input_summary=_summary(payload),
                result_summary=result.output,
                state=str(RunState.AGENT_ACTING),
                duration_seconds=duration,
            )
        )
        return result

    def aci_memory_get_context(self, scope: str = "run") -> AciResult:
        def run() -> AciResult:
            if self.memory is None or not self.memory.enabled:
                return _memory_disabled("aci_memory_get_context")
            if scope not in {"run", "repo", "global"}:
                return _memory_error("aci_memory_get_context", "invalid_memory_scope")
            return AciResult(
                tool="aci_memory_get_context",
                success=True,
                output=json.dumps(
                    self.memory.working.model_dump(mode="json")
                    if scope == "run"
                    else self.memory.context.model_dump(mode="json"),
                    ensure_ascii=True,
                ),
            )

        return self._record_memory(
            event="aci.memory_get_context",
            tool="aci_memory_get_context",
            payload={"scope": scope},
            fn=run,
        )

    def aci_memory_search(
        self,
        query: str,
        intent: str = "unknown",
        max_results: int = 5,
    ) -> AciResult:
        def run() -> AciResult:
            if self.memory is None or not self.memory.enabled:
                return _memory_disabled("aci_memory_search")
            result = self.memory.search_for_agent(
                query,
                intent=intent,
                max_results=max(1, min(max_results, 10)),
            )
            return AciResult(
                tool="aci_memory_search",
                success=result.success,
                output=result.model_dump_json(),
                error=result.error_kind or None if not result.success else None,
                recovery_kind=result.error_kind or None if not result.success else None,
            )

        return self._record_memory(
            event="aci.memory_search",
            tool="aci_memory_search",
            payload={"query": query, "intent": intent, "max_results": max_results},
            fn=run,
        )

    def aci_memory_note(
        self,
        scope: str,
        text: str,
        tags_json: str = "[]",
        confidence: str = "medium",
    ) -> AciResult:
        def run() -> AciResult:
            if self.memory is None or not self.memory.enabled:
                return _memory_disabled("aci_memory_note")
            try:
                tags = json.loads(tags_json) if tags_json else []
            except json.JSONDecodeError as exc:
                return _memory_error("aci_memory_note", f"invalid tags_json: {exc}")
            if not isinstance(tags, list):
                return _memory_error("aci_memory_note", "tags_json must decode to a list")
            write = self.memory.note_agent_memory(
                scope,
                text,
                [str(tag) for tag in tags],
                confidence,
                source_ref="aci_memory_note",
            )
            return AciResult(
                tool="aci_memory_note",
                success=write.success,
                output=write.model_dump_json(),
                error=write.error_message or None if not write.success else None,
                recovery_kind=write.error_kind or None if not write.success else None,
            )

        return self._record_memory(
            event="aci.memory_note",
            tool="aci_memory_note",
            payload={"scope": scope, "tags_json": tags_json, "confidence": confidence},
            fn=run,
        )

    def aci_memory_plan_update(
        self,
        action: str,
        item_id: str = "",
        text: str = "",
        status: str = "",
    ) -> AciResult:
        def run() -> AciResult:
            if self.memory is None or not self.memory.enabled:
                return _memory_disabled("aci_memory_plan_update")
            write = self.memory.plan_update(action, item_id=item_id, text=text, status=status)
            return AciResult(
                tool="aci_memory_plan_update",
                success=write.success,
                output=write.model_dump_json(),
                error=write.error_message or None if not write.success else None,
                recovery_kind=write.error_kind or None if not write.success else None,
            )

        return self._record_memory(
            event="aci.memory_plan_update",
            tool="aci_memory_plan_update",
            payload={"action": action, "item_id": item_id, "status": status},
            fn=run,
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
                    review_notes=(f"attempted_tool={attempted_tool}" if attempted_tool else ""),
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
            fn=lambda: self._submit_patch_execution(path, no_command_verification_rationale),
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
            execution.result = execution.result.model_copy(update={"review_notes": notes})
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
        self._write_operator_event(event, "finished", result, payload)
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
        if phase == "recovery":
            self.trace.write(
                RunState.AGENT_RECOVERING,
                "agent.recovering",
                {
                    "tool": tool,
                    "recovery_kind": result.recovery_kind,
                    "terminal_status": result.terminal_status,
                },
            )
        elif phase in {"exploration", "implementation", "verification", "submission"}:
            self.trace.write(
                RunState.AGENT_ACTING,
                "agent.acting",
                {"phase": phase, "tool": tool, "success": result.success},
            )
        if (
            tool
            in {
                "aci_apply_patch",
                "aci_replace",
                "aci_insert",
                "aci_create",
                "aci_undo",
            }
            and result.success
        ):
            self.trace.write(
                RunState.WORKSPACE_DIRTY,
                "workspace.dirty",
                {"tool": tool, "files_modified": result.files_modified},
            )
        if tool == "aci_submit_patch" and result.success:
            self.trace.write(
                RunState.WORKSPACE_PATCH_CAPTURED,
                "workspace.patch_captured",
                {"bytes": len((result.output or "").encode("utf-8"))},
            )
        if self.memory is not None:
            try:
                self.memory.record_tool_observation(
                    str(len(self.capture.steps) + 1),
                    tool,
                    payload,
                    result.model_dump(mode="json"),
                )
            except Exception as exc:  # pragma: no cover - memory must fail soft
                self.trace.write(
                    RunState.AGENT_ACTING,
                    "memory.tool_observation_failed",
                    {"tool": tool, "error": str(exc)},
                )
        self.trace.write(state, f"{event}.finished", {"result": _safe_result(result)})
        self._write_operator_event(event, "finished", result, payload, phase=phase, tool=tool)
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

    def _record_memory(
        self,
        *,
        event: str,
        tool: str,
        payload: dict[str, Any],
        fn: Callable[[], AciResult],
    ) -> AciResult:
        self.budget.record_step()
        start = time.monotonic()
        self.trace.write(RunState.AGENT_ACTING, f"{event}.started", payload)
        result = _annotate_aci_result(tool, fn())
        duration = time.monotonic() - start
        self.capture.record_aci_result(result)
        self.trace.write(RunState.AGENT_ACTING, f"{event}.finished", {"result": _safe_result(result)})
        self._write_operator_event(event, "finished", result, payload, phase="memory", tool=tool)
        self.capture.record_step(
            AgentStep(
                step=len(self.capture.steps) + 1,
                phase="memory",
                tool=tool,
                input_summary=_summary(payload),
                result_summary=_result_summary(result),
                state=str(RunState.AGENT_ACTING),
                duration_seconds=duration,
                error=result.error,
                accepted=_step_accepted(result),
                recovery_kind=result.recovery_kind,
                terminal_status=result.terminal_status,
            )
        )
        return result

    def _write_operator_event(
        self,
        event: str,
        status: str,
        result: object,
        payload: dict[str, Any],
        *,
        phase: str | None = None,
        tool: str | None = None,
    ) -> None:
        if self.operator is None:
            return
        operator_phase = (
            _operator_phase_for_aci(phase) if phase else _operator_phase_for_event(event)
        )
        operator_status = _operator_status(result, status)
        operator_tool = tool or event
        self.operator.write(
            operator_phase,
            operator_status,
            _operator_summary(operator_tool, result, payload),
            evidence=["trace.jsonl", "trajectory.json"],
            payload={
                "tool": operator_tool,
                "event": event,
                "input": _operator_payload(payload),
            },
        )


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


def _operator_phase_for_event(event: str) -> str:
    if event == "repo.search" or event == "repo.eligibility":
        return "repo_scope"
    if event == "repo.metadata" or event == "repo.issues":
        return "task_discovery"
    if event == "workspace.run":
        return "coding"
    if event == "workspace.apply_patch":
        return "coding"
    return "agent"


def _operator_phase_for_aci(phase: str) -> str:
    if phase == "exploration":
        return "task_discovery"
    if phase == "implementation":
        return "coding"
    if phase == "submission":
        return "submit_patch"
    return phase


def _operator_status(result: object, fallback: str) -> str:
    if isinstance(result, AciResult):
        return "ok" if result.success else "needs_attention"
    if isinstance(result, CommandResult):
        return "ok" if result.exit_code == 0 and not result.timed_out else "needs_attention"
    if isinstance(result, PatchResult):
        return "ok" if result.success else "needs_attention"
    return "ok" if fallback == "finished" else fallback


def _operator_summary(tool: str, result: object, payload: dict[str, Any]) -> str:
    if tool.startswith("aci_memory_"):
        return f"updated or queried run memory with {tool}"
    if tool in {"aci_view", "aci_search", "aci_find_files"}:
        return f"inspected repository context with {tool}"
    if tool in {"aci_apply_patch", "aci_replace", "aci_insert", "aci_create", "aci_undo"}:
        return f"updated workspace with {tool}"
    if tool in {"aci_verify", "aci_suggest_verification"}:
        return f"checked verification path with {tool}"
    if tool == "aci_submit_patch":
        if isinstance(result, AciResult) and result.success:
            return "submitted a candidate patch for harness review"
        return "patch submission needs recovery"
    if tool.startswith("repo."):
        return f"queried repository signal: {tool}"
    if tool == "workspace.run":
        return "ran workspace command: " + truncate_for_operator(payload.get("cmd", ""))
    if tool == "workspace.apply_patch":
        return "applied workspace patch"
    return f"completed {tool}"


def _operator_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: truncate_for_operator(value) for key, value in payload.items()}


def _operation_values(operations: object, key: str) -> list[str]:
    if not isinstance(operations, list):
        return []
    values: list[str] = []
    for item in operations[:20]:
        if isinstance(item, dict):
            values.append(str(item.get(key) or ""))
    return values


def _clean_operator_phase(value: str) -> str:
    allowed = {
        "repo_scope",
        "task_discovery",
        "task_selection",
        "coding",
        "verification",
        "submit_patch",
        "quality_gate",
        "governance_gate",
        "pr_submit",
        "ci_observe",
        "postmortem",
    }
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in allowed else "agent"


def _clean_operator_status(value: str) -> str:
    allowed = {"working", "selected", "blocked", "needs_attention", "completed"}
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in allowed else "working"


def _parse_evidence_refs(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return [
            truncate_for_operator(item.strip(), 180) for item in stripped.split(",") if item.strip()
        ][:20]
    if isinstance(parsed, list):
        return [truncate_for_operator(item, 180) for item in parsed[:20]]
    return [truncate_for_operator(parsed, 180)]


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


def _memory_disabled(tool: str) -> AciResult:
    return AciResult(
        tool=tool,
        success=False,
        output="memory is disabled",
        error="memory is disabled",
        recovery_kind="memory_disabled",
    )


def _memory_error(tool: str, message: str) -> AciResult:
    return AciResult(
        tool=tool,
        success=False,
        output=message,
        error=message,
        recovery_kind="memory_tool_error",
    )


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
        sum(1 for item in capture.aci_results if item.recovery_kind == result.recovery_kind) + 1
    )
    terminal_after_retries = retry_count >= 3
    terminal_status = result.terminal_status
    if terminal_after_retries and terminal_status is None:
        terminal_status = "failed_to_recover"
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
            + "; call aci_clean_generated(path='repo'), rerun focused verification if needed, "
            "then submit again"
        )
    untracked = _untracked_patch_paths(capture, _patch_paths(patch))
    if untracked:
        blockers.append(
            "submit-time review rejected changed files without unified editor provenance: "
            + ", ".join(untracked)
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
        if (
            item.tool
            in {
                "aci_apply_patch",
                "aci_replace",
                "aci_insert",
                "aci_create",
                "aci_undo",
            }
            and item.success
        ):
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


def _untracked_patch_paths(capture: ArtifactCapture, paths: list[str]) -> list[str]:
    if not paths:
        return []
    known = _known_editor_paths(capture)
    return [path for path in paths if _normalize_patch_path(path) not in known]


def _known_editor_paths(capture: ArtifactCapture) -> set[str]:
    tools = {"aci_apply_patch", "aci_replace", "aci_insert", "aci_create", "aci_undo"}
    paths: set[str] = set()
    for result in capture.aci_results:
        if result.tool not in tools or not result.success:
            continue
        for path in result.files_modified:
            normalized = _normalize_patch_path(path)
            paths.add(normalized)
            if normalized.startswith("repo/"):
                paths.add(normalized.removeprefix("repo/"))
    return paths


def _normalize_patch_path(path: str) -> str:
    return path.removeprefix("a/").removeprefix("b/")
