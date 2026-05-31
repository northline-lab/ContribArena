from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from contribarena.tools.github_client import (
    GitHubResponse,
    classify_github_error,
    classify_http_error,
    github_token,
    repo_api_path,
)


class GitHubResponseTest(unittest.TestCase):
    """Tests for the GitHubResponse frozen dataclass."""

    def test_success_defaults(self) -> None:
        resp = GitHubResponse(ok=True, data={"id": 1})
        self.assertTrue(resp.ok)
        self.assertEqual(resp.data, {"id": 1})
        self.assertEqual(resp.error, "")
        self.assertEqual(resp.source, "")
        self.assertIsNone(resp.status_code)

    def test_failure_defaults(self) -> None:
        resp = GitHubResponse(ok=False, error="not found")
        self.assertFalse(resp.ok)
        self.assertIsNone(resp.data)
        self.assertEqual(resp.error, "not found")
        self.assertEqual(resp.source, "")
        self.assertIsNone(resp.status_code)

    def test_explicit_values(self) -> None:
        resp = GitHubResponse(ok=True, data=[1, 2], source="httpx", status_code=200)
        self.assertTrue(resp.ok)
        self.assertEqual(resp.data, [1, 2])
        self.assertEqual(resp.source, "httpx")
        self.assertEqual(resp.status_code, 200)

    def test_frozen_immutability(self) -> None:
        resp = GitHubResponse(ok=True)
        with self.assertRaises(AttributeError):
            resp.ok = False  # type: ignore[misc]

    def test_data_none_is_valid(self) -> None:
        resp = GitHubResponse(ok=True, data=None)
        self.assertTrue(resp.ok)
        self.assertIsNone(resp.data)

    def test_status_code_zero(self) -> None:
        resp = GitHubResponse(ok=True, status_code=0)
        self.assertEqual(resp.status_code, 0)


class ClassifyGithubErrorTest(unittest.TestCase):
    """Tests for classify_github_error(returncode, detail) -> str."""

    def test_authentication_missing(self) -> None:
        result = classify_github_error(1, "authentication required")
        self.assertIn("authentication missing", result)
        self.assertIn("exit_code=1", result)

    def test_login_hint(self) -> None:
        result = classify_github_error(1, "please login first")
        self.assertIn("authentication missing", result)

    def test_credentials_hint(self) -> None:
        result = classify_github_error(1, "credentials not found")
        self.assertIn("authentication missing", result)

    def test_rate_limit(self) -> None:
        result = classify_github_error(1, "rate limit exceeded")
        self.assertIn("rate limit or abuse detection", result)

    def test_abuse_detection(self) -> None:
        result = classify_github_error(1, "abuse detection triggered")
        self.assertIn("rate limit or abuse detection", result)

    def test_secondary_rate(self) -> None:
        result = classify_github_error(1, "secondary rate limit reached")
        self.assertIn("rate limit or abuse detection", result)

    def test_not_found(self) -> None:
        result = classify_github_error(1, "repository not found")
        self.assertIn("command failed or repo not found", result)

    def test_could_not_resolve(self) -> None:
        result = classify_github_error(1, "could not resolve host")
        self.assertIn("command failed or repo not found", result)

    def test_generic_failure(self) -> None:
        result = classify_github_error(1, "something went wrong")
        self.assertIn("gh command failed", result)

    def test_case_insensitive(self) -> None:
        result = classify_github_error(1, "AUTH REQUIRED")
        self.assertIn("authentication missing", result)

    def test_exit_code_preserved(self) -> None:
        result = classify_github_error(42, "auth error")
        self.assertIn("exit_code=42", result)

    def test_detail_preserved_in_output(self) -> None:
        result = classify_github_error(1, "my specific error")
        self.assertIn("my specific error", result)

    def test_auth_takes_priority_over_rate(self) -> None:
        """Priority: auth keyword checked before rate in classify_github_error. This documents intentional precedence per the if/elif chain."""
        result = classify_github_error(1, "auth and rate limit")
        self.assertIn("authentication missing", result)

    def test_rate_takes_priority_over_not_found(self) -> None:
        """Priority: rate-limit checked before not-found in classify_github_error. This documents intentional precedence per the if/elif chain."""
        result = classify_github_error(1, "rate limit and not found")
        self.assertIn("rate limit or abuse detection", result)


class ClassifyHttpErrorTest(unittest.TestCase):
    """Tests for classify_http_error(status_code, body) -> str."""

    def test_401_authentication(self) -> None:
        result = classify_http_error(401, "bad credentials")
        self.assertIn("authentication missing or forbidden", result)
        self.assertIn("status_code=401", result)

    def test_403_authentication(self) -> None:
        result = classify_http_error(403, "access denied")
        self.assertIn("authentication missing or forbidden", result)

    def test_401_rate_limit(self) -> None:
        """401 with rate limit text is classified as rate limit."""
        result = classify_http_error(401, "rate limit exceeded")
        self.assertIn("rate limit or abuse detection", result)

    def test_403_rate_limit(self) -> None:
        """403 with abuse text is classified as rate limit."""
        result = classify_http_error(403, "abuse detection")
        self.assertIn("rate limit or abuse detection", result)

    def test_401_rate_takes_priority(self) -> None:
        """Priority: rate-limit/abuse check runs first for 401/403 in classify_http_error. This documents intentional precedence per the if/elif chain."""
        result = classify_http_error(401, "rate limit and auth")
        self.assertIn("rate limit or abuse detection", result)

    def test_404_not_found(self) -> None:
        result = classify_http_error(404, "not found")
        self.assertIn("repo not found", result)
        self.assertIn("status_code=404", result)

    def test_generic_500(self) -> None:
        result = classify_http_error(500, "internal server error")
        self.assertIn("http request failed", result)

    def test_generic_422(self) -> None:
        result = classify_http_error(422, "validation failed")
        self.assertIn("http request failed", result)

    def test_body_truncated_at_500_chars(self) -> None:
        long_body = "x" * 600
        result = classify_http_error(500, long_body)
        self.assertTrue(len(result) < 600 + 50)

    def test_case_insensitive(self) -> None:
        result = classify_http_error(403, "RATE LIMIT")
        self.assertIn("rate limit or abuse detection", result)

    def test_403_not_rate_limit(self) -> None:
        """403 without rate/abuse keywords falls to generic auth."""
        result = classify_http_error(403, "permission denied")
        self.assertIn("authentication missing or forbidden", result)


class RepoApiPathTest(unittest.TestCase):
    """Tests for repo_api_path(owner, repo, suffix) -> str."""

    def test_basic_path(self) -> None:
        result = repo_api_path("octocat", "hello-world")
        self.assertEqual(result, "/repos/octocat/hello-world")

    def test_with_suffix(self) -> None:
        result = repo_api_path("octocat", "hello-world", "issues")
        self.assertEqual(result, "/repos/octocat/hello-world/issues")

    def test_suffix_with_leading_slash(self) -> None:
        result = repo_api_path("octocat", "hello-world", "/issues")
        self.assertEqual(result, "/repos/octocat/hello-world/issues")

    def test_suffix_with_multiple_leading_slashes(self) -> None:
        result = repo_api_path("octocat", "hello-world", "///issues")
        self.assertEqual(result, "/repos/octocat/hello-world/issues")

    def test_empty_suffix(self) -> None:
        result = repo_api_path("octocat", "hello-world", "")
        self.assertEqual(result, "/repos/octocat/hello-world")

    def test_owner_url_quoted(self) -> None:
        result = repo_api_path("org-name", "repo")
        self.assertEqual(result, "/repos/org-name/repo")

    def test_special_chars_url_quoted(self) -> None:
        result = repo_api_path("org+name", "repo")
        self.assertIn("/repos/org%2Bname/repo", result)

    def test_repo_name_url_quoted(self) -> None:
        result = repo_api_path("owner", "repo+name")
        self.assertIn("/repos/owner/repo%2Bname", result)

    def test_slash_in_owner_quoted(self) -> None:
        result = repo_api_path("org/slash", "repo")
        self.assertIn("/repos/org%2Fslash/repo", result)

    def test_deep_suffix(self) -> None:
        result = repo_api_path("octocat", "hello-world", "issues/1/comments")
        self.assertEqual(result, "/repos/octocat/hello-world/issues/1/comments")


class GithubTokenTest(unittest.TestCase):
    """Tests for github_token() reading env vars."""

    def test_github_token_from_gh_token(self) -> None:
        with patch.dict(os.environ, {"GH_TOKEN": "gh_test_123", "GITHUB_TOKEN": "gh_alt_456"}):
            self.assertEqual(github_token(), "gh_test_123")

    def test_github_token_from_github_token(self) -> None:
        with patch.dict(os.environ, {"GITHUB_TOKEN": "gh_alt_456"}, clear=False):
            if "GH_TOKEN" in os.environ:
                del os.environ["GH_TOKEN"]
            self.assertEqual(github_token(), "gh_alt_456")

    def test_github_token_none_when_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(github_token())

    def test_gh_token_preferred_over_github_token(self) -> None:
        with patch.dict(
            os.environ, {"GH_TOKEN": "primary", "GITHUB_TOKEN": "fallback"}, clear=True
        ):
            self.assertEqual(github_token(), "primary")

    def test_empty_gh_token_falls_through(self) -> None:
        """Empty GH_TOKEN should not be returned; falls through to GITHUB_TOKEN."""
        with patch.dict(
            os.environ, {"GH_TOKEN": "", "GITHUB_TOKEN": "fallback"}, clear=True
        ):
            self.assertEqual(github_token(), "fallback")


class GithubClientImportTest(unittest.TestCase):
    """Tests for package-level imports."""

    def test_all_symbols_importable(self) -> None:
        from contribarena.tools.github_client import (
            GitHubClient,
            GitHubResponse,
            classify_github_error,
            classify_http_error,
            github_token,
            repo_api_path,
        )
        self.assertTrue(callable(classify_github_error))
        self.assertTrue(callable(classify_http_error))
        self.assertTrue(callable(repo_api_path))
        self.assertTrue(callable(github_token))
        self.assertTrue(isinstance(GitHubResponse(ok=True), GitHubResponse))
        self.assertTrue(callable(GitHubClient))


if __name__ == "__main__":
    unittest.main()
