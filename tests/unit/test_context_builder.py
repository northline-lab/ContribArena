from __future__ import annotations

import unittest

from contribarena.config.schema import (
    DiscoveryConfig,
    GovernanceConfig,
    OwnedRepositoryPolicy,
    RepoCandidate,
    RepoSearchFilters,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.context import ContextBuilder


def _candidate(
    owner: str = "qWaitCrypto",
    repo: str = "ContribArena",
    notes: str | None = "owned calibration target",
) -> RepoCandidate:
    return RepoCandidate(
        owner=owner,
        repo=repo,
        url=f"https://github.com/{owner}/{repo}",
        branch="main",
        notes=notes,
    )


def _config(
    *,
    mode: str = "shadow",
    candidates: list[RepoCandidate] | None = None,
    query: str = "agent framework",
    filters: RepoSearchFilters | None = None,
) -> RunConfig:
    candidates = list(candidates or [])
    governance = GovernanceConfig()
    if mode == "owned_live" and candidates:
        governance.owned_repositories = [
            OwnedRepositoryPolicy(owner=candidates[0].owner, repo=candidates[0].repo)
        ]
    return RunConfig(
        run=RunSection(mode=mode),
        discovery=DiscoveryConfig(
            candidates=candidates,
            query=query,
            filters=filters or RepoSearchFilters(),
        ),
        workspace=WorkspaceConfig(),
        governance=governance,
    )


class ContextBuilderTest(unittest.TestCase):
    def test_owned_live_prompt_includes_owned_live_boundary_and_candidate_notes(self) -> None:
        prompt = ContextBuilder().build_system_prompt(
            _config(mode="owned_live", candidates=[_candidate()])
        )

        self.assertIn("The run is owned-live mode", prompt)
        self.assertIn("live GitHub writes are executed only by the harness", prompt)
        self.assertIn(
            "- qWaitCrypto/ContribArena: https://github.com/qWaitCrypto/ContribArena (owned calibration target)",
            prompt,
        )
        self.assertIn("Discovery query: agent framework", prompt)

    def test_external_live_prompt_includes_fork_only_boundary(self) -> None:
        prompt = ContextBuilder().build_system_prompt(
            _config(mode="external_live", candidates=[_candidate()])
        )

        self.assertIn("The run is external-live mode", prompt)
        self.assertIn("External PR submission is fork-only.", prompt)
        self.assertIn("governance gates", prompt)

    def test_shadow_prompt_includes_no_pull_request_boundary(self) -> None:
        prompt = ContextBuilder().build_system_prompt(
            _config(mode="shadow", candidates=[_candidate()])
        )

        self.assertIn("The run is shadow mode", prompt)
        self.assertIn("do not open pull requests or write comments", prompt)

    def test_candidate_without_notes_uses_no_notes_placeholder(self) -> None:
        prompt = ContextBuilder().build_system_prompt(
            _config(candidates=[_candidate(owner="octo", repo="demo", notes=None)])
        )

        self.assertIn("- octo/demo: https://github.com/octo/demo (no notes)", prompt)

    def test_empty_candidate_list_uses_repo_search_fallback_and_serializes_filters(self) -> None:
        prompt = ContextBuilder().build_system_prompt(
            _config(
                candidates=[],
                query="",
                filters=RepoSearchFilters(language="Python", stars_min=0),
            )
        )

        self.assertIn("Discovery query: n/a", prompt)
        self.assertIn("Discovery filters: {'language': 'Python', 'stars_min': 0}", prompt)
        self.assertIn(
            "- no fixed candidates; use repo_search with configured query/filters",
            prompt,
        )

    def test_prompt_includes_runtime_and_guidance_contracts(self) -> None:
        prompt = ContextBuilder().build_system_prompt(
            _config(candidates=[_candidate()])
        )

        self.assertIn("Call aci_runtime_get_context(scope='run') early", prompt)
        self.assertIn("Move phases only through aci_goal_update", prompt)
        self.assertIn("If guidance is available, read the returned path", prompt)
        self.assertIn("Before editing, check repository-local guidance", prompt)
        self.assertIn("Choose a low-risk task and return a structured completion result.", prompt)


if __name__ == "__main__":
    unittest.main()
