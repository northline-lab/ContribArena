from __future__ import annotations

import unittest
from unittest.mock import patch

from contribarena.config.schema import RepoCandidate
from contribarena.models import RepoReadmeResult
from contribarena.tools.github_client import GitHubResponse
from contribarena.tools.repo_readme import repo_get_readme


def _candidate() -> RepoCandidate:
    return RepoCandidate(
        owner="owner",
        repo="project",
        url="https://github.com/owner/project",
        branch="main",
    )


class _RecordingClient:
    """Fake GitHubClient that records the requested REST path and returns a
    pre-configured response."""

    def __init__(self, response: GitHubResponse) -> None:
        self._response = response
        self.path: str | None = None

    def rest_text(self, path: str) -> GitHubResponse:
        self.path = path
        return self._response


class RepoGetReadmeTest(unittest.TestCase):
    def test_returns_failure_when_rest_response_not_ok(self) -> None:
        client = _RecordingClient(
            GitHubResponse(ok=False, source="httpx", data=None, error="not_found"),
        )

        with patch(
            "contribarena.tools.repo_readme.GitHubClient",
            return_value=client,
        ):
            result = repo_get_readme(_candidate())

        self.assertIsInstance(result, RepoReadmeResult)
        self.assertFalse(result.success)
        self.assertEqual("owner/project", result.full_name)
        self.assertEqual("not_found", result.error)
        self.assertEqual("", result.content)
        self.assertEqual("/repos/owner/project/readme", client.path)

    def test_returns_empty_content_when_response_data_is_none(self) -> None:
        client = _RecordingClient(
            GitHubResponse(ok=True, source="httpx", data=None),
        )

        with patch(
            "contribarena.tools.repo_readme.GitHubClient",
            return_value=client,
        ):
            result = repo_get_readme(_candidate())

        self.assertTrue(result.success)
        self.assertEqual("", result.content)
        self.assertEqual("", result.error)

    def test_truncates_content_when_longer_than_max_chars(self) -> None:
        long_body = "a" * 1200
        client = _RecordingClient(
            GitHubResponse(ok=True, source="httpx", data=long_body),
        )

        with patch(
            "contribarena.tools.repo_readme.GitHubClient",
            return_value=client,
        ):
            result = repo_get_readme(_candidate(), max_chars=500)

        self.assertTrue(result.success)
        # Limit floor is 500 chars; truncate_for_operator appends a marker.
        self.assertTrue(result.content.startswith("a" * 500))
        self.assertTrue(result.content.endswith("...[truncated]"))
        self.assertNotIn("a" * 1200, result.content)

    def test_max_chars_is_floored_to_500(self) -> None:
        # A body of 600 chars with a tiny requested max should still be
        # preserved up to the 500-char floor before truncation kicks in.
        body = "b" * 600
        client = _RecordingClient(
            GitHubResponse(ok=True, source="httpx", data=body),
        )

        with patch(
            "contribarena.tools.repo_readme.GitHubClient",
            return_value=client,
        ):
            result = repo_get_readme(_candidate(), max_chars=10)

        self.assertTrue(result.success)
        self.assertTrue(result.content.startswith("b" * 500))
        self.assertTrue(result.content.endswith("...[truncated]"))

    def test_max_chars_is_capped_at_20000(self) -> None:
        # When the caller requests a huge limit, the function caps the limit
        # at 20_000 characters before truncation.
        body = "c" * 25_000
        client = _RecordingClient(
            GitHubResponse(ok=True, source="httpx", data=body),
        )

        with patch(
            "contribarena.tools.repo_readme.GitHubClient",
            return_value=client,
        ):
            result = repo_get_readme(_candidate(), max_chars=10_000_000)

        self.assertTrue(result.success)
        self.assertTrue(result.content.startswith("c" * 20_000))
        self.assertTrue(result.content.endswith("...[truncated]"))

    def test_short_content_is_returned_verbatim(self) -> None:
        client = _RecordingClient(
            GitHubResponse(ok=True, source="httpx", data="# Hello\nworld"),
        )

        with patch(
            "contribarena.tools.repo_readme.GitHubClient",
            return_value=client,
        ):
            result = repo_get_readme(_candidate())

        self.assertTrue(result.success)
        self.assertEqual("# Hello\nworld", result.content)
        self.assertEqual("README", result.path)


if __name__ == "__main__":  # pragma: no cover - manual invocation helper
    unittest.main()
