from __future__ import annotations

import unittest

from contribarena.providers.redaction import RedactedText, redact_visible_text


class RedactedTextDefaultsTest(unittest.TestCase):
    def test_default_redacted_is_false(self) -> None:
        result = RedactedText(text="hello")
        self.assertFalse(result.redacted)

    def test_default_classes_is_empty(self) -> None:
        result = RedactedText(text="hello")
        self.assertEqual(result.classes, [])


class NoSecretFoundTest(unittest.TestCase):
    def test_plain_text_is_not_redacted(self) -> None:
        result = redact_visible_text("hello world")
        self.assertFalse(result.redacted)
        self.assertEqual(result.classes, [])
        self.assertEqual(result.text, "hello world")

    def test_empty_string_strips_to_nothing(self) -> None:
        result = redact_visible_text("")
        self.assertFalse(result.redacted)
        self.assertEqual(result.text, "")

    def test_whitespace_only_strips(self) -> None:
        result = redact_visible_text("   \n  ")
        self.assertFalse(result.redacted)
        self.assertEqual(result.text, "")


class GithubTokenRedactionTest(unittest.TestCase):
    def test_classic_token_is_redacted(self) -> None:
        result = redact_visible_text("token: ghp_1234567890abcdefghij")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)
        self.assertNotIn("ghp_1234567890abcdefghij", result.text)
        self.assertIn("***", result.text)

    def test_github_oauth_token_is_redacted(self) -> None:
        result = redact_visible_text("gho_abcdefghijklmnopqrstuvwxyz")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)

    def test_github_user_token_is_redacted(self) -> None:
        result = redact_visible_text("ghu_abcdefghijklmnopqrstuvwxyz")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)

    def test_github_server_to_server_token_is_redacted(self) -> None:
        result = redact_visible_text("ghs_abcdefghijklmnopqrstuvwxyz")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)

    def test_github_refresh_token_is_redacted(self) -> None:
        result = redact_visible_text("ghr_abcdefghijklmnopqrstuvwxyz")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)

    def test_short_github_token_not_matched(self) -> None:
        result = redact_visible_text("ghp_short")
        self.assertFalse(result.redacted)

    def test_github_token_inside_text(self) -> None:
        result = redact_visible_text("prefix ghp_1234567890abcdefghij suffix")
        self.assertTrue(result.redacted)
        self.assertNotIn("ghp_1234567890abcdefghij", result.text)


class BearerTokenRedactionTest(unittest.TestCase):
    def test_bearer_token_is_redacted(self) -> None:
        result = redact_visible_text("Authorization: Bearer abcdefghijklmnop")
        self.assertTrue(result.redacted)
        self.assertIn("bearer_token", result.classes)
        self.assertNotIn("abcdefghijklmnop", result.text)

    def test_bearer_case_insensitive(self) -> None:
        result = redact_visible_text("Authorization: bearer abcdefghijklmnop")
        self.assertTrue(result.redacted)
        self.assertIn("bearer_token", result.classes)

    def test_short_bearer_not_matched(self) -> None:
        result = redact_visible_text("Authorization: Bearer short")
        self.assertFalse(result.redacted)

    def test_bearer_with_dots_and_dashes(self) -> None:
        result = redact_visible_text("Bearer abc.def-ghi_jkl.mno-pqr")
        self.assertTrue(result.redacted)
        self.assertIn("bearer_token", result.classes)


class ApiKeyRedactionTest(unittest.TestCase):
    def test_api_key_equals_quoted(self) -> None:
        result = redact_visible_text('api_key="abcdef1234567890"')
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)
        self.assertNotIn("abcdef1234567890", result.text)

    def test_api_key_colon_unquoted(self) -> None:
        result = redact_visible_text("api-key: abcdef1234567890")
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)

    def test_secret_equals(self) -> None:
        result = redact_visible_text("secret=abcdef1234567890")
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)

    def test_token_colon(self) -> None:
        result = redact_visible_text("token: abcdef1234567890")
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)

    def test_api_key_case_insensitive(self) -> None:
        result = redact_visible_text("API_KEY = abcdef1234567890")
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)

    def test_short_api_key_not_matched(self) -> None:
        result = redact_visible_text("api_key = short")
        self.assertFalse(result.redacted)


class XAccessTokenUrlRedactionTest(unittest.TestCase):
    def test_x_access_token_url_is_redacted(self) -> None:
        # Use a short secret so the api_key pattern (< 12 chars after
        # `token:` / `key:` / `secret=`) does not intercept.
        result = redact_visible_text(
            "https://x-access-token:x@ab"
        )
        self.assertTrue(result.redacted)
        self.assertIn("x_access_token_url", result.classes)
        self.assertNotIn("x@ab", result.text)
        self.assertIn("https://x-access-token:***@", result.text)

    def test_x_access_token_preserves_domain(self) -> None:
        # With only 4 chars after the colon (x@ab), api_key does not
        # intercept. The x_access_token_url replacement preserves text
        # after @ so the domain survives.
        result = redact_visible_text(
            "url: https://x-access-token:x@ab"
        )
        self.assertIn("ab", result.text)
        self.assertIn("https://x-access-token:***@", result.text)

    def test_api_key_intercepts_long_secret_in_url(self) -> None:
        result = redact_visible_text(
            "https://x-access-token:longsecretvalue123@github.com"
        )
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)
        self.assertNotIn("longsecretvalue123", result.text)


class TruncationTest(unittest.TestCase):
    def test_long_text_is_truncated(self) -> None:
        long_text = "A" * 900
        result = redact_visible_text(long_text, max_chars=100)
        self.assertTrue(result.redacted)
        self.assertIn("truncated", result.classes)
        self.assertLessEqual(len(result.text), 100)
        self.assertTrue(result.text.endswith(" [truncated]"))

    def test_short_text_is_not_truncated(self) -> None:
        result = redact_visible_text("short text", max_chars=100)
        self.assertNotIn("truncated", result.classes)

    def test_default_max_chars_is_800(self) -> None:
        text = "B" * 801
        result = redact_visible_text(text)
        self.assertTrue(result.redacted)
        self.assertIn("truncated", result.classes)

    def test_truncated_length_respects_max_chars(self) -> None:
        text = "X" * 1000
        result = redact_visible_text(text, max_chars=50)
        self.assertLessEqual(len(result.text), 50)


class MultiplePatternsTest(unittest.TestCase):
    def test_multiple_secrets_in_one_text(self) -> None:
        text = (
            "Token: ghp_1234567890abcdefghij\n"
            "Authorization: Bearer abcdefghijklmnop\n"
            'api_key="abcdef1234567890"'
        )
        result = redact_visible_text(text)
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)
        self.assertIn("bearer_token", result.classes)
        self.assertIn("api_key", result.classes)
        self.assertNotIn("ghp_1234567890abcdefghij", result.text)
        self.assertNotIn("abcdefghijklmnop", result.text)
        self.assertNotIn("abcdef1234567890", result.text)

    def test_four_pattern_types_together(self) -> None:
        text = (
            "ghp_1234567890abcdefghij\n"
            "Bearer abcdefghijklmnop\n"
            "api_key=abcdef1234567890\n"
            "https://x-access-token:x@ab"
        )
        result = redact_visible_text(text)
        self.assertIn("github_token", result.classes)
        self.assertIn("bearer_token", result.classes)
        self.assertIn("api_key", result.classes)
        self.assertIn("x_access_token_url", result.classes)


class RedactionPreservesNonSecretTextTest(unittest.TestCase):
    def test_surrounding_text_preserved(self) -> None:
        text = "Hello ghp_1234567890abcdefghij World"
        result = redact_visible_text(text)
        self.assertIn("Hello", result.text)
        self.assertIn("World", result.text)

    def test_multiline_text_preserved(self) -> None:
        text = "Line 1\nLine 2\nLine 3"
        result = redact_visible_text(text)
        self.assertEqual(result.text, "Line 1\nLine 2\nLine 3")


if __name__ == "__main__":
    unittest.main()
