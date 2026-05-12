from __future__ import annotations

import unittest
from unittest.mock import patch

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    RepoCandidate,
    RepoSearchFilters,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.models import RepoMetadata
from contribarena.tools.github_client import GitHubResponse
from contribarena.tools.github_pr import GitHubPullRequestClient
from contribarena.tools.repo_eligibility import repo_check_eligibility
from contribarena.tools.repo_issues import repo_get_issues
from contribarena.tools.repo_metadata import repo_get_metadata
from contribarena.tools.repo_search import repo_search


class GithubToolsTest(unittest.TestCase):
    def test_search_only_discovery_config_is_valid(self) -> None:
        config = _config(query="agent framework")

        self.assertEqual([], config.discovery.candidates)
        self.assertEqual("agent framework", config.discovery.query)

    def test_repo_search_uses_gh_results(self) -> None:
        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                self.args = args
                return GitHubResponse(
                    ok=True,
                    source="gh",
                    data=[
                        {
                            "fullName": "owner/project",
                            "description": "A project",
                            "stargazersCount": 12,
                            "language": "Python",
                            "pushedAt": "2026-05-01T00:00:00Z",
                        }
                    ],
                )

            def rest_json(self, *args: object, **kwargs: object) -> GitHubResponse:
                raise AssertionError("REST fallback should not be used")

        with patch("contribarena.tools.repo_search.GitHubClient", FakeClient):
            results = repo_search(_config(query="agent", language="Python"))

        self.assertEqual("owner/project", results[0].full_name)
        self.assertIn("language=Python", results[0].notes or "")

    def test_repo_search_falls_back_to_rest(self) -> None:
        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                return GitHubResponse(ok=False, source="gh", error="missing gh")

            def rest_json(self, *args: object, **kwargs: object) -> GitHubResponse:
                return GitHubResponse(
                    ok=True,
                    source="httpx",
                    data={
                        "items": [
                            {
                                "full_name": "owner/rest-project",
                                "html_url": "https://github.com/owner/rest-project",
                                "description": "REST project",
                                "stargazers_count": 5,
                                "language": "Go",
                                "pushed_at": "2026-05-01T00:00:00Z",
                            }
                        ]
                    },
                )

        with patch("contribarena.tools.repo_search.GitHubClient", FakeClient):
            results = repo_search(_config(query="agent"))

        self.assertEqual("owner/rest-project", results[0].full_name)

    def test_repo_metadata_normalizes_gh_payload(self) -> None:
        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                return GitHubResponse(
                    ok=True,
                    source="gh",
                    data={
                        "name": "project",
                        "description": "A project",
                        "stargazerCount": 10,
                        "forkCount": 2,
                        "primaryLanguage": {"name": "Python"},
                        "pushedAt": "2026-05-01T00:00:00Z",
                        "createdAt": "2025-01-01T00:00:00Z",
                        "openIssuesCount": 3,
                        "defaultBranchRef": {"name": "main"},
                        "url": "https://github.com/owner/project",
                    },
                )

            def rest_json(self, *args: object, **kwargs: object) -> GitHubResponse:
                raise AssertionError("REST fallback should not be used")

        with patch("contribarena.tools.repo_metadata.GitHubClient", FakeClient):
            metadata = repo_get_metadata(_candidate())

        self.assertEqual("owner/project", metadata.full_name)
        self.assertEqual("Python", metadata.language)
        self.assertEqual(10, metadata.stars)

    def test_repo_issues_normalizes_gh_payload(self) -> None:
        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                return GitHubResponse(
                    ok=True,
                    source="gh",
                    data=[
                        {
                            "number": 7,
                            "title": "Improve docs",
                            "body": "Body",
                            "url": "https://github.com/owner/project/issues/7",
                            "labels": [{"name": "good first issue"}],
                            "assignees": [],
                            "createdAt": "2026-05-01T00:00:00Z",
                            "updatedAt": "2026-05-02T00:00:00Z",
                        }
                    ],
                )

            def rest_json(self, *args: object, **kwargs: object) -> GitHubResponse:
                raise AssertionError("REST fallback should not be used")

        with patch("contribarena.tools.repo_issues.GitHubClient", FakeClient):
            issues = repo_get_issues(_candidate(), filters={"no_assignee": True})

        self.assertEqual(7, issues[0].number)
        self.assertEqual(["good first issue"], issues[0].labels)

    def test_repo_eligibility_runs_rule_checks(self) -> None:
        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                return GitHubResponse(
                    ok=True,
                    source="gh",
                    data=[
                        {
                            "author": {"login": "external-user"},
                            "mergedAt": "2026-04-01T00:00:00Z",
                        }
                    ],
                )

            def rest_json(
                self, method: str, path: str, params: dict | None = None
            ) -> GitHubResponse:
                return GitHubResponse(ok=True, source="httpx", data={"Python": 100_000})

            def rest_text(self, path: str) -> GitHubResponse:
                return GitHubResponse(
                    ok=True,
                    source="httpx",
                    data="README\n\nContributions are welcome. Please open focused pull requests.",
                )

        metadata = RepoMetadata(
            owner="owner",
            repo="project",
            full_name="owner/project",
            url="https://github.com/owner/project",
            last_push="2026-05-01T00:00:00Z",
        )
        with (
            patch("contribarena.tools.repo_eligibility.GitHubClient", FakeClient),
            patch("contribarena.tools.repo_eligibility.repo_get_metadata", return_value=metadata),
        ):
            result = repo_check_eligibility(_candidate())

        self.assertTrue(result.eligible)
        self.assertIn("activity", result.checks_performed)
        self.assertIn("code_size", result.checks_performed)

    def test_github_pr_client_posts_pull_request_payload(self) -> None:
        class FakeClient:
            def rest_json(
                self,
                method: str,
                path: str,
                params: dict | None = None,
                json_body: dict | None = None,
                token_env: str | None = None,
            ) -> GitHubResponse:
                self.method = method
                self.path = path
                self.json_body = json_body
                self.token_env = token_env
                return GitHubResponse(
                    ok=True,
                    source="fake",
                    data={
                        "number": 42,
                        "html_url": "https://github.com/owner/project/pull/42",
                        "head": {"sha": "abc123"},
                    },
                )

        fake = FakeClient()
        client = GitHubPullRequestClient(client=fake, token_env="BOT_TOKEN")  # type: ignore[arg-type]

        result = client.open_pr(
            owner="owner",
            repo="project",
            title="Improve docs",
            body="Body",
            head="contribarena/improve-docs",
            base="main",
        )

        self.assertTrue(result.ok)
        self.assertEqual(42, result.number)
        self.assertEqual("abc123", result.head_sha)
        self.assertEqual("POST", fake.method)
        self.assertEqual("/repos/owner/project/pulls", fake.path)
        self.assertEqual("BOT_TOKEN", fake.token_env)
        self.assertEqual("Improve docs", fake.json_body["title"])
        self.assertEqual("contribarena/improve-docs", fake.json_body["head"])
        self.assertEqual("main", fake.json_body["base"])

    def test_github_pr_client_ensures_existing_fork(self) -> None:
        class FakeClient:
            def rest_json(
                self,
                method: str,
                path: str,
                params: dict | None = None,
                json_body: dict | None = None,
                token_env: str | None = None,
            ) -> GitHubResponse:
                self.method = method
                self.path = path
                self.token_env = token_env
                return GitHubResponse(
                    ok=True,
                    source="fake",
                    data={
                        "name": "project",
                        "full_name": "bot/project",
                        "html_url": "https://github.com/bot/project",
                        "owner": {"login": "bot"},
                    },
                )

        fake = FakeClient()
        client = GitHubPullRequestClient(client=fake, token_env="BOT_TOKEN")  # type: ignore[arg-type]

        result = client.ensure_fork(owner="owner", repo="project", fork_owner="bot")

        self.assertTrue(result.ok)
        self.assertFalse(result.created)
        self.assertEqual("bot/project", result.full_name)
        self.assertEqual("GET", fake.method)
        self.assertEqual("/repos/bot/project", fake.path)
        self.assertEqual("BOT_TOKEN", fake.token_env)

    def test_github_pr_client_creates_missing_fork(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def rest_json(
                self,
                method: str,
                path: str,
                params: dict | None = None,
                json_body: dict | None = None,
                token_env: str | None = None,
            ) -> GitHubResponse:
                self.calls.append((method, path))
                if method == "GET":
                    return GitHubResponse(ok=False, error="repo not found", source="fake")
                return GitHubResponse(
                    ok=True,
                    source="fake",
                    data={
                        "name": "project",
                        "full_name": "bot/project",
                        "html_url": "https://github.com/bot/project",
                        "owner": {"login": "bot"},
                    },
                )

        fake = FakeClient()
        client = GitHubPullRequestClient(client=fake, token_env="BOT_TOKEN")  # type: ignore[arg-type]

        result = client.ensure_fork(owner="owner", repo="project", fork_owner="bot")

        self.assertTrue(result.ok)
        self.assertTrue(result.created)
        self.assertEqual("bot", result.owner)
        self.assertEqual(
            [("GET", "/repos/bot/project"), ("POST", "/repos/owner/project/forks")],
            fake.calls,
        )

    def test_github_pr_client_does_not_create_fork_after_auth_lookup_error(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def rest_json(
                self,
                method: str,
                path: str,
                params: dict | None = None,
                json_body: dict | None = None,
                token_env: str | None = None,
            ) -> GitHubResponse:
                self.calls.append((method, path))
                return GitHubResponse(ok=False, error="authentication missing", source="fake")

        fake = FakeClient()
        client = GitHubPullRequestClient(client=fake, token_env="BOT_TOKEN")  # type: ignore[arg-type]

        result = client.ensure_fork(owner="owner", repo="project", fork_owner="bot")

        self.assertFalse(result.ok)
        self.assertEqual("authentication missing", result.error)
        self.assertEqual([("GET", "/repos/bot/project")], fake.calls)

    def test_github_pr_client_normalizes_check_runs(self) -> None:
        class FakeClient:
            def rest_json(
                self,
                method: str,
                path: str,
                params: dict | None = None,
                json_body: dict | None = None,
                token_env: str | None = None,
            ) -> GitHubResponse:
                self.method = method
                self.path = path
                self.token_env = token_env
                return GitHubResponse(
                    ok=True,
                    source="fake",
                    data={
                        "check_runs": [
                            {
                                "name": "unit",
                                "status": "completed",
                                "conclusion": "success",
                                "details_url": "https://example.test/check",
                            }
                        ]
                    },
                )

        fake = FakeClient()
        client = GitHubPullRequestClient(client=fake, token_env="BOT_TOKEN")  # type: ignore[arg-type]

        status = client.get_check_runs(owner="owner", repo="project", ref="abc123")

        self.assertEqual("success", status.status)
        self.assertEqual("github", status.source)
        self.assertEqual("unit", status.checks[0].name)
        self.assertEqual("/repos/owner/project/commits/abc123/check-runs", fake.path)
        self.assertEqual("BOT_TOKEN", fake.token_env)


def _candidate() -> RepoCandidate:
    return RepoCandidate(
        owner="owner",
        repo="project",
        url="https://github.com/owner/project",
        branch="main",
    )


def _config(query: str = "", language: str | None = None) -> RunConfig:
    return RunConfig(
        run=RunSection(),
        discovery=DiscoveryConfig(
            query=query,
            filters=RepoSearchFilters(language=language) if language else RepoSearchFilters(),
        ),
        workspace=WorkspaceConfig(),
        artifacts=ArtifactConfig(),
    )


if __name__ == "__main__":
    unittest.main()
