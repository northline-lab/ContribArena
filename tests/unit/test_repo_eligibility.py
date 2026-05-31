from __future__ import annotations

import unittest
from datetime import UTC, datetime

from contribarena.config.schema import RepoCandidate
from contribarena.tools.repo_eligibility import (
    _is_external_recent_pr,
    _looks_english,
    _parse_github_datetime,
    _prohibits_ai_or_bots,
)


class ParseGithubDatetimeTest(unittest.TestCase):
    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_parse_github_datetime(""))

    def test_iso_format_with_z_suffix(self) -> None:
        result = _parse_github_datetime("2026-01-15T10:30:00Z")
        self.assertIsNotNone(result)
        self.assertEqual(2026, result.year)
        self.assertEqual(1, result.month)
        self.assertEqual(15, result.day)
        self.assertEqual(10, result.hour)
        self.assertEqual(30, result.minute)
        self.assertEqual(0, result.second)
        self.assertEqual(UTC, result.tzinfo)

    def test_iso_format_with_timezone_offset(self) -> None:
        result = _parse_github_datetime("2026-01-15T10:30:00+00:00")
        self.assertIsNotNone(result)
        self.assertEqual(2026, result.year)
        self.assertEqual(UTC, result.tzinfo)

    def test_invalid_format_returns_none(self) -> None:
        self.assertIsNone(_parse_github_datetime("not-a-date"))
        self.assertIsNone(_parse_github_datetime("2026/01/15"))

    def test_timezone_conversion(self) -> None:
        result = _parse_github_datetime("2026-01-15T10:30:00+05:00")
        self.assertIsNotNone(result)
        self.assertEqual(UTC, result.tzinfo)
        self.assertEqual(5, result.hour)
        self.assertEqual(30, result.minute)


class LooksEnglishTest(unittest.TestCase):
    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(_looks_english(""))

    def test_whitespace_only_returns_false(self) -> None:
        self.assertFalse(_looks_english("   \n\t  "))

    def test_pure_ascii_returns_true(self) -> None:
        text = "This is a simple English text with only ASCII characters."
        self.assertTrue(_looks_english(text))

    def test_high_ascii_ratio_returns_true(self) -> None:
        text = "Hello world " * 10 + "é" * 10
        self.assertTrue(_looks_english(text))

    def test_low_ascii_ratio_returns_false(self) -> None:
        text = "Привет мир " * 20
        self.assertFalse(_looks_english(text))

    def test_exactly_85_percent_boundary(self) -> None:
        text = "a" * 85 + "é" * 15
        self.assertFalse(_looks_english(text))

    def test_above_85_percent_boundary(self) -> None:
        text = "a" * 86 + "é" * 14
        self.assertTrue(_looks_english(text))

    def test_long_text_truncated_at_2000_chars(self) -> None:
        text = "a" * 2000 + "é" * 1000
        self.assertTrue(_looks_english(text))


class ProhibitsAiOrBotsTest(unittest.TestCase):
    def test_empty_text_returns_false(self) -> None:
        self.assertFalse(_prohibits_ai_or_bots(""))

    def test_no_prohibited_phrases_returns_false(self) -> None:
        text = "We welcome contributions from everyone. Please read our guidelines."
        self.assertFalse(_prohibits_ai_or_bots(text))

    def test_no_ai_generated_detected(self) -> None:
        text = "We do not accept no AI generated code in this repository."
        self.assertTrue(_prohibits_ai_or_bots(text))

    def test_ai_generated_contributions_not_accepted(self) -> None:
        text = "AI-generated contributions are not accepted here."
        self.assertTrue(_prohibits_ai_or_bots(text))

    def test_do_not_submit_ai(self) -> None:
        text = "Please do not submit AI written code."
        self.assertTrue(_prohibits_ai_or_bots(text))

    def test_no_bot_contributions(self) -> None:
        text = "We have a policy of no bot contributions."
        self.assertTrue(_prohibits_ai_or_bots(text))

    def test_bot_contributions_not_accepted(self) -> None:
        text = "Bot contributions are not accepted in this project."
        self.assertTrue(_prohibits_ai_or_bots(text))

    def test_automated_pull_requests_not_accepted(self) -> None:
        text = "Automated pull requests are not accepted."
        self.assertTrue(_prohibits_ai_or_bots(text))

    def test_case_insensitive_detection(self) -> None:
        text = "NO AI GENERATED CODE PLEASE"
        self.assertTrue(_prohibits_ai_or_bots(text))

    def test_partial_phrase_not_detected(self) -> None:
        text = "We use AI tools but accept all contributions."
        self.assertFalse(_prohibits_ai_or_bots(text))


class IsExternalRecentPrTest(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = RepoCandidate(
            owner="testowner", repo="testrepo", url="https://github.com/testowner/testrepo"
        )
        self.since = datetime(2026, 1, 1, tzinfo=UTC)

    def test_empty_merged_at_returns_false(self) -> None:
        item = {"mergedAt": "", "author": {"login": "contributor"}}
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_missing_author_returns_false(self) -> None:
        item = {"mergedAt": "2026-06-15T10:00:00Z"}
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_missing_login_returns_false(self) -> None:
        item = {"mergedAt": "2026-06-15T10:00:00Z", "author": {}}
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_old_pr_returns_false(self) -> None:
        item = {"mergedAt": "2025-06-15T10:00:00Z", "author": {"login": "contributor"}}
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_recent_external_pr_returns_true(self) -> None:
        item = {"mergedAt": "2026-06-15T10:00:00Z", "author": {"login": "contributor"}}
        self.assertTrue(_is_external_recent_pr(item, self.candidate, self.since))

    def test_owner_pr_returns_false(self) -> None:
        item = {"mergedAt": "2026-06-15T10:00:00Z", "author": {"login": "testowner"}}
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_owner_pr_case_insensitive(self) -> None:
        item = {"mergedAt": "2026-06-15T10:00:00Z", "author": {"login": "TestOwner"}}
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_bot_pr_returns_false(self) -> None:
        item = {"mergedAt": "2026-06-15T10:00:00Z", "author": {"login": "dependabot[bot]"}}
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_rest_api_format_merged_at(self) -> None:
        item = {"merged_at": "2026-06-15T10:00:00Z", "user": {"login": "contributor"}}
        self.assertTrue(_is_external_recent_pr(item, self.candidate, self.since))

    def test_rest_api_format_user_field(self) -> None:
        item = {"merged_at": "2026-06-15T10:00:00Z", "user": {"login": "contributor"}}
        self.assertTrue(_is_external_recent_pr(item, self.candidate, self.since))

    def test_author_not_dict_treated_as_missing(self) -> None:
        item = {"mergedAt": "2026-06-15T10:00:00Z", "author": "not-a-dict"}
        self.assertFalse(_is_external_recent_pr(item, self.candidate, self.since))

    def test_exactly_at_since_boundary(self) -> None:
        item = {"mergedAt": "2026-01-01T00:00:00Z", "author": {"login": "contributor"}}
        # Equal timestamps are considered recent (merged_at >= since)
        self.assertTrue(_is_external_recent_pr(item, self.candidate, self.since))

    def test_one_second_after_since(self) -> None:
        item = {"mergedAt": "2026-01-01T00:00:01Z", "author": {"login": "contributor"}}
        self.assertTrue(_is_external_recent_pr(item, self.candidate, self.since))
