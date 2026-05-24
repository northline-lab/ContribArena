from __future__ import annotations

import unittest
from unittest.mock import patch

from contribarena.config.schema import RepoCandidate
from contribarena.tools.github_client import GitHubResponse
from contribarena.tools.repo_readme import repo_get_readme


def _candidate() -> RepoCandidate:
    return RepoCandidate(
        owner="owner",
        repo="project",
        url="https://github.com/owner/project",
        branch="main",
    )


class RepoReadmeTest(unittest.TestCase):
    def test_success_returns_readme_content(self) -> None:
        class FakeClient:
            def rest_text(self, path: str) -> GitHubResponse:
                self.path = path
                return GitHubResponse(
                    ok=True, source="httpx", data="# My Project\n\nThis is the readme.\n"
                )

        with patch("contribarena.tools.repo_readme.GitHubClient", FakeClient):
            result = repo_get_readme(_candidate())

        self.assertTrue(result.success)
        self.assertEqual("owner/project", result.full_name)
        self.assertEqual("README", result.path)
        self.assertIn("# My Project", result.content)
        self.assertEqual("", result.error)

    def test_failure_returns_error(self) -> None:
        class FakeClient:
            def rest_text(self, path: str) -> GitHubResponse:
                return GitHubResponse(
                    ok=False, source="httpx", error="Not Found", status_code=404
                )

        with patch("contribarena.tools.repo_readme.GitHubClient", FakeClient):
            result = repo_get_readme(_candidate())

        self.assertFalse(result.success)
        self.assertEqual("owner/project", result.full_name)
        self.assertEqual("", result.content)
        self.assertEqual("Not Found", result.error)

    def test_max_chars_clamping(self) -> None:
        """max_chars is clamped between 500 and 20_000."""
        long_content = "A" * 30_000

        class FakeClient:
            def rest_text(self, path: str) -> GitHubResponse:
                return GitHubResponse(ok=True, source="httpx", data=long_content)

        with patch("contribarena.tools.repo_readme.GitHubClient", FakeClient):
            result_small = repo_get_readme(_candidate(), max_chars=100)

        self.assertTrue(result_small.success)
        self.assertEqual(500 + len("...[truncated]"), len(result_small.content))

        with patch("contribarena.tools.repo_readme.GitHubClient", FakeClient):
            result_large = repo_get_readme(_candidate(), max_chars=50_000)

        self.assertTrue(result_large.success)
        self.assertEqual(20_000 + len("...[truncated]"), len(result_large.content))

    def test_max_chars_preserves_short_content(self) -> None:
        """Content shorter than max_chars is returned unchanged."""
        short_content = "Short readme"

        class FakeClient:
            def rest_text(self, path: str) -> GitHubResponse:
                return GitHubResponse(ok=True, source="httpx", data=short_content)

        with patch("contribarena.tools.repo_readme.GitHubClient", FakeClient):
            result = repo_get_readme(_candidate(), max_chars=6000)

        self.assertTrue(result.success)
        self.assertEqual(short_content, result.content)

    def test_truncation_marker_is_appended(self) -> None:
        """When content exceeds max_chars, it is truncated with a marker."""
        content = "X" * 1000

        class FakeClient:
            def rest_text(self, path: str) -> GitHubResponse:
                return GitHubResponse(ok=True, source="httpx", data=content)

        with patch("contribarena.tools.repo_readme.GitHubClient", FakeClient):
            result = repo_get_readme(_candidate(), max_chars=500)

        self.assertTrue(result.success)
        self.assertTrue(result.content.endswith("...[truncated]"))
        self.assertEqual(500 + len("...[truncated]"), len(result.content))

    def test_empty_readme_content(self) -> None:
        """An empty readme is still reported as success."""

        class FakeClient:
            def rest_text(self, path: str) -> GitHubResponse:
                return GitHubResponse(ok=True, source="httpx", data="")

        with patch("contribarena.tools.repo_readme.GitHubClient", FakeClient):
            result = repo_get_readme(_candidate())

        self.assertTrue(result.success)
        self.assertEqual("", result.content)

    def test_rest_text_path_uses_expected_api_route(self) -> None:
        """The client is called with the expected API path."""
        captured: list[str] = []

        class FakeClient:
            def rest_text(self, path: str) -> GitHubResponse:
                captured.append(path)
                return GitHubResponse(ok=True, source="httpx", data="# OK\n")

        with patch("contribarena.tools.repo_readme.GitHubClient", FakeClient):
            repo_get_readme(_candidate())

        self.assertEqual(len(captured), 1)
        self.assertEqual("/repos/owner/project/readme", captured[0])


if __name__ == "__main__":
    unittest.main()
