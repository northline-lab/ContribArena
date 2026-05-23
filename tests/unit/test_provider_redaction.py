from __future__ import annotations

import unittest

from contribarena.providers.redaction import (
    RedactedText,
    redact_visible_text,
)


class RedactedTextDataclassTest(unittest.TestCase):
    """Unit tests for the RedactedText frozen dataclass."""

    def test_default_redacted_is_false(self) -> None:
        rt = RedactedText(text="hello")
        self.assertFalse(rt.redacted)

    def test_default_classes_is_empty_list(self) -> None:
        rt = RedactedText(text="hello")
        self.assertEqual(rt.classes, [])

    def test_explicit_fields_are_stored(self) -> None:
        rt = RedactedText(text="***", redacted=True, classes=["github_token"])
        self.assertEqual(rt.text, "***")
        self.assertTrue(rt.redacted)
        self.assertEqual(rt.classes, ["github_token"])

    def test_frozen_raises_on_attribute_assignment(self) -> None:
        rt = RedactedText(text="hello")
        with self.assertRaises(AttributeError):
            rt.text = "changed"


class RedactVisibleTextGithubTokenTest(unittest.TestCase):
    """Unit tests for redact_visible_text github_token pattern."""

    def test_redacts_github_personal_access_token(self) -> None:
        text = "pushed using token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"
        result = redact_visible_text(text)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234", result.text)
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)

    def test_redacts_github_oauth_token(self) -> None:
        text = "auth with gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        result = redact_visible_text(text)
        self.assertNotIn("gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890", result.text)
        self.assertIn("github_token", result.classes)

    def test_redacts_github_user_to_server_token(self) -> None:
        text = "using ghu_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        result = redact_visible_text(text)
        self.assertNotIn("ghu_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890", result.text)
        self.assertIn("github_token", result.classes)

    def test_redacts_github_server_to_server_token(self) -> None:
        text = "using ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        result = redact_visible_text(text)
        self.assertNotIn("ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890", result.text)
        self.assertIn("github_token", result.classes)

    def test_redacts_github_refresh_token(self) -> None:
        text = "refresh ghr_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        result = redact_visible_text(text)
        self.assertNotIn("ghr_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890", result.text)
        self.assertIn("github_token", result.classes)

    def test_does_not_redact_short_github_token_prefix(self) -> None:
        text = "ghp_short"
        result = redact_visible_text(text)
        self.assertEqual(result.text, "ghp_short")
        self.assertFalse(result.redacted)
        self.assertEqual(result.classes, [])


class RedactVisibleTextBearerTokenTest(unittest.TestCase):
    """Unit tests for redact_visible_text bearer_token pattern."""

    def test_redacts_bearer_token(self) -> None:
        text = "Authorization: Bearer abcDEF1234567890ghiJKL"
        result = redact_visible_text(text)
        self.assertNotIn("abcDEF1234567890ghiJKL", result.text)
        self.assertTrue(result.redacted)
        self.assertIn("bearer_token", result.classes)

    def test_redacts_bearer_token_case_insensitive(self) -> None:
        text = "auth: bearer MyToken1234567890abcdef"
        result = redact_visible_text(text)
        self.assertNotIn("MyToken1234567890abcdef", result.text)
        self.assertIn("bearer_token", result.classes)

    def test_does_not_redact_short_bearer_value(self) -> None:
        text = "Bearer shortval"
        result = redact_visible_text(text)
        # "shortval" is only 9 chars, below 16 minimum
        self.assertFalse("bearer_token" in result.classes)

    def test_redacts_bearer_value_at_minimum_length_16(self) -> None:
        # Exactly 16 chars should match the bearer_token pattern
        text = "Bearer abcdefghijklmnop"
        result = redact_visible_text(text)
        self.assertIn("bearer_token", result.classes)
        self.assertNotIn("abcdefghijklmnop", result.text)

    def test_does_not_redact_bearer_value_just_below_minimum_15(self) -> None:
        # 15 chars should not match the bearer_token pattern
        text = "Bearer abcdefghijklmno"
        result = redact_visible_text(text)
        self.assertFalse("bearer_token" in result.classes)

class RedactVisibleTextApiKeyTest(unittest.TestCase):
    """Unit tests for redact_visible_text api_key pattern."""

    def test_redacts_api_key_equals_assignment(self) -> None:
        text = "api_key = sk-mysecretkey12345678"
        result = redact_visible_text(text)
        self.assertNotIn("sk-mysecretkey12345678", result.text)
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)

    def test_redacts_token_equals_assignment(self) -> None:
        text = "token = ghp_abc123def456ghi789jkl012mno345"
        result = redact_visible_text(text)
        self.assertNotIn("ghp_abc123def456ghi789jkl012mno345", result.text)

    def test_redacts_secret_colon_assignment(self) -> None:
        text = "secret: mylongsecretvalue12345678"
        result = redact_visible_text(text)
        self.assertNotIn("mylongsecretvalue12345678", result.text)
        self.assertIn("api_key", result.classes)

    def test_does_not_redact_short_value_after_key(self) -> None:
        text = "api_key = short"
        result = redact_visible_text(text)
        # "short" is only 5 chars, below 12 minimum
        self.assertFalse("api_key" in result.classes)

    def test_redacts_api_key_value_at_minimum_length_12(self) -> None:
        # Exactly 12 chars should match the api_key pattern
        text = "api_key = abcdefghijkl"
        result = redact_visible_text(text)
        self.assertIn("api_key", result.classes)
        self.assertNotIn("abcdefghijkl", result.text)

    def test_does_not_redact_api_key_value_just_below_minimum_11(self) -> None:
        # 11 chars should not match the api_key pattern
        text = "api_key = abcdefghijk"
        result = redact_visible_text(text)
        self.assertFalse("api_key" in result.classes)

class RedactVisibleTextXAccessTokenTest(unittest.TestCase):
    """Unit tests for redact_visible_text x_access_token_url pattern."""

    def test_redacts_x_access_token_in_url(self) -> None:
        text = "https://x-access-token:mysecrettoken@github.com/owner/repo"
        result = redact_visible_text(text)
        # The api_key pattern also matches "token:mysecrettoken@", so both
        # api_key and x_access_token_url classes may appear; however the
        # api_key pattern runs first, replacing "token:mysecrettoken@" with
        # "token:***", which then prevents the x_access_token_url pattern
        # from matching. The net result is "https://x-access-***".
        self.assertIn("api_key", result.classes)
        self.assertTrue(result.redacted)
        self.assertNotIn("mysecrettoken", result.text)


class RedactVisibleTextCombinedTest(unittest.TestCase):
    """Unit tests for redact_visible_text with multiple patterns and edge cases."""

    def test_redacts_multiple_secret_classes_in_one_string(self) -> None:
        text = "token=ghp_abc123def456ghi789jkl012mno345 and password=s3cret"  # noqa: S106
        result = redact_visible_text(text)
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)

    def test_clean_text_has_no_redaction(self) -> None:
        text = "Hello world, this is a normal message."
        result = redact_visible_text(text)
        self.assertEqual(result.text, text)
        self.assertFalse(result.redacted)
        self.assertEqual(result.classes, [])

    def test_strips_whitespace(self) -> None:
        text = "  Hello world  "
        result = redact_visible_text(text)
        self.assertEqual(result.text, "Hello world")

    def test_empty_string_returns_empty_redacted_text(self) -> None:
        text = ""
        result = redact_visible_text(text)
        self.assertEqual(result.text, "")
        self.assertFalse(result.redacted)

    def test_whitespace_only_string_returns_empty_text(self) -> None:
        text = "   "
        result = redact_visible_text(text)
        self.assertEqual(result.text, "")
        self.assertFalse(result.redacted)


class RedactVisibleTextTruncationTest(unittest.TestCase):
    """Unit tests for redact_visible_text truncation behavior."""

    def test_truncates_long_clean_text(self) -> None:
        text = "a" * 900
        result = redact_visible_text(text, max_chars=800)
        self.assertTrue(result.text.endswith(" [truncated]"))
        self.assertTrue(result.redacted)
        self.assertIn("truncated", result.classes)
        self.assertNotIn("github_token", result.classes)

    def test_does_not_truncate_text_within_max_chars(self) -> None:
        text = "a" * 800
        result = redact_visible_text(text, max_chars=800)
        self.assertFalse("truncated" in result.classes)
        self.assertEqual(len(result.text), 800)

    def test_truncation_preserves_prefix_then_appends_marker(self) -> None:
        text = "b" * 1000
        result = redact_visible_text(text, max_chars=800)
        # max_chars - 15 = 785 chars of prefix, then " [truncated]"
        prefix_len = 800 - 15  # 785
        expected = "b" * prefix_len + " [truncated]"
        self.assertEqual(result.text, expected.rstrip())

    def test_custom_max_chars_truncates(self) -> None:
        text = "c" * 200
        result = redact_visible_text(text, max_chars=100)
        self.assertTrue(result.text.endswith(" [truncated]"))
        self.assertIn("truncated", result.classes)

    def test_truncation_and_redaction_combined(self) -> None:
        text = "api_key = sk-mysecretkey12345678" + " padding" * 200  # noqa: S106
        result = redact_visible_text(text, max_chars=100)
        self.assertIn("api_key", result.classes)
        self.assertIn("truncated", result.classes)
        self.assertNotIn("sk-mysecretkey12345678", result.text)


