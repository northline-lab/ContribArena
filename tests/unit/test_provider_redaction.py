from __future__ import annotations

import unittest

from contribarena.providers.redaction import RedactedText, redact_visible_text


class RedactVisibleTextTest(unittest.TestCase):
    # -- GitHub PAT prefixes --

    def test_redacts_ghp_token(self) -> None:
        result = redact_visible_text("https://ghp_AbCdEf1234567890abcdefghij@github.com")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)
        self.assertNotIn("ghp_AbCdEf1234567890abcdefghij", result.text)

    def test_redacts_gho_token(self) -> None:
        result = redact_visible_text("gho_12345678901234567890")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)
        self.assertNotIn("gho_12345678901234567890", result.text)

    def test_redacts_ghu_token(self) -> None:
        result = redact_visible_text("ghu_AbCdEf1234567890abcdefghij")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)

    def test_redacts_ghs_token(self) -> None:
        result = redact_visible_text("ghs_AbCdEf1234567890abcdefghij")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)

    def test_redacts_ghr_token(self) -> None:
        result = redact_visible_text("ghr_AbCdEf1234567890abcdefghij")
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)

    def test_does_not_redact_short_ghp_prefix(self) -> None:
        result = redact_visible_text("ghp_short")
        self.assertFalse(result.redacted)
        self.assertEqual(result.text, "ghp_short")

    # -- Bearer tokens --

    def test_redacts_bearer_token(self) -> None:
        result = redact_visible_text("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9_payload")
        self.assertTrue(result.redacted)
        self.assertIn("bearer_token", result.classes)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9_payload", result.text)

    def test_redacts_lowercase_bearer(self) -> None:
        result = redact_visible_text("bearer abcdefghijklmnopqrstuvwxyz")
        self.assertTrue(result.redacted)
        self.assertIn("bearer_token", result.classes)

    def test_does_not_redact_short_bearer(self) -> None:
        result = redact_visible_text("bearer short")
        self.assertFalse(result.redacted)
        self.assertEqual(result.text, "bearer short")

    # -- API key / token / secret assignments --

    def test_redacts_api_key_assignment(self) -> None:
        result = redact_visible_text("api_key=sk-1234567890abcdef")
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)
        self.assertNotIn("sk-1234567890abcdef", result.text)

    def test_redacts_token_assignment(self) -> None:
        result = redact_visible_text("token=abcdefghijklmnop")
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)
        self.assertNotIn("abcdefghijklmnop", result.text)

    def test_redacts_secret_assignment(self) -> None:
        result = redact_visible_text("secret=mysecretvalue12345")
        self.assertTrue(result.redacted)
        self.assertIn("api_key", result.classes)

    # -- x-access-token URLs --

    def test_redacts_x_access_token_url(self) -> None:
        # When a GitHub PAT appears in the x-access-token URL, the github_token
        # and api_key patterns consume the secret first. The key invariant is
        # that no raw secret survives in the output.
        result = redact_visible_text("https://x-access-token:ghp_AbCdEf1234567890abcdefghij@github.com")
        self.assertTrue(result.redacted)
        self.assertNotIn("ghp_AbCdEf1234567890abcdefghij", result.text)

    def test_redacts_x_access_token_url_with_non_pat_secret(self) -> None:
        # Non-GH-PAT secrets longer than 12 chars in x-access-token URLs are
        # caught by the api_key pattern ("token:...").
        result = redact_visible_text("https://x-access-token:verylongsecretvalue@github.com")
        self.assertTrue(result.redacted)
        self.assertNotIn("verylongsecretvalue", result.text)

    # -- Truncation --

    def test_truncates_long_text(self) -> None:
        long_text = "A" * 1000
        result = redact_visible_text(long_text, max_chars=100)
        self.assertTrue(result.redacted)
        self.assertIn("truncated", result.classes)
        self.assertLessEqual(len(result.text), 100)
        self.assertIn("[truncated]", result.text)

    def test_does_not_truncate_short_text(self) -> None:
        result = redact_visible_text("short text", max_chars=100)
        self.assertFalse(result.redacted)
        self.assertEqual(result.text, "short text")

    # -- Clean text --

    def test_clean_text_not_redacted(self) -> None:
        result = redact_visible_text("Hello, this is safe public text.")
        self.assertFalse(result.redacted)
        self.assertEqual(result.text, "Hello, this is safe public text.")
        self.assertEqual(result.classes, [])

    def test_empty_string(self) -> None:
        result = redact_visible_text("")
        self.assertFalse(result.redacted)
        self.assertEqual(result.text, "")

    # -- Multiple patterns --

    def test_multiple_patterns_detected(self) -> None:
        text = "ghp_AbCdEf1234567890abcdefghij Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9_extra"
        result = redact_visible_text(text)
        self.assertTrue(result.redacted)
        self.assertIn("github_token", result.classes)
        self.assertIn("bearer_token", result.classes)

    # -- RedactedText dataclass --

    def test_redacted_text_default(self) -> None:
        r = RedactedText(text="hello")
        self.assertEqual(r.text, "hello")
        self.assertFalse(r.redacted)
        self.assertEqual(r.classes, [])

    def test_redacted_text_with_classes(self) -> None:
        r = RedactedText(text="***", redacted=True, classes=["github_token"])
        self.assertTrue(r.redacted)
        self.assertIn("github_token", r.classes)


if __name__ == "__main__":
    unittest.main()
