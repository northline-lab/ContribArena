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


def _shadow_config(
    *,
    candidates: list[RepoCandidate] | None = None,
    query: str = "",
    filters: RepoSearchFilters | None = None,
) -> RunConfig:
    if candidates is None and not query and filters is None:
        candidates = [
            RepoCandidate(
                owner="example",
                repo="repo",
                url="https://github.com/example/repo",
            )
        ]
    return RunConfig(
        run=RunSection(mode="shadow"),
        discovery=DiscoveryConfig(
            candidates=candidates or [],
            query=query,
            filters=filters or RepoSearchFilters(),
        ),
        workspace=WorkspaceConfig(),
    )


class ContextBuilderModeBoundaryTest(unittest.TestCase):
    def test_owned_live_mode_boundary_text_is_included(self) -> None:
        config = RunConfig(
            run=RunSection(mode="owned_live"),
            discovery=DiscoveryConfig(
                candidates=[
                    RepoCandidate(
                        owner="example",
                        repo="repo",
                        url="https://github.com/example/repo",
                    )
                ]
            ),
            workspace=WorkspaceConfig(),
            governance=GovernanceConfig(
                owned_repositories=[
                    OwnedRepositoryPolicy(
                        owner="example",
                        repo="repo",
                        default_branch="main",
                    )
                ]
            ),
        )

        prompt = ContextBuilder().build_system_prompt(config)

        self.assertIn("owned-live mode", prompt)
        self.assertIn(
            "live GitHub writes are executed only by the harness",
            prompt,
        )
        self.assertNotIn("external-live mode", prompt)
        self.assertNotIn("shadow mode", prompt)

    def test_external_live_mode_boundary_text_is_included(self) -> None:
        config = RunConfig(
            run=RunSection(mode="external_live"),
            discovery=DiscoveryConfig(query="agent framework"),
            workspace=WorkspaceConfig(),
        )

        prompt = ContextBuilder().build_system_prompt(config)

        self.assertIn("external-live mode", prompt)
        self.assertIn("fork-only", prompt)
        self.assertNotIn("owned-live mode", prompt)
        self.assertNotIn("shadow mode", prompt)

    def test_shadow_mode_boundary_text_is_default(self) -> None:
        config = _shadow_config()

        prompt = ContextBuilder().build_system_prompt(config)

        self.assertIn(
            "shadow mode: do not open pull requests or write comments.",
            prompt,
        )
        self.assertNotIn("owned-live mode", prompt)
        self.assertNotIn("external-live mode", prompt)

    def test_dry_run_mode_uses_shadow_boundary_branch(self) -> None:
        config = _shadow_config()
        # dry_run is also accepted by the schema and falls through the
        # else branch of build_system_prompt alongside shadow.
        config = config.model_copy(
            update={"run": config.run.model_copy(update={"mode": "dry_run"})}
        )

        prompt = ContextBuilder().build_system_prompt(config)

        self.assertIn(
            "shadow mode: do not open pull requests or write comments.",
            prompt,
        )


class ContextBuilderCandidatesTest(unittest.TestCase):
    def test_single_candidate_renders_full_name_url_and_notes(self) -> None:
        candidate = RepoCandidate(
            owner="acme",
            repo="widgets",
            url="https://github.com/acme/widgets",
            notes="Calibration target.",
        )
        config = _shadow_config(candidates=[candidate])

        prompt = ContextBuilder().build_system_prompt(config)

        expected_line = f"- acme/widgets: {candidate.url} (Calibration target.)"
        self.assertIn(expected_line, prompt)

    def test_candidate_without_notes_falls_back_to_no_notes_placeholder(self) -> None:
        candidate = RepoCandidate(
            owner="acme",
            repo="widgets",
            url="https://github.com/acme/widgets",
        )
        config = _shadow_config(candidates=[candidate])

        prompt = ContextBuilder().build_system_prompt(config)

        expected_line = f"- acme/widgets: {candidate.url} (no notes)"
        self.assertIn(expected_line, prompt)

    def test_multiple_candidates_are_listed_in_order(self) -> None:
        config = _shadow_config(
            candidates=[
                RepoCandidate(
                    owner="acme",
                    repo="first",
                    url="https://github.com/acme/first",
                    notes="one",
                ),
                RepoCandidate(
                    owner="acme",
                    repo="second",
                    url="https://github.com/acme/second",
                    notes="two",
                ),
            ]
        )

        prompt = ContextBuilder().build_system_prompt(config)

        first_pos = prompt.find("- acme/first:")
        second_pos = prompt.find("- acme/second:")
        self.assertNotEqual(-1, first_pos)
        self.assertNotEqual(-1, second_pos)
        self.assertLess(first_pos, second_pos)

    def test_no_candidates_uses_search_fallback_line(self) -> None:
        config = _shadow_config(
            candidates=[],
            query="agent framework",
        )

        prompt = ContextBuilder().build_system_prompt(config)

        self.assertIn(
            "- no fixed candidates; use repo_search with configured query/filters",
            prompt,
        )


class ContextBuilderDiscoveryRenderingTest(unittest.TestCase):
    def test_query_is_rendered_verbatim(self) -> None:
        config = _shadow_config(
            candidates=[],
            query="agent framework",
        )

        prompt = ContextBuilder().build_system_prompt(config)

        self.assertIn("Discovery query: agent framework", prompt)

    def test_empty_query_renders_n_a_marker(self) -> None:
        config = _shadow_config()

        prompt = ContextBuilder().build_system_prompt(config)

        self.assertIn("Discovery query: n/a", prompt)

    def test_filters_render_only_set_fields(self) -> None:
        config = _shadow_config(
            candidates=[],
            query="agent framework",
            filters=RepoSearchFilters(language="Python", stars_min=10),
        )

        prompt = ContextBuilder().build_system_prompt(config)

        self.assertIn("'language': 'Python'", prompt)
        self.assertIn("'stars_min': 10", prompt)
        # exclude_none means unset fields must not appear in the dict literal.
        self.assertNotIn("'pushed_after'", prompt)
        self.assertNotIn("'topic'", prompt)

    def test_empty_filters_render_as_empty_dict_literal(self) -> None:
        config = _shadow_config()

        prompt = ContextBuilder().build_system_prompt(config)

        self.assertIn("Discovery filters: {}", prompt)


if __name__ == "__main__":
    unittest.main()
