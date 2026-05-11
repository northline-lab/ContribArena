from __future__ import annotations

from typing import Protocol

from contribarena.models import AciResult, CommandResult, PatchResult


class ContributorTools(Protocol):
    def repo_search(self, query: str = "", filters: object | None = None) -> object:
        ...

    def repo_check_eligibility(self, candidate: object) -> object:
        ...

    def repo_get_metadata(self, candidate: object) -> object:
        ...

    def repo_get_issues(self, candidate: object, filters: object | None = None) -> object:
        ...

    def workspace_run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
        ...

    def workspace_apply_patch(self, diff: str) -> PatchResult:
        ...

    def aci_view(self, path: str, start_line: int = 1, max_lines: int = 200) -> AciResult:
        ...

    def aci_search(self, pattern: str, path: str = ".", max_results: int = 80) -> AciResult:
        ...

    def aci_find_files(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 80,
    ) -> AciResult:
        ...

    def aci_replace(self, path: str, old_str: str, new_str: str) -> AciResult:
        ...

    def aci_insert(self, path: str, insert_after_line: int, text: str) -> AciResult:
        ...

    def aci_create(self, path: str, content: str) -> AciResult:
        ...

    def aci_undo(self) -> AciResult:
        ...

    def aci_verify(
        self,
        command: str,
        path: str = "repo",
        timeout_seconds: int | None = None,
    ) -> AciResult:
        ...

    def aci_suggest_verification(self, path: str = "repo") -> AciResult:
        ...

    def aci_recover_invalid_action(
        self,
        recovery_kind: str,
        message: str,
        attempted_tool: str = "",
    ) -> AciResult:
        ...

    def aci_submit_patch(
        self,
        path: str = "repo",
        no_command_verification_rationale: str = "",
    ) -> AciResult:
        ...
