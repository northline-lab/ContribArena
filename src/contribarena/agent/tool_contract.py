from __future__ import annotations

from typing import Protocol

from contribarena.models import AciResult, CommandResult


class ContributorTools(Protocol):
    def repo_search(self, query: str = "", filters: object | None = None) -> object: ...

    def repo_check_eligibility(self, candidate: object) -> object: ...

    def repo_get_metadata(self, candidate: object) -> object: ...

    def repo_get_issues(self, candidate: object, filters: object | None = None) -> object: ...

    def workspace_run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult: ...

    def aci_view(self, path: str, start_line: int = 1, max_lines: int = 200) -> AciResult: ...

    def aci_search(self, pattern: str, path: str = ".", max_results: int = 80) -> AciResult: ...

    def aci_find_files(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 80,
    ) -> AciResult: ...

    def aci_apply_patch(
        self,
        operations: list[dict[str, object]],
        rationale: str = "",
        expected_files: list[str] | None = None,
    ) -> AciResult: ...

    def aci_undo(self) -> AciResult: ...

    def aci_verify(
        self,
        command: str,
        path: str = "repo",
        timeout_seconds: int | None = None,
    ) -> AciResult: ...

    def aci_suggest_verification(self, path: str = "repo") -> AciResult: ...

    def aci_clean_generated(self, path: str = "repo") -> AciResult: ...

    def operator_report_progress(
        self,
        phase: str,
        status: str,
        summary: str,
        evidence_refs: str = "",
    ) -> AciResult: ...

    def aci_runtime_get_context(self, scope: str = "run") -> AciResult: ...

    def aci_memory_get_context(self, scope: str = "run") -> AciResult: ...

    def aci_memory_search(
        self,
        query: str,
        intent: str = "unknown",
        max_results: int = 5,
    ) -> AciResult: ...

    def aci_memory_note(
        self,
        scope: str,
        text: str,
        tags_json: str = "[]",
        confidence: str = "medium",
    ) -> AciResult: ...

    def aci_memory_plan_update(
        self,
        action: str,
        item_id: str = "",
        text: str = "",
        status: str = "",
    ) -> AciResult: ...

    def aci_goal_update(
        self,
        objective: str = "",
        status: str = "active",
        evidence: str = "",
    ) -> AciResult: ...

    def aci_recover_invalid_action(
        self,
        recovery_kind: str,
        message: str,
        attempted_tool: str = "",
    ) -> AciResult: ...

    def aci_submit_patch(
        self,
        path: str = "repo",
        no_command_verification_rationale: str = "",
    ) -> AciResult: ...
