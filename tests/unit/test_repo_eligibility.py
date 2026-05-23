from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from contribarena.config.schema import RepoCandidate
from contribarena.tools.repo_eligibility import (
    _is_external_recent_pr,
    _looks_english,
    _parse_github_datetime,
    _prohibits_ai_or_bots,
)


class ProhibitsAiOrBotsTest(unittest.TestCase):
    def test_no_bot_policy_returns_false(self) -> None:
        self.assertFalse(_prohibits_ai_or_bots("Contributions are welcome."))

    def test_detects_ai_generated_phrase(self) -> None:
        self.assertTrue(_prohibits_ai_or_bots("no ai generated contributions accepted"))

    def test_detects_bot_contributions_phrase(self) -> None:
        self.assertTrue(_prohibits_ai_or_bots("Bot contributions are not accepted"))

    def test_detects_automated_pr_phrase(self) -> None:
        self.assertTrue(_prohibits_ai_or_bots("Automated pull requests are not accepted"))

    def test_case_insensitive(self) -> None:
        self.assertTrue(_prohibits_ai_or_bots("NO BOT CONTRIBUTIONS ALLOWED"))

    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(_prohibits_ai_or_bots(""))


class LooksEnglishTest(unittest.TestCase):
    def test_plain_english_returns_true(self) -> None:
        self.assertTrue(_looks_english("This is a simple English sentence."))

    def test_non_ascii_heavy_returns_false(self) -> None:
        self.assertFalse(_looks_english("これは日本語のテキストです。" * 50))

    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(_looks_english(""))

    def test_whitespace_only_returns_false(self) -> None:
        self.assertFalse(_looks_english("   \n\t   "))

    def test_mixed_ascii_returns_true(self) -> None:
        # Mostly ASCII with a few unicode characters should still pass
        text = "Hello world, this is a test with one unicode char: é"
        self.assertTrue(_looks_english(text))


class ParseGithubDatetimeTest(unittest.TestCase):
    def test_iso_format_with_z(self) -> None:
        result = _parse_github_datetime("2026-05-01T00:00:00Z")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 5)

    def test_iso_format_with_offset(self) -> None:
        result = _parse_github_datetime("2026-05-01T12:30:00+00:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.hour, 12)

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_parse_github_datetime(""))

    def test_invalid_datetime_returns_none(self) -> None:
        self.assertIsNone(_parse_github_datetime("not-a-datetime"))


class IsExternalRecentPrTest(unittest.TestCase):
    def setUp(self) -> None:
        self.since = datetime.now(UTC) - timedelta(days=180)
        self.candidate = RepoCandidate(
            owner="owner", repo="repo", url="https://github.com/owner/repo"
        )

    def test_external_pr_is_recent(self) -> None:
        item = {
            "author": {"login": "external-user"},
            "mergedAt": datetime.now(UTC).isoformat(),
        }
        self.assertTrue(_is_external_recent_pr(item, self.candidate, self.since))

    def test_owner_pr_is_not_external(self) -> None:
        item = {
            "author": {"login": "owner"},
            "mergedAt": datetime.now(UTC).isoformat(),
        }
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_bot_pr_is_not_external(self) -> None:
        item = {
            "author": {"login": "dependabot[bot]"},
            "mergedAt": datetime.now(UTC).isoformat(),
        }
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_old_pr_is_not_recent(self) -> None:
        item = {
            "author": {"login": "external-user"},
            "mergedAt": (datetime.now(UTC) - timedelta(days=365)).isoformat(),
        }
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_missing_author_is_not_external(self) -> None:
        item = {
            "mergedAt": datetime.now(UTC).isoformat(),
        }
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_missing_merged_at_is_not_recent(self) -> None:
        item = {
            "author": {"login": "external-user"},
        }
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_rest_api_format_user_key(self) -> None:
        item = {
            "user": {"login": "external-user"},
            "merged_at": datetime.now(UTC).isoformat(),
        }
        self.assertTrue(_is_external_recent_pr(item, self.candidate, self.since))


if __name__ == "__main__":
    unittest.main()
