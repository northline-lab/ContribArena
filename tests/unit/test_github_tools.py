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
    SeasonConfig,
    SeasonDiscoveryProfileConfig,
    SeasonParticipantConfig,
    WorkspaceConfig,
)
from contribarena.models import RepoMetadata
from contribarena.tools.github_client import GitHubResponse
from contribarena.tools.github_pr import GitHubPullRequestClient
from contribarena.tools.repo_eligibility import repo_check_eligibility
from contribarena.tools.repo_issues import repo_get_issues
from contribarena.tools.repo_metadata import repo_get_metadata
from contribarena.tools.repo_readme import repo_get_readme
from contribarena.tools.repo_prs import (
    repo_get_issue_linkage,
    repo_get_open_prs,
    repo_get_recent_merged_prs,
    repo_search_prs_by_title,
)
from contribarena.tools.repo_search import repo_search, repo_search_with_log


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

    def test_repo_search_applies_owned_profile_and_records_log_row(self) -> None:
        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                raise AssertionError("owned discovery must not query GitHub")

            def rest_json(self, *args: object, **kwargs: object) -> GitHubResponse:
                raise AssertionError("REST fallback should not be used")

        config = _config(language="Python")
        config.run.season_id = "season_0"
        config.run.participant_id = "season_0:qwen36plus"
        config.season = SeasonConfig(
            id="season_0",
            status="active",
            participants=[SeasonParticipantConfig(model="compatible/qwen36plus")],
            discovery_profile=SeasonDiscoveryProfileConfig(
                scope="owned",
                seed_queries=["agent framework"],
                language_filter=["Python"],
                min_stars=50,
                allowlist=["owner/allowed"],
                denylist=["owner/denied"],
            ),
        )

        with patch("contribarena.tools.repo_search.GitHubClient", FakeClient):
            result = repo_search_with_log(config)

        self.assertEqual(["owner/allowed"], [candidate.full_name for candidate in result.candidates])
        self.assertEqual("season_0", result.log_row["season_id"])
        self.assertEqual("season_0:qwen36plus", result.log_row["participant_id"])
        self.assertEqual("", result.log_row["query"])
        self.assertEqual({"language": "Python", "stars_min": 50}, result.log_row["filters_resolved"])
        self.assertIn("repo:owner/allowed", str(result.log_row["github_query_string"]))
        self.assertIn("-repo:owner/denied", str(result.log_row["github_query_string"]))
        self.assertEqual(1, result.log_row["total_hits"])
        self.assertEqual(1, result.log_row["returned_count"])

    def test_owned_repo_search_rejects_query_outside_allowlist(self) -> None:
        config = _config(query="placeholder")
        config.season = SeasonConfig(
            id="season_0",
            status="active",
            participants=[SeasonParticipantConfig(model="local-stub")],
            discovery_profile=SeasonDiscoveryProfileConfig(
                scope="owned",
                allowlist=["owner/allowed"],
            ),
        )

        result = repo_search_with_log(config, query="owner/other")

        self.assertEqual([], result.candidates)
        self.assertEqual("denied_by_season_policy", str(result.log_row["error"]).split(":", 1)[0])
        self.assertEqual(0, result.log_row["returned_count"])

    def test_external_repo_search_pushes_denylist_into_query(self) -> None:
        seen: dict[str, object] = {}

        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                seen["query"] = args[-1]
                return GitHubResponse(ok=True, source="gh", data=[])

            def rest_json(self, *args: object, **kwargs: object) -> GitHubResponse:
                raise AssertionError("REST fallback should not be used")

        config = _config(query="agent")
        config.season = SeasonConfig(
            id="season_1",
            status="active",
            participants=[SeasonParticipantConfig(model="local-stub")],
            discovery_profile=SeasonDiscoveryProfileConfig(
                scope="external",
                denylist=["owner/denied"],
            ),
        )

        with patch("contribarena.tools.repo_search.GitHubClient", FakeClient):
            result = repo_search_with_log(config)

        self.assertIn("-repo:owner/denied", str(seen["query"]))
        self.assertIn("-repo:owner/denied", str(result.log_row["github_query_string"]))

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
                        "openIssues": 3,
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
        self.assertEqual(3, metadata.open_issues)

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

    def test_repo_issues_rest_include_prs_is_opt_in(self) -> None:
        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                return GitHubResponse(ok=False, source="gh", error="missing gh")

            def rest_json(self, *args: object, **kwargs: object) -> GitHubResponse:
                return GitHubResponse(
                    ok=True,
                    source="httpx",
                    data=[
                        {"number": 7, "title": "Issue", "html_url": "https://example/7"},
                        {
                            "number": 8,
                            "title": "PR",
                            "html_url": "https://example/8",
                            "pull_request": {},
                        },
                    ],
                )

        with patch("contribarena.tools.repo_issues.GitHubClient", FakeClient):
            issues_only = repo_get_issues(_candidate())
            with_prs = repo_get_issues(_candidate(), include_prs=True)

        self.assertEqual([7], [item.number for item in issues_only])
        self.assertEqual([7, 8], [item.number for item in with_prs])

    def test_repo_readme_uses_rest_text(self) -> None:
        class FakeClient:
            def rest_text(self, path: str) -> GitHubResponse:
                self.path = path
                return GitHubResponse(ok=True, source="httpx", data="# Project\n")

        with patch("contribarena.tools.repo_readme.GitHubClient", FakeClient):
            readme = repo_get_readme(_candidate())

        self.assertTrue(readme.success)
        self.assertEqual("owner/project", readme.full_name)
        self.assertIn("# Project", readme.content)

    def test_repo_pr_tools_normalize_pr_payloads(self) -> None:
        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                state = args[args.index("--state") + 1]
                return GitHubResponse(
                    ok=True,
                    source="gh",
                    data=[
                        {
                            "number": 11,
                            "title": f"{state} fix parser edge case",
                            "url": "https://github.com/owner/project/pull/11",
                            "state": state,
                            "author": {"login": "contrib"},
                            "body": "Fixes #7",
                            "labels": [{"name": "bug"}],
                            "createdAt": "2026-05-01T00:00:00Z",
                            "updatedAt": "2026-05-02T00:00:00Z",
                            "mergedAt": "2026-05-03T00:00:00Z" if state == "merged" else None,
                            "isDraft": False,
                            "closingIssuesReferences": [{"number": 7}],
                        }
                    ],
                )

            def rest_json(self, *args: object, **kwargs: object) -> GitHubResponse:
                raise AssertionError("REST fallback should not be used")

        with patch("contribarena.tools.repo_prs.GitHubClient", FakeClient):
            open_prs = repo_get_open_prs(_candidate())
            merged_prs = repo_get_recent_merged_prs(_candidate())
            searched = repo_search_prs_by_title(_candidate(), "parser edge")

        self.assertEqual(11, open_prs[0].number)
        self.assertEqual([7], open_prs[0].linked_issues)
        self.assertEqual("2026-05-03T00:00:00Z", merged_prs[0].merged_at)
        self.assertEqual(2, len(searched))

    def test_repo_issue_linkage_uses_issue_and_pr_signals(self) -> None:
        class FakeClient:
            def gh_json(self, args: list[str]) -> GitHubResponse:
                return GitHubResponse(
                    ok=True,
                    source="gh",
                    data=[
                        {
                            "number": 12,
                            "title": "Fix issue",
                            "url": "https://github.com/owner/project/pull/12",
                            "state": "open",
                            "author": {"login": "contrib"},
                            "body": "Fixes #7",
                            "labels": [],
                            "createdAt": "2026-05-01T00:00:00Z",
                            "updatedAt": "2026-05-02T00:00:00Z",
                            "mergedAt": None,
                            "isDraft": False,
                            "closingIssuesReferences": [{"number": 7}],
                        }
                    ],
                )

            def rest_json(
                self,
                method: str,
                path: str,
                params: dict | None = None,
                json_body: dict | None = None,
                token_env: str | None = None,
            ) -> GitHubResponse:
                if path.endswith("/issues/7"):
                    return GitHubResponse(
                        ok=True,
                        source="httpx",
                        data={"assignees": [{"login": "maintainer"}]},
                    )
                if path.endswith("/issues/7/comments"):
                    return GitHubResponse(
                        ok=True,
                        source="httpx",
                        data=[{"body": "Please avoid duplicating PR #12"}],
                    )
                raise AssertionError(path)

        with patch("contribarena.tools.repo_prs.GitHubClient", FakeClient):
            linkage = repo_get_issue_linkage(_candidate(), 7)

        self.assertEqual(["maintainer"], linkage.assignees)
        self.assertEqual(12, linkage.linked_prs[0].number)
        self.assertIn("duplicating", linkage.recent_comments[0])

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
        self.assertNotIn("labels", fake.json_body)

    def test_github_pr_client_ensures_and_sets_labels(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict | None]] = []

            def rest_json(
                self,
                method: str,
                path: str,
                params: dict | None = None,
                json_body: dict | None = None,
                token_env: str | None = None,
            ) -> GitHubResponse:
                self.calls.append((method, path, json_body))
                if method == "GET" and path.endswith("/labels/contribarena-live"):
                    return GitHubResponse(ok=True, source="fake", data={"name": "contribarena-live"})
                if method == "GET":
                    return GitHubResponse(ok=False, source="fake", error="repo not found")
                return GitHubResponse(ok=True, source="fake", data={})

        fake = FakeClient()
        client = GitHubPullRequestClient(client=fake, token_env="BOT_TOKEN")  # type: ignore[arg-type]

        ensure = client.ensure_labels(
            owner="owner",
            repo="project",
            labels=["contribarena-live", "risk-low", "risk-low"],
        )
        set_result = client.set_pr_labels(
            owner="owner",
            repo="project",
            issue_number=42,
            labels=["contribarena-live", "risk-low"],
        )

        self.assertTrue(ensure.ok)
        self.assertTrue(set_result.ok)
        self.assertEqual(["contribarena-live", "risk-low"], ensure.labels)
        self.assertEqual(
            [
                ("GET", "/repos/owner/project/labels/contribarena-live", None),
                ("GET", "/repos/owner/project/labels/risk-low", None),
                (
                    "POST",
                    "/repos/owner/project/labels",
                    {
                        "name": "risk-low",
                        "color": "c2e0c6",
                        "description": "Low-risk contribution",
                    },
                ),
                (
                    "PUT",
                    "/repos/owner/project/issues/42/labels",
                    {"labels": ["contribarena-live", "risk-low"]},
                ),
            ],
            fake.calls,
        )

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

    def test_github_pr_client_classifies_empty_check_runs_with_workflow_inventory(self) -> None:
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
                if path.endswith("/check-runs"):
                    return GitHubResponse(ok=True, source="fake", data={"check_runs": []})
                return GitHubResponse(
                    ok=True,
                    source="fake",
                    data={"workflows": [{"name": "CI"}]},
                )

        fake = FakeClient()
        client = GitHubPullRequestClient(client=fake, token_env="BOT_TOKEN")  # type: ignore[arg-type]

        status = client.get_check_runs(owner="owner", repo="project", ref="abc123")

        self.assertEqual("pending", status.status)
        self.assertIn("workflows_configured=1", status.checks[0].details)
        self.assertEqual(
            [
                ("GET", "/repos/owner/project/commits/abc123/check-runs"),
                ("GET", "/repos/owner/project/actions/workflows"),
            ],
            fake.calls,
        )

    def test_github_pr_client_reads_pr_reviews_and_posts_event_comment(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict | None]] = []

            def rest_json(
                self,
                method: str,
                path: str,
                params: dict | None = None,
                json_body: dict | None = None,
                token_env: str | None = None,
            ) -> GitHubResponse:
                self.calls.append((method, path, json_body))
                if path.endswith("/pulls/42") and method == "GET":
                    return GitHubResponse(
                        ok=True,
                        source="fake",
                        data={
                            "number": 42,
                            "state": "open",
                            "merged": False,
                            "html_url": "https://github.com/owner/project/pull/42",
                            "title": "Improve docs",
                            "head": {"sha": "abc123", "ref": "contribarena/docs"},
                            "base": {"ref": "main"},
                            "comments": 1,
                            "review_comments": 2,
                        },
                    )
                if path.endswith("/pulls/42/reviews") and method == "GET":
                    return GitHubResponse(
                        ok=True,
                        source="fake",
                        data=[
                            {
                                "user": {"login": "maintainer"},
                                "state": "CHANGES_REQUESTED",
                                "body": "Please adjust the test.",
                                "submitted_at": "2026-05-13T00:00:00Z",
                                "html_url": "https://github.com/owner/project/pull/42#review",
                            }
                        ],
                    )
                if path.endswith("/issues/42/comments") and method == "POST":
                    return GitHubResponse(
                        ok=True,
                        source="fake",
                        data={
                            "id": 99,
                            "html_url": "https://github.com/owner/project/pull/42#comment",
                        },
                    )
                raise AssertionError(f"unexpected call: {method} {path}")

        fake = FakeClient()
        client = GitHubPullRequestClient(client=fake, token_env="BOT_TOKEN")  # type: ignore[arg-type]

        pr_status = client.get_pr(owner="owner", repo="project", number=42)
        reviews = client.list_reviews(owner="owner", repo="project", number=42)
        comment = client.create_issue_comment(
            owner="owner",
            repo="project",
            issue_number=42,
            body="CI failure fixed in the latest push.",
        )

        self.assertTrue(pr_status.ok)
        self.assertEqual("abc123", pr_status.head_sha)
        self.assertEqual("main", pr_status.base_ref)
        self.assertEqual("CHANGES_REQUESTED", reviews[0].state)
        self.assertTrue(comment.ok)
        self.assertEqual(99, comment.id)
        self.assertEqual(
            ("POST", "/repos/owner/project/issues/42/comments", {"body": "CI failure fixed in the latest push."}),
            fake.calls[-1],
        )


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
