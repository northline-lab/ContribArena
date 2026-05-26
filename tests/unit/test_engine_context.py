from __future__ import annotations

import unittest

from contribarena.config.schema import (
    DiscoveryConfig,
    RepoCandidate,
    RepoSearchFilters,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.context import ContextBuilder


def _shadow_config() -> RunConfig:
    return RunConfig(
        run=RunSection(mode="shadow"),
        discovery=DiscoveryConfig(
            candidates=[
                RepoCandidate(
                    owner="example",
                    repo="repo",
                    url="https://github.com/example/repo",
                )
            ],
        ),
        workspace=WorkspaceConfig(),
    )


class ContextBuilderTest(unittest.TestCase):
    def test_shadow_mode_boundary(self) -> None:
        config = _shadow_config()
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("shadow mode", prompt)
        self.assertIn("do not open pull requests", prompt)

    def test_owned_live_mode_boundary(self) -> None:
        config = _shadow_config()
        config.run.mode = "owned_live"
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("owned-live mode", prompt)
        self.assertIn("PR-ready patch", prompt)

    def test_external_live_mode_boundary(self) -> None:
        config = _shadow_config()
        config.run.mode = "external_live"
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("external-live mode", prompt)
        self.assertIn("fork-only", prompt)

    def test_candidate_listing_format(self) -> None:
        config = _shadow_config()
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("example/repo", prompt)
        self.assertIn("https://github.com/example/repo", prompt)

    def test_candidate_notes_rendered(self) -> None:
        config = RunConfig(
            run=RunSection(mode="shadow"),
            discovery=DiscoveryConfig(
                candidates=[
                    RepoCandidate(
                        owner="owner",
                        repo="project",
                        url="https://github.com/owner/project",
                        notes="A well-documented project.",
                    )
                ],
            ),
            workspace=WorkspaceConfig(),
        )
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("A well-documented project.", prompt)

    def test_candidate_no_notes_shows_placeholder(self) -> None:
        config = _shadow_config()
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("no notes", prompt)

    def test_query_without_candidates_shows_fallback(self) -> None:
        config = RunConfig(
            run=RunSection(mode="shadow"),
            discovery=DiscoveryConfig(candidates=[], query="test query", filters=RepoSearchFilters()),
            workspace=WorkspaceConfig(),
        )
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("no fixed candidates", prompt)
        self.assertIn("use repo_search", prompt)

    def test_discovery_query_in_prompt(self) -> None:
        config = _shadow_config()
        config.discovery.query = "agent framework"
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("Discovery query: agent framework", prompt)

    def test_empty_discovery_query_shows_na(self) -> None:
        config = _shadow_config()
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("Discovery query: n/a", prompt)

    def test_discovery_filters_rendered(self) -> None:
        config = _shadow_config()
        config.discovery.filters = RepoSearchFilters(language="Python")
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("Python", prompt)

    def test_multiple_candidates_all_listed(self) -> None:
        config = RunConfig(
            run=RunSection(mode="shadow"),
            discovery=DiscoveryConfig(
                candidates=[
                    RepoCandidate(
                        owner="a", repo="r1", url="https://github.com/a/r1"
                    ),
                    RepoCandidate(
                        owner="b", repo="r2", url="https://github.com/b/r2"
                    ),
                ],
            ),
            workspace=WorkspaceConfig(),
        )
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertIn("a/r1", prompt)
        self.assertIn("b/r2", prompt)

    def test_prompt_always_starts_with_agent_intro(self) -> None:
        config = _shadow_config()
        prompt = ContextBuilder().build_system_prompt(config)
        self.assertTrue(
            prompt.startswith("You are an autonomous open-source contribution agent")
        )
