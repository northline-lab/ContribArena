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


def _candidate(owner: str = "alice", repo: str = "proj") -> RepoCandidate:
    return RepoCandidate(owner=owner, repo=repo, url=f"https://github.com/{owner}/{repo}")


class ParseGithubDatetimeTest(unittest.TestCase):
    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_parse_github_datetime(""))

    def test_iso_z_suffix_parses_to_utc(self) -> None:
        result = _parse_github_datetime("2026-05-29T03:57:01Z")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.tzinfo, UTC)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 5)
        self.assertEqual(result.day, 29)

    def test_iso_with_offset_parses(self) -> None:
        result = _parse_github_datetime("2026-05-29T03:57:01+00:00")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.tzinfo, UTC)

    def test_invalid_format_returns_none(self) -> None:
        self.assertIsNone(_parse_github_datetime("not a date"))
        self.assertIsNone(_parse_github_datetime("2026-13-45"))

    def test_normalizes_to_utc(self) -> None:
        result = _parse_github_datetime("2026-05-29T10:00:00+05:00")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.tzinfo, UTC)
        # 10:00 UTC+5 -> 05:00 UTC
        self.assertEqual(result.hour, 5)


class IsExternalRecentPrTest(unittest.TestCase):
    def _recent(self, days: int = 10) -> datetime:
        return datetime.now(UTC) - timedelta(days=days)

    def _recent_item(
        self,
        login: str,
        days_ago: int = 10,
        author_key: str = "author",
        merged_key: str = "mergedAt",
    ) -> dict:
        merged = (datetime.now(UTC) - timedelta(days=days_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return {merged_key: merged, author_key: {"login": login}}

    def test_external_recent_pr_returns_true(self) -> None:
        item = self._recent_item(login="contributor")
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertTrue(_is_external_recent_pr(item, _candidate("alice"), since))

    def test_owner_login_returns_false(self) -> None:
        item = self._recent_item(login="alice")
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertFalse(_is_external_recent_pr(item, _candidate("alice"), since))

    def test_owner_login_case_insensitive_returns_false(self) -> None:
        item = self._recent_item(login="ALICE")
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertFalse(_is_external_recent_pr(item, _candidate("alice"), since))

    def test_bot_suffix_returns_false(self) -> None:
        item = self._recent_item(login="dependabot[bot]")
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertFalse(_is_external_recent_pr(item, _candidate("alice"), since))

    def test_old_pr_returns_false(self) -> None:
        item = self._recent_item(login="contributor", days_ago=400)
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertFalse(_is_external_recent_pr(item, _candidate("alice"), since))

    def test_missing_author_returns_false(self) -> None:
        merged = self._recent().strftime("%Y-%m-%dT%H:%M:%SZ")
        item = {"mergedAt": merged}
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertFalse(_is_external_recent_pr(item, _candidate("alice"), since))

    def test_missing_login_returns_false(self) -> None:
        merged = self._recent().strftime("%Y-%m-%dT%H:%M:%SZ")
        item = {"mergedAt": merged, "author": {}}
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertFalse(_is_external_recent_pr(item, _candidate("alice"), since))

    def test_missing_merged_at_returns_false(self) -> None:
        item = {"author": {"login": "contributor"}}
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertFalse(_is_external_recent_pr(item, _candidate("alice"), since))

    def test_accepts_rest_api_key_names(self) -> None:
        item = self._recent_item(
            login="contributor", author_key="user", merged_key="merged_at"
        )
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertTrue(_is_external_recent_pr(item, _candidate("alice"), since))

    def test_author_not_dict_returns_false(self) -> None:
        merged = self._recent().strftime("%Y-%m-%dT%H:%M:%SZ")
        item = {"mergedAt": merged, "author": "contributor"}
        since = datetime.now(UTC) - timedelta(days=180)
        self.assertFalse(_is_external_recent_pr(item, _candidate("alice"), since))


class LooksEnglishTest(unittest.TestCase):
    def test_plain_english_text_returns_true(self) -> None:
        self.assertTrue(_looks_english("This is a standard README in English."))

    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(_looks_english(""))

    def test_whitespace_only_returns_false(self) -> None:
        self.assertFalse(_looks_english("   \n\t  "))

    def test_non_ascii_heavy_text_returns_false(self) -> None:
        # Build text that is >85% non-ASCII within the first 2000 chars.
        non_ascii = "\u3042" * 1000  # Japanese hiragana 'a'
        self.assertFalse(_looks_english(non_ascii))

    def test_mostly_ascii_with_few_non_ascii_returns_true(self) -> None:
        text = "Hello world. " * 100 + "\u00e9" * 10
        self.assertTrue(_looks_english(text))

    def test_boundary_at_exactly_85_percent_returns_false(self) -> None:
        # 170 ASCII + 30 non-ASCII = 200 chars, 170/200 = 0.85 -> not > 0.85
        text = "a" * 170 + "\u3042" * 30
        self.assertFalse(_looks_english(text))

    def test_just_above_85_percent_returns_true(self) -> None:
        # 171 ASCII + 29 non-ASCII = 200 chars, 171/200 = 0.855 > 0.85
        text = "a" * 171 + "\u3042" * 29
        self.assertTrue(_looks_english(text))

    def test_text_longer_than_2000_chars_caps_sample(self) -> None:
        # First 2000 chars are ASCII; trailing 1000 chars are non-ASCII.
        text = "a" * 2000 + "\u3042" * 1000
        self.assertTrue(_looks_english(text))


class ProhibitsAiOrBotsTest(unittest.TestCase):
    def test_empty_text_returns_false(self) -> None:
        self.assertFalse(_prohibits_ai_or_bots(""))

    def test_clean_text_returns_false(self) -> None:
        text = "We welcome all contributions. Please read CONTRIBUTING.md first."
        self.assertFalse(_prohibits_ai_or_bots(text))

    def test_detects_no_ai_generated(self) -> None:
        self.assertTrue(_prohibits_ai_or_bots("We accept no AI generated PRs."))

    def test_detects_ai_generated_contributions_not_accepted(self) -> None:
        self.assertTrue(
            _prohibits_ai_or_bots("AI-generated contributions are not accepted.")
        )

    def test_detects_do_not_submit_ai(self) -> None:
        self.assertTrue(_prohibits_ai_or_bots("Please do not submit AI code."))

    def test_detects_no_bot_contributions(self) -> None:
        self.assertTrue(_prohibits_ai_or_bots("We accept no bot contributions here."))

    def test_detects_bot_contributions_not_accepted(self) -> None:
        self.assertTrue(
            _prohibits_ai_or_bots("Bot contributions are not accepted in this repo.")
        )

    def test_detects_automated_prs_not_accepted(self) -> None:
        self.assertTrue(
            _prohibits_ai_or_bots("Automated pull requests are not accepted.")
        )

    def test_case_insensitive_match(self) -> None:
        self.assertTrue(_prohibits_ai_or_bots("NO AI GENERATED CONTENT PLEASE."))

    def test_benign_ai_mention_returns_false(self) -> None:
        # Mentions AI but does not use any prohibited phrase.
        text = "We use AI tools in our workflow and welcome AI-assisted work."
        self.assertFalse(_prohibits_ai_or_bots(text))


if __name__ == "__main__":
    unittest.main()
