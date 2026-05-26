from __future__ import annotations

from typing import Protocol

from contribarena.models import AciResult, CommandResult


class ContributorTools(Protocol):
    def repo_search(self, query: str = "", filters: object | None = None) -> object: ...

    def repo_check_eligibility(self, candidate: object) -> object: ...

    def repo_get_metadata(self, candidate: object) -> object: ...

    def repo_get_readme(self, candidate: object, max_chars: int = 6000) -> object: ...

    def repo_get_issues(self, candidate: object, filters: object | None = None) -> object: ...

    def repo_get_open_prs(self, candidate: object, limit: int = 30) -> object: ...

    def repo_get_recent_merged_prs(self, candidate: object, limit: int = 30) -> object: ...

    def repo_search_prs_by_title(
        self, candidate: object, query: str, limit: int = 20
    ) -> object: ...

    def repo_get_issue_linkage(self, candidate: object, issue_number: int) -> object: ...

    def repo_get_pr_review_history(self, candidate: object, limit: int = 20) -> object: ...

    def repo_setup_probe(
        self,
        candidate: object,
        max_probe_seconds: int | None = None,
        install_dependencies: bool = False,
    ) -> object: ...

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
        scope: str = "",
        evidence_refs_json: str = "[]",
        next_objective: str = "",
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

    def aci_dispute_review(
        self,
        concern_id: str,
        rebuttal_text: str,
        evidence_refs_json: str = "[]",
    ) -> AciResult: ...

    def aci_submit_patch_finalize(self, path: str = "repo") -> AciResult: ...

    def github_prepare_fork(self, owner: str, repo: str) -> AciResult: ...

    def github_prepare_branch(
        self,
        owner: str,
        repo: str,
        base: str,
        branch: str,
        path: str = "repo",
    ) -> AciResult: ...

    def github_commit(
        self,
        title: str,
        body: str = "",
        path: str = "repo",
    ) -> AciResult: ...

    def github_push_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
        path: str = "repo",
    ) -> AciResult: ...

    def github_open_pr(
        self,
        owner: str,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> AciResult: ...

    def github_observe_pr(self, owner: str, repo: str, number: int) -> AciResult: ...
