from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable, TypeVar, cast

from agents.models.interface import ModelProvider

from contribarena.config.schema import RunConfig
from contribarena.engine.goals import GoalService
from contribarena.engine.maintainer_review import run_maintainer_prereview
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.engine.operator_events import (
    OperatorProgressWriter,
    truncate_for_operator,
)
from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.memory import MemoryService
from contribarena.memory.schema import GuidanceContext, MemoryCapabilities
from contribarena.models import AciResult, AgentStep, CommandResult, PatchResult, RunState
from contribarena.models.tool_results import CommandType
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
from contribarena.tools.repo_prs import (
    repo_get_issue_linkage,
    repo_get_open_prs,
    repo_get_pr_review_history,
    repo_get_recent_merged_prs,
    repo_search_prs_by_title,
)
from contribarena.tools.repo_readme import repo_get_readme
from contribarena.tools.repo_search import repo_search_with_log
from contribarena.tools.repo_setup_probe import repo_setup_probe
from contribarena.tools.workspace_patch import workspace_apply_patch
from contribarena.tools.workspace_run import workspace_run

T = TypeVar("T")
PhaseKey = tuple[str, str | None]


ALLOWED_PHASES: dict[str, set[PhaseKey]] = {
    "repo.search": {("scout", "project")},
    "repo.eligibility": {("scout", "project"), ("scout", "opportunity")},
    "repo.metadata": {("scout", "project"), ("scout", "opportunity")},
    "repo.readme": {("scout", "project"), ("scout", "opportunity")},
    "repo.setup_probe": {("scout", "project")},
    "repo.issues": {("scout", "opportunity")},
    "repo.open_prs": {("scout", "opportunity")},
    "repo.recent_merged_prs": {("scout", "opportunity")},
    "repo.search_prs_by_title": {("scout", "opportunity")},
    "repo.issue_linkage": {("scout", "opportunity")},
    "repo.pr_review_history": {("scout", "opportunity"), ("review", None)},
    "aci_view": {("scout", "project"), ("scout", "opportunity"), ("work", None), ("review", None)},
    "aci_search": {("scout", "opportunity"), ("work", None), ("review", None)},
    "aci_find_files": {
        ("scout", "project"),
        ("scout", "opportunity"),
        ("work", None),
        ("review", None),
    },
    "aci_apply_patch": {("work", None), ("review", None)},
    "aci_replace": {("work", None), ("review", None)},
    "aci_insert": {("work", None), ("review", None)},
    "aci_create": {("work", None), ("review", None)},
    "aci_undo": {("work", None), ("review", None)},
    "aci_verify": {("work", None), ("review", None)},
    "aci_suggest_verification": {("work", None), ("review", None)},
    "aci_clean_generated": {("work", None), ("review", None)},
    "aci_submit_patch": {("work", None), ("review", None)},
    "aci_submit_patch_finalize": {("review", None)},
    "aci_dispute_review": {("review", None)},
}

ALWAYS_ALLOWED_TOOLS = {
    "aci_goal_update",
    "aci_memory_get_context",
    "aci_memory_search",
    "aci_memory_note",
    "aci_memory_plan_update",
    "aci_runtime_get_context",
    "aci_recover_invalid_action",
    "operator_report_progress",
    "workspace.run",
    "workspace.apply_patch",
}

SCOUT_PROJECT_BUDGET_TOOLS = {
    "repo.eligibility",
    "repo.metadata",
    "repo.readme",
    "repo.setup_probe",
}
SCOUT_OPPORTUNITY_BUDGET_TOOLS = {"repo.issues"}
SCOUT_DUPLICATE_BUDGET_TOOLS = {
    "repo.open_prs",
    "repo.recent_merged_prs",
    "repo.search_prs_by_title",
    "repo.issue_linkage",
    "repo.pr_review_history",
}


@dataclass
class ToolRegistry:
    config: RunConfig
    workspace: DockerWorkspaceManager
    trace: TraceWriter
    budget: BudgetTracker
    capture: ArtifactCapture
    operator: OperatorProgressWriter | None = None
    memory: MemoryService | None = None
    goals: GoalService | None = None
    model_provider: ModelProvider | None = None

    def current_phase_key(self) -> PhaseKey:
        if self.goals is None:
            return ("scout", "project")
        context = self.goals.context
        return (context.current_phase, context.current_sub_phase)

    def repo_search(self, query: str = "", filters: object | None = None) -> object:
        def run_search() -> object:
            result = repo_search_with_log(self.config, query=query, filters=filters)
            self.capture.record_discovery(result.log_row)
            return result.candidates

        return self._record(
            state=RunState.REPO_DISCOVERED,
            event="repo.search",
            fn=run_search,
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

    def repo_get_readme(self, candidate: object, max_chars: int = 6000) -> object:
        return self._record(
            state=RunState.REPO_PROFILED,
            event="repo.readme",
            fn=lambda: repo_get_readme(candidate, max_chars=max_chars),  # type: ignore[arg-type]
            payload={"candidate": str(candidate), "max_chars": max_chars},
        )

    def repo_get_issues(self, candidate: object, filters: object | None = None) -> object:
        return self._record(
            state=RunState.OPPORTUNITIES_RANKED,
            event="repo.issues",
            fn=lambda: repo_get_issues(candidate, filters=filters),  # type: ignore[arg-type]
            payload={"candidate": str(candidate), "filters": str(filters)},
        )

    def repo_get_open_prs(self, candidate: object, limit: int = 30) -> object:
        return self._record(
            state=RunState.OPPORTUNITIES_RANKED,
            event="repo.open_prs",
            fn=lambda: repo_get_open_prs(candidate, limit=limit),  # type: ignore[arg-type]
            payload={"candidate": str(candidate), "limit": limit},
        )

    def repo_get_recent_merged_prs(self, candidate: object, limit: int = 30) -> object:
        return self._record(
            state=RunState.OPPORTUNITIES_RANKED,
            event="repo.recent_merged_prs",
            fn=lambda: repo_get_recent_merged_prs(candidate, limit=limit),  # type: ignore[arg-type]
            payload={"candidate": str(candidate), "limit": limit},
        )

    def repo_search_prs_by_title(
        self, candidate: object, query: str, limit: int = 20
    ) -> object:
        return self._record(
            state=RunState.OPPORTUNITIES_RANKED,
            event="repo.search_prs_by_title",
            fn=lambda: repo_search_prs_by_title(candidate, query, limit=limit),  # type: ignore[arg-type]
            payload={"candidate": str(candidate), "query": query, "limit": limit},
        )

    def repo_get_issue_linkage(self, candidate: object, issue_number: int) -> object:
        return self._record(
            state=RunState.OPPORTUNITIES_RANKED,
            event="repo.issue_linkage",
            fn=lambda: repo_get_issue_linkage(candidate, issue_number),  # type: ignore[arg-type]
            payload={"candidate": str(candidate), "issue_number": issue_number},
        )

    def repo_get_pr_review_history(self, candidate: object, limit: int = 20) -> object:
        return self._record(
            state=RunState.OPPORTUNITIES_RANKED,
            event="repo.pr_review_history",
            fn=lambda: repo_get_pr_review_history(candidate, limit=limit),  # type: ignore[arg-type]
            payload={"candidate": str(candidate), "limit": limit},
        )

    def repo_setup_probe(
        self,
        candidate: object,
        max_probe_seconds: int | None = None,
        install_dependencies: bool = False,
    ) -> object:
        seconds = max_probe_seconds or self.config.run.budget.scout.max_probe_seconds_per_repo

        def run() -> object:
            probe, command = repo_setup_probe(
                self.workspace,
                candidate,  # type: ignore[arg-type]
                max_probe_seconds=seconds,
                install_dependencies=install_dependencies,
            )
            self.capture.record_command(_typed_command(command, "setup"))
            return probe

        return self._record(
            state=RunState.WORKSPACE_CHECKED,
            event="repo.setup_probe",
            fn=run,
            payload={
                "candidate": str(candidate),
                "max_probe_seconds": seconds,
                "install_dependencies": install_dependencies,
            },
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
        result = _typed_command(result, _infer_command_type(cmd))
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
        if self.current_phase_key()[0] == "review" and _review_resubmit_rounds_used(
            self.capture
        ) >= self.config.run.budget.review.max_review_rounds:
            return _review_round_limit_result("aci_apply_patch")
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

    def aci_dispute_review(
        self,
        concern_id: str,
        rebuttal_text: str,
        evidence_refs_json: str = "[]",
    ) -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.dispute_review",
            phase="review",
            tool="aci_dispute_review",
            fn=lambda: self._dispute_review_execution(
                concern_id,
                rebuttal_text,
                evidence_refs_json,
            ),
            payload={
                "concern_id": concern_id,
                "rebuttal_bytes": len(rebuttal_text.encode("utf-8")),
                "evidence_refs_json": evidence_refs_json,
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

    def aci_runtime_get_context(self, scope: str = "run") -> AciResult:
        def run() -> AciResult:
            if scope != "run":
                return AciResult(
                    tool="aci_runtime_get_context",
                    success=False,
                    output="invalid_runtime_scope",
                    error="invalid_runtime_scope",
                    recovery_kind="invalid_runtime_scope",
                )
            memory_working = self.memory.working if self.memory is not None else None
            memory_enabled = bool(self.memory is not None and self.memory.enabled)
            goals = self.goals.context if self.goals is not None else None
            memory_capabilities = (
                memory_working.memory_capabilities
                if memory_enabled and memory_working is not None
                else MemoryCapabilities(
                    run_scope_notes=False,
                    repo_scope_persistent=False,
                    global_scope_persistent=False,
                    note="memory disabled or unavailable",
                )
            )
            payload = {
                "schema_version": "1",
                "run_id": self.trace.run_id,
                "run_mode": self.config.run.mode,
                "repo_full_name": (
                    self.memory.repo_full_name
                    if self.memory is not None
                    else _configured_repo_full_name(self.config)
                ),
                "goals": goals.model_dump(mode="json") if goals is not None else None,
                "guidance": (
                    memory_working.guidance.model_dump(mode="json")
                    if memory_working is not None
                    else GuidanceContext().model_dump(mode="json")
                ),
                "memory_enabled": memory_enabled,
                "memory_capabilities": memory_capabilities.model_dump(mode="json"),
                "memory_hints": (
                    [hint.model_dump(mode="json") for hint in memory_working.memory_hints]
                    if memory_enabled and memory_working is not None
                    else []
                ),
                "tracked_prs": (
                    [tracked.model_dump(mode="json") for tracked in memory_working.tracked_prs]
                    if memory_working is not None
                    else []
                ),
            }
            return AciResult(
                tool="aci_runtime_get_context",
                success=True,
                output=json.dumps(payload, ensure_ascii=True),
            )

        return self._record_memory(
            event="aci.runtime_get_context",
            tool="aci_runtime_get_context",
            payload={"scope": scope},
            fn=run,
            phase="runtime",
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

    def aci_goal_update(
        self,
        objective: str = "",
        status: str = "active",
        evidence: str = "",
        scope: str = "",
        evidence_refs_json: str = "[]",
        next_objective: str = "",
    ) -> AciResult:
        def run() -> AciResult:
            if self.goals is None or not self.goals.enabled:
                return _goal_error("goal_disabled", "Goal tracking is disabled for this run.")
            try:
                evidence_refs = json.loads(evidence_refs_json) if evidence_refs_json else []
            except json.JSONDecodeError as exc:
                return _goal_error(
                    "invalid_evidence_refs",
                    f"evidence_refs_json must decode to a JSON list: {exc}",
                )
            if not isinstance(evidence_refs, list) or not all(
                isinstance(item, str) for item in evidence_refs
            ):
                return _goal_error(
                    "invalid_evidence_refs",
                    "evidence_refs_json must decode to a JSON list of strings.",
                )
            budget_error = self._goal_transition_budget_error(status, scope or None)
            if budget_error is not None:
                return budget_error
            update = self.goals.update(
                objective=objective,
                status=status,
                evidence=evidence,
                scope=scope or None,
                evidence_refs=evidence_refs,
                next_objective=next_objective,
            )
            if self.memory is not None:
                self.memory.set_goal_context(self.goals.context)
            terminal_status = _terminal_status_for_goal_event(update.event)
            if (
                update.success
                and update.event is not None
                and update.event.event_type == "goal_abandoned"
                and self.goals.abandoned_count >= self.config.goal.max_abandoned_goals_per_run
            ):
                terminal_status = "goal_abandon_limit"
            return AciResult(
                tool="aci_goal_update",
                success=update.success,
                output=update.model_dump_json(),
                error=update.error_message or None if not update.success else None,
                recovery_kind=update.error_kind or None if not update.success else None,
                terminal_status=terminal_status,
            )

        return self._record_memory(
            event="aci.goal_update",
            tool="aci_goal_update",
            payload={
                "status": status,
                "scope": scope,
                "objective_bytes": len(objective.encode("utf-8")),
                "evidence_bytes": len(evidence.encode("utf-8")),
                "evidence_refs": len(evidence_refs_json.encode("utf-8")),
                "next_objective_bytes": len(next_objective.encode("utf-8")),
            },
            fn=run,
        )

    def _goal_transition_budget_error(
        self,
        status: str,
        scope: str | None,
    ) -> AciResult | None:
        if self.goals is None:
            return None
        normalized_scope = scope or (
            self.goals.state.short_term.scope if self.goals.state.short_term is not None else ""
        )
        if status == "abandoned" and normalized_scope == "repo":
            if self.goals.abandoned_count_for_scope("repo") >= self.config.run.budget.work.max_repo_switches:
                return _goal_error(
                    "repo_switch_limit",
                    "This run has reached max_repo_switches.",
                    terminal_status="repo_switch_limit",
                )
        if status == "abandoned" and normalized_scope == "opportunity":
            if (
                self.goals.abandoned_count_for_scope("opportunity")
                >= self.config.run.budget.work.max_opportunity_switches
            ):
                return _goal_error(
                    "opportunity_switch_limit",
                    "This run has reached max_opportunity_switches.",
                    terminal_status="opportunity_switch_limit",
                )
        if status == "superseded" and normalized_scope == "contribution":
            strategy_switches = sum(
                1
                for event in self.goals.events
                if event.event_type == "goal_superseded" and event.scope == "contribution"
            )
            if strategy_switches >= self.config.run.budget.work.max_strategy_switches_per_opportunity:
                return _goal_error(
                    "strategy_switch_limit",
                    "This opportunity has reached max_strategy_switches_per_opportunity; abandon or finalize.",
                )
        if status == "active" and normalized_scope == "repo":
            if self.goals.abandoned_count_for_scope("repo") >= self.config.run.budget.work.max_repo_switches:
                return _goal_error(
                    "repo_switch_limit",
                    "This run has reached max_repo_switches.",
                    terminal_status="repo_switch_limit",
                )
        if status == "active" and normalized_scope == "opportunity":
            if (
                self.goals.abandoned_count_for_scope("opportunity")
                >= self.config.run.budget.work.max_opportunity_switches
            ):
                return _goal_error(
                    "opportunity_switch_limit",
                    "This run has reached max_opportunity_switches.",
                    terminal_status="opportunity_switch_limit",
                )
        if (
            status == "active"
            and self.goals.abandoned_count >= self.config.goal.max_abandoned_goals_per_run
        ):
            return _goal_error(
                "goal_abandon_limit",
                "This run has reached the short-term goal abandon limit.",
                terminal_status="goal_abandon_limit",
            )
        return None

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

    def aci_submit_patch_finalize(self, path: str = "repo") -> AciResult:
        return self._record_aci(
            state=RunState.WORKSPACE_CHECKED,
            event="aci.submit_patch_finalize",
            phase="submission",
            tool="aci_submit_patch_finalize",
            fn=lambda: AciExecution(
                result=AciResult(
                    tool="aci_submit_patch_finalize",
                    success=bool(_latest_successful_submit(self.capture)),
                    output=_latest_successful_submit(self.capture).output
                    if _latest_successful_submit(self.capture) is not None
                    else "",
                    error=None
                    if _latest_successful_submit(self.capture) is not None
                    else "finalize requires a successful draft aci_submit_patch first",
                    recovery_kind=None
                    if _latest_successful_submit(self.capture) is not None
                    else "missing_draft_submission",
                    review_notes="finalized draft submission for quality gate",
                )
            ),
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

    def _submit_patch_execution(
        self,
        path: str,
        no_command_verification_rationale: str = "",
    ) -> AciExecution:
        if self.current_phase_key()[0] == "review" and _review_resubmit_rounds_used(
            self.capture
        ) >= self.config.run.budget.review.max_review_rounds:
            return AciExecution(result=_review_round_limit_result("aci_submit_patch"))
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

    def _dispute_review_execution(
        self,
        concern_id: str,
        rebuttal_text: str,
        evidence_refs_json: str,
    ) -> AciExecution:
        concern_id = concern_id.strip()
        rebuttal_text = rebuttal_text.strip()
        if not concern_id:
            return AciExecution(
                result=AciResult(
                    tool="aci_dispute_review",
                    success=False,
                    output="concern_id is required",
                    error="concern_id is required",
                    recovery_kind="invalid_tool_arguments",
                )
            )
        if len(rebuttal_text) < 20:
            return AciExecution(
                result=AciResult(
                    tool="aci_dispute_review",
                    success=False,
                    output="rebuttal_text must explain the evidence-backed disagreement",
                    error="rebuttal_text must explain the evidence-backed disagreement",
                    recovery_kind="invalid_tool_arguments",
                )
            )
        try:
            refs = json.loads(evidence_refs_json) if evidence_refs_json else []
        except json.JSONDecodeError as exc:
            return AciExecution(
                result=AciResult(
                    tool="aci_dispute_review",
                    success=False,
                    output=f"evidence_refs_json must decode to a JSON list: {exc}",
                    error=f"evidence_refs_json must decode to a JSON list: {exc}",
                    recovery_kind="invalid_evidence_refs",
                )
            )
        if not isinstance(refs, list) or not all(isinstance(item, str) for item in refs):
            return AciExecution(
                result=AciResult(
                    tool="aci_dispute_review",
                    success=False,
                    output="evidence_refs_json must decode to a JSON list of strings.",
                    error="evidence_refs_json must decode to a JSON list of strings.",
                    recovery_kind="invalid_evidence_refs",
                )
            )
        if not refs:
            return AciExecution(
                result=AciResult(
                    tool="aci_dispute_review",
                    success=False,
                    output="aci_dispute_review requires evidence_refs.",
                    error="aci_dispute_review requires evidence_refs.",
                    recovery_kind="missing_evidence_refs",
                )
            )
        if self.goals is not None:
            ref_error = self.goals.validate_evidence_refs([str(item) for item in refs])
            if ref_error is not None:
                return AciExecution(
                    result=AciResult(
                        tool="aci_dispute_review",
                        success=False,
                        output=ref_error.error_message or "invalid evidence_refs",
                        error=ref_error.error_message or "invalid evidence_refs",
                        recovery_kind=ref_error.error_kind or "invalid_evidence_refs",
                    )
                )
        self.capture.record_phase_artifact(
            "phase_review_response",
            {
                "schema_version": "1",
                "tool_call_id": f"aci_dispute_review:{len(self.capture.steps) + 1}",
                "tool": "aci_dispute_review",
                "phase": "review",
                "action": "dispute",
                "concern_id": concern_id,
                "rebuttal": truncate_for_operator(rebuttal_text, 1000),
                "evidence_refs": refs,
                "ts": time.time(),
            },
        )
        return AciExecution(
            result=AciResult(
                tool="aci_dispute_review",
                success=True,
                output="review concern disputed with evidence; no simulator rerun triggered",
            )
        )

    def _record(
        self,
        state: str,
        event: str,
        fn: Callable[[], T],
        payload: dict[str, Any],
    ) -> T:
        scout_limit = self._scout_budget_exhausted(event, payload)
        if scout_limit is not None:
            return cast(T, scout_limit)
        violation = self._phase_violation(event, payload)
        if violation is not None:
            return cast(T, violation)
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
        self._record_phase_projection(event, payload, result)
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
        violation = self._phase_violation(tool, payload, phase=phase)
        if violation is not None:
            return violation
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
        command_type = _aci_tool_command_type(tool)
        for command in execution.commands:
            typed = _typed_command(command, command_type)
            if command_type == "other":
                typed = _typed_command(typed, _infer_command_type(typed.command))
            self.capture.record_command(typed)
        for patch in execution.patches:
            self.capture.record_patch(patch)
        result = _annotate_recovery_retry(
            self.capture,
            _annotate_aci_result(tool, execution.result),
        )
        self.capture.record_aci_result(result)
        self._record_phase_projection(tool, payload, result)
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
            if self.goals is not None:
                self.goals.record_draft_submitted(
                    evidence="aci_submit_patch produced a draft patch."
                )
                if self.memory is not None:
                    self.memory.set_goal_context(self.goals.context)
            round_number = _next_maintainer_review_round(self.capture)
            review = run_maintainer_prereview(
                config=self.config,
                model_provider=self.model_provider,
                capture=self.capture,
                patch=result.output or "",
                round_number=round_number,
            )
            self.capture.record_phase_artifact(
                "phase_review_maintainer_review",
                review.row,
            )
            result = _attach_maintainer_review(result, review.row)
            self.capture.aci_results[-1] = result
            self.trace.write(
                RunState.WORKSPACE_PATCH_CAPTURED,
                "workspace.patch_captured",
                {
                    "bytes": len((result.output or "").encode("utf-8")),
                    "review_round": round_number,
                    "review_status": review.row.get("status"),
                },
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
        phase: str = "memory",
    ) -> AciResult:
        violation = self._phase_violation(tool, payload, phase=phase)
        if violation is not None:
            return violation
        self.budget.record_step()
        start = time.monotonic()
        self.trace.write(RunState.AGENT_ACTING, f"{event}.started", payload)
        result = _annotate_aci_result(tool, fn())
        duration = time.monotonic() - start
        self.capture.record_aci_result(result)
        self.trace.write(RunState.AGENT_ACTING, f"{event}.finished", {"result": _safe_result(result)})
        self._record_phase_projection(tool, payload, result)
        self._write_operator_event(event, "finished", result, payload, phase=phase, tool=tool)
        self.capture.record_step(
            AgentStep(
                step=len(self.capture.steps) + 1,
                phase=phase,
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

    def _record_phase_projection(
        self,
        tool: str,
        payload: dict[str, Any],
        result: object,
    ) -> None:
        phase, sub_phase = self.current_phase_key()
        row = {
            "schema_version": "1",
            "tool_call_id": f"{tool}:{len(self.capture.steps) + 1}",
            "tool": tool,
            "phase": phase,
            "sub_phase": sub_phase,
            "success": bool(getattr(result, "success", True)),
            "input_summary": _summary(payload),
            "result_summary": _result_summary(result),
            "ts": time.time(),
        }
        if phase == "scout" and sub_phase == "project" and tool in {
            "repo.search",
            "repo.eligibility",
            "repo.metadata",
            "repo.readme",
            "repo.setup_probe",
            "aci_find_files",
            "aci_view",
        }:
            self.capture.record_phase_artifact("phase_scout_project_comparison", row)
        elif phase == "scout" and sub_phase == "opportunity" and tool in {
            "repo.issues",
            "aci_view",
            "aci_search",
            "aci_find_files",
        }:
            self.capture.record_phase_artifact("phase_scout_opportunity_comparison", row)
        elif phase == "scout" and sub_phase == "opportunity" and tool in {
            "repo.open_prs",
            "repo.recent_merged_prs",
            "repo.search_prs_by_title",
            "repo.issue_linkage",
            "repo.pr_review_history",
        }:
            self.capture.record_phase_artifact("phase_scout_duplicate_check", row)
        elif phase == "review" and tool in {
            "aci_submit_patch",
            "aci_submit_patch_finalize",
            "aci_dispute_review",
        }:
            self.capture.record_phase_artifact("phase_review_response", row)
        elif tool == "aci_goal_update" and bool(getattr(result, "success", False)):
            goal_row = _goal_projection_row(row, result)
            if goal_row.get("scope") == "opportunity":
                self.capture.record_phase_artifact(
                    "phase_scout_project_comparison",
                    {**goal_row, "decision": "selected_project"},
                )
            elif goal_row.get("scope") == "contribution":
                self.capture.record_phase_artifact(
                    "phase_scout_opportunity_comparison",
                    {**goal_row, "decision": "selected_opportunity"},
                )

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

    def _phase_violation(
        self,
        tool: str,
        payload: dict[str, Any],
        *,
        phase: str | None = None,
    ) -> AciResult | None:
        if tool in ALWAYS_ALLOWED_TOOLS:
            return None
        allowed = ALLOWED_PHASES.get(tool)
        if allowed is None:
            return None
        current_phase, current_sub_phase = self.current_phase_key()
        if (current_phase, current_sub_phase) in allowed or (current_phase, None) in allowed:
            return None
        result = AciResult(
            tool=tool,
            success=False,
            output=f"phase_violation: tool {tool} not allowed in {current_phase}/{current_sub_phase}",
            error=f"phase_violation: tool {tool} not allowed in {current_phase}/{current_sub_phase}",
            recovery_kind="phase_violation",
        )
        self.capture.record_aci_result(result)
        self.capture.record_tool_violation(
            {
                "schema_version": "1",
                "tool": tool,
                "phase": current_phase,
                "sub_phase": current_sub_phase,
                "recovery_kind": "phase_violation",
                "input_summary": _summary(payload),
                "ts": time.time(),
            }
        )
        self.trace.write(
            RunState.AGENT_RECOVERING,
            "agent.phase_violation",
            {
                "tool": tool,
                "phase": current_phase,
                "sub_phase": current_sub_phase,
                "allowed": sorted(f"{item[0]}/{item[1] or '*'}" for item in allowed),
            },
        )
        if self.operator is not None:
            self.operator.write(
                "agent",
                "needs_attention",
                f"tool blocked by phase gate: {tool}",
                evidence=["trace.jsonl", "trajectory.json"],
                payload={
                    "tool": tool,
                    "phase": current_phase,
                    "sub_phase": current_sub_phase,
                    "input": _operator_payload(payload),
                },
            )
        self.capture.record_step(
            AgentStep(
                step=len(self.capture.steps) + 1,
                phase=phase or current_phase,
                tool=tool,
                input_summary=_summary(payload),
                result_summary=result.output,
                state=str(RunState.AGENT_RECOVERING),
                duration_seconds=0.0,
                error=result.error,
                accepted=False,
                recovery_kind="phase_violation",
            )
        )
        return result

    def _scout_budget_exhausted(
        self,
        tool: str,
        payload: dict[str, Any],
    ) -> AciResult | None:
        current_phase, current_sub_phase = self.current_phase_key()
        if current_phase != "scout":
            return None
        budget_name = ""
        limit = 0
        used = 0
        if tool in SCOUT_PROJECT_BUDGET_TOOLS:
            budget_name = "max_candidate_repos_considered"
            limit = self.config.run.budget.scout.max_candidate_repos_considered
            used = self._count_successful_tool_calls(SCOUT_PROJECT_BUDGET_TOOLS)
        elif tool in SCOUT_OPPORTUNITY_BUDGET_TOOLS:
            budget_name = "max_opportunities_considered"
            limit = self.config.run.budget.scout.max_opportunities_considered
            used = self._count_successful_tool_calls(SCOUT_OPPORTUNITY_BUDGET_TOOLS)
        elif tool in SCOUT_DUPLICATE_BUDGET_TOOLS:
            budget_name = "max_duplicate_checks"
            limit = self.config.run.budget.scout.max_duplicate_checks
            used = self._count_successful_tool_calls(SCOUT_DUPLICATE_BUDGET_TOOLS)
        if not budget_name or used < limit:
            return None
        result = AciResult(
            tool=tool,
            success=False,
            output=f"scout_budget_exhausted: {budget_name} reached",
            error=f"scout_budget_exhausted: {budget_name} reached",
            recovery_kind="scout_budget_exhausted",
        )
        self.capture.record_aci_result(result)
        row = {
            "schema_version": "1",
            "tool_call_id": f"{tool}:{len(self.capture.steps) + 1}",
            "tool": tool,
            "phase": current_phase,
            "sub_phase": current_sub_phase,
            "success": False,
            "budget": budget_name,
            "limit": limit,
            "used": used,
            "input_summary": _summary(payload),
            "result_summary": result.output,
            "ts": time.time(),
        }
        artifact = (
            "phase_scout_project_comparison"
            if tool in SCOUT_PROJECT_BUDGET_TOOLS
            else "phase_scout_duplicate_check"
            if tool in SCOUT_DUPLICATE_BUDGET_TOOLS
            else "phase_scout_opportunity_comparison"
        )
        self.capture.record_phase_artifact(artifact, row)
        if self.goals is not None:
            self.goals.record_budget_event(
                event_type="scout_budget_exhausted",
                phase="scout",
                sub_phase=current_sub_phase,
                evidence=f"{budget_name} reached while calling {tool}",
            )
        self.trace.write(
            RunState.AGENT_RECOVERING,
            "agent.scout_budget_exhausted",
            {
                "tool": tool,
                "phase": current_phase,
                "sub_phase": current_sub_phase,
                "budget": budget_name,
                "limit": limit,
                "used": used,
            },
        )
        self.capture.record_step(
            AgentStep(
                step=len(self.capture.steps) + 1,
                phase=current_phase,
                tool=tool,
                input_summary=_summary(payload),
                result_summary=result.output,
                state=str(RunState.AGENT_RECOVERING),
                duration_seconds=0.0,
                error=result.error,
                accepted=False,
                recovery_kind="scout_budget_exhausted",
            )
        )
        return result

    def _count_successful_tool_calls(self, tools: set[str]) -> int:
        return sum(1 for step in self.capture.steps if step.tool in tools and step.error is None)


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
    if tool == "aci_runtime_get_context":
        return "queried runtime context"
    if tool == "aci_goal_update":
        return "updated runtime goal"
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


def _goal_projection_row(base: dict[str, object], result: object) -> dict[str, object]:
    row = dict(base)
    output = str(getattr(result, "output", "") or "")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return row
    if not isinstance(payload, dict):
        return row
    event = payload.get("event")
    if not isinstance(event, dict):
        return row
    for key in (
        "event_id",
        "event_type",
        "goal_id",
        "status",
        "scope",
        "phase",
        "sub_phase",
        "objective",
        "evidence_summary",
        "evidence_refs",
        "next_objective",
    ):
        if key in event:
            row[key] = event[key]
    return row


def _configured_repo_full_name(config: RunConfig) -> str:
    if config.discovery.candidates:
        return config.discovery.candidates[0].full_name
    return config.discovery.query or ""


_VERIFICATION_COMMAND_MARKERS = (
    "pytest",
    "compileall",
    "py_compile",
    "tomllib",
    "ruff",
    "mypy",
    "verify",
    "lint",
    "unit",
    "cargo test",
    "go test",
    "npm test",
    "yarn test",
    "pnpm test",
    "tox",
    "nox",
    "black --check",
    "prettier --check",
    "eslint",
    "flake8",
    "pylint",
    "isort --check",
)

_SETUP_COMMAND_MARKERS = (
    "git clone",
    "pip install",
    "uv pip",
    "uv sync",
    "npm install",
    "npm ci",
    "yarn install",
    "pnpm install",
    "cargo build",
    "cargo fetch",
    "go build",
    "go mod download",
    "apt-get install",
    "apt install",
    "brew install",
    "make install",
    "poetry install",
)

_ACI_VERIFICATION_TOOLS = {"aci_verify"}

_ACI_SETUP_TOOLS = {
    "aci_apply_patch",
    "aci_undo",
    "aci_clean_generated",
    "aci_create",
    "aci_submit_patch",
    "aci_submit_patch_finalize",
    "repo.setup_probe",
}


def _aci_tool_command_type(tool: str) -> CommandType:
    if tool in _ACI_VERIFICATION_TOOLS:
        return "verification"
    if tool in _ACI_SETUP_TOOLS:
        return "setup"
    return "other"


def _infer_command_type(cmd: str) -> CommandType:
    lowered = cmd.lower()
    if any(marker in lowered for marker in _VERIFICATION_COMMAND_MARKERS):
        return "verification"
    if any(marker in lowered for marker in _SETUP_COMMAND_MARKERS):
        return "setup"
    return "other"


def _typed_command(command: CommandResult, command_type: CommandType) -> CommandResult:
    if command.command_type == command_type:
        return command
    return command.model_copy(update={"command_type": command_type})


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


def _attach_maintainer_review(result: AciResult, row: dict[str, object]) -> AciResult:
    summary = _maintainer_review_model_summary(row)
    if not summary:
        return result
    review_notes = result.review_notes
    if review_notes:
        review_notes += "\n\n"
    review_notes += summary
    return result.model_copy(update={"review_notes": review_notes})


def _maintainer_review_model_summary(row: dict[str, object]) -> str:
    status = str(row.get("status") or "")
    severity = str(row.get("severity") or "")
    if status != "completed" or severity == "unavailable":
        return ""
    concerns = _bounded_string_list(row.get("concerns"), 3)
    suggested = _bounded_string_list(row.get("suggested_changes"), 3)
    summary = truncate_for_operator(row.get("summary", ""), 500)
    if not any([status, severity, concerns, suggested, summary]):
        return ""
    lines = [
        "Maintainer pre-review:",
        f"- status: {status or 'unknown'}",
        f"- severity: {severity or 'unknown'}",
    ]
    if summary:
        lines.append(f"- summary: {summary}")
    if concerns:
        lines.append("- concerns:")
        lines.extend(f"  - {item}" for item in concerns)
    if suggested:
        lines.append("- suggested_changes:")
        lines.extend(f"  - {item}" for item in suggested)
    lines.append(
        "Before aci_submit_patch_finalize, either address these points with Review-phase "
        "tools or call aci_dispute_review with evidence_refs."
    )
    return "\n".join(lines)


def _bounded_string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [truncate_for_operator(str(item), 240) for item in value[:limit] if str(item).strip()]


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


def _goal_error(kind: str, message: str, terminal_status: str | None = None) -> AciResult:
    return AciResult(
        tool="aci_goal_update",
        success=False,
        output=message,
        error=message,
        recovery_kind=kind,
        terminal_status=terminal_status,
    )


def _terminal_status_for_goal_event(event: object | None) -> str | None:
    if event is None or getattr(event, "event_type", "") != "goal_abandoned":
        return None
    if getattr(event, "scope", "") == "repo":
        return "repo_abandoned"
    if getattr(event, "scope", "") == "opportunity":
        return "opportunity_abandoned"
    return None


def _annotate_aci_result(tool: str, result: AciResult) -> AciResult:
    if result.success or result.recovery_kind or result.terminal_status:
        return result
    recovery_kind = _classify_recovery(result.error or result.output)
    terminal_status = _terminal_status_for_recovery(recovery_kind)
    return result.model_copy(
        update={"recovery_kind": recovery_kind, "terminal_status": terminal_status}
    )


def _latest_successful_submit(capture: ArtifactCapture) -> AciResult | None:
    for result in reversed(capture.aci_results):
        if result.tool == "aci_submit_patch" and result.success:
            return result
    return None


def _review_round_limit_result(tool: str) -> AciResult:
    message = (
        "review_round_limit: max_review_rounds exhausted; finalize, dispute with evidence, "
        "or abandon/supersede the goal"
    )
    return AciResult(
        tool=tool,
        success=False,
        output=message,
        error=message,
        recovery_kind="review_round_limit",
    )


def _review_resubmit_rounds_used(capture: ArtifactCapture) -> int:
    return max(0, sum(1 for item in capture.phase_review_maintainer_rows if item) - 1)


def _next_maintainer_review_round(capture: ArtifactCapture) -> int:
    return sum(1 for item in capture.phase_review_maintainer_rows if item) + 1


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
