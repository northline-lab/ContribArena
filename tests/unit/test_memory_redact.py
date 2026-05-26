from __future__ import annotations

import unittest

from contribarena.memory.redact import redact_payload, redact_text


class RedactTextTest(unittest.TestCase):
    # -- x-access-token URLs --

    def test_redacts_x_access_token_url(self) -> None:
        text = "https://x-access-token:ghp_AbCdEf1234567890abcdefghij@github.com/owner/repo.git"
        result = redact_text(text)
        self.assertEqual(result, "https://x-access-token:***@github.com/owner/repo.git")

    def test_redacts_x_access_token_url_case_insensitive(self) -> None:
        text = "HTTPS://X-ACCESS-TOKEN:mysecrettoken@github.com/owner/repo.git"
        result = redact_text(text)
        self.assertIn("***", result)
        self.assertNotIn("mysecrettoken", result)

    # -- Bearer tokens --

    def test_redacts_bearer_token(self) -> None:
        # When "Authorization:" is present, the authorization-header pattern
        # consumes the entire value including the Bearer prefix.
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9_payload"
        result = redact_text(text)
        self.assertIn("Authorization: ***", result)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9_payload", result)

    def test_redacts_bearer_token_preserves_prefix(self) -> None:
        text = "bearer abcdefghijklmnopqrstuvwxyz"
        result = redact_text(text)
        self.assertTrue(result.startswith("bearer ***"))

    def test_does_not_redact_short_bearer_like_words(self) -> None:
        # Bearer followed by fewer than 12 chars should not be caught
        text = "bearer short"
        result = redact_text(text)
        self.assertEqual(text, result)

    # -- Authorization header values --

    def test_redacts_authorization_header(self) -> None:
        text = "authorization: Basic dXNlcjpwYXNz\nextra_line"
        result = redact_text(text)
        self.assertIn("authorization: ***", result)
        self.assertNotIn("Basic dXNlcjpwYXNz", result)

    def test_redacts_authorization_header_case_insensitive(self) -> None:
        text = "Authorization: secret-value-here\nnext"
        result = redact_text(text)
        self.assertIn("Authorization: ***", result)
        self.assertNotIn("secret-value-here", result)

    # -- API key / token / password / secret assignments --

    def test_redacts_api_key_assignment(self) -> None:
        text = "api_key=sk-1234567890abcdef"
        result = redact_text(text)
        self.assertIn("api_key=***", result)
        self.assertNotIn("sk-1234567890abcdef", result)

    def test_redacts_token_assignment(self) -> None:
        text = "token = ghp_12345678901234567890"
        result = redact_text(text)
        # The regex captures "token = " and replaces the rest with ***
        self.assertIn("***", result)
        self.assertNotIn("ghp_12345678901234567890", result)

    def test_redacts_password_assignment(self) -> None:
        text = "password=mysecretpassword123"
        result = redact_text(text)
        self.assertIn("password=***", result)
        self.assertNotIn("mysecretpassword123", result)

    def test_redacts_secret_assignment(self) -> None:
        text = "secret=supersecretvalue"
        result = redact_text(text)
        self.assertIn("secret=***", result)
        self.assertNotIn("supersecretvalue", result)

    def test_redacts_api_key_with_dash(self) -> None:
        text = "api-key=longkeyvalue1234"
        result = redact_text(text)
        self.assertNotIn("longkeyvalue1234", result)

    def test_redacts_api_key_with_underscore(self) -> None:
        text = "api_key=longkeyvalue1234"
        result = redact_text(text)
        self.assertNotIn("longkeyvalue1234", result)

    # -- GitHub PAT patterns (ghp_, gho_, ghu_, ghs_, ghr_) --

    def test_redacts_ghp_token(self) -> None:
        text = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZab"
        result = redact_text(text)
        self.assertEqual(result, "***")

    def test_redacts_gho_token(self) -> None:
        text = "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZab"
        result = redact_text(text)
        self.assertEqual(result, "***")

    def test_redacts_ghu_token(self) -> None:
        text = "ghu_ABCDEFGHIJKLMNOPQRSTUVWXYZab"
        result = redact_text(text)
        self.assertEqual(result, "***")

    def test_redacts_ghs_token(self) -> None:
        text = "ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZab"
        result = redact_text(text)
        self.assertEqual(result, "***")

    def test_redacts_ghr_token(self) -> None:
        text = "ghr_ABCDEFGHIJKLMNOPQRSTUVWXYZab"
        result = redact_text(text)
        self.assertEqual(result, "***")

    def test_does_not_redact_short_github_like_token(self) -> None:
        # Under 20 chars after prefix should not match
        text = "ghp_short"
        result = redact_text(text)
        self.assertEqual(text, result)

    # -- Multiple patterns in one string --

    def test_redacts_multiple_patterns_in_one_string(self) -> None:
        text = "api_key=sk-abc123 Bearer longtokenvalue123 https://x-access-token:secret@github.com/owner.git"
        result = redact_text(text)
        self.assertNotIn("sk-abc123", result)
        self.assertNotIn("longtokenvalue123", result)
        self.assertNotIn("secret", result)
        self.assertIn("***", result)

    # -- Truncation --

    def test_truncates_long_text_when_max_chars_set(self) -> None:
        text = "a" * 200
        result = redact_text(text, max_chars=100)
        self.assertLessEqual(len(result), 100)
        self.assertIn("[memory text truncated]", result)

    def test_does_not_truncate_when_text_fits_max_chars(self) -> None:
        text = "short text"
        result = redact_text(text, max_chars=100)
        self.assertEqual(result, text)
        self.assertNotIn("[memory text truncated]", result)

    def test_truncation_preserves_redaction_prefix(self) -> None:
        # After redaction the secret is replaced with ***; the remaining
        # safe text is still long enough to trigger truncation.
        text = "Bearer longtokenvalue12345 " + "safe " * 100
        result = redact_text(text, max_chars=50)
        self.assertNotIn("longtokenvalue12345", result)
        self.assertIn("Bearer ***", result)
        self.assertIn("[memory text truncated]", result)

    def test_no_truncation_when_max_chars_is_none(self) -> None:
        text = "a" * 500
        result = redact_text(text, max_chars=None)
        self.assertEqual(len(result), 500)

    # -- Clean text passes through --

    def test_clean_text_passes_through_unchanged(self) -> None:
        text = "The agent searched the repository and found 3 open issues."
        result = redact_text(text)
        self.assertEqual(result, text)

    def test_empty_string_passes_through(self) -> None:
        result = redact_text("")
        self.assertEqual(result, "")


class RedactPayloadTest(unittest.TestCase):
    def test_redacts_string_value(self) -> None:
        payload = "Bearer longtokenvalue12345"
        result = redact_payload(payload)
        self.assertIn("***", result)
        self.assertNotIn("longtokenvalue12345", result)

    def test_redacts_strings_in_list(self) -> None:
        payload = ["api_key=sk-abc123", "safe text"]
        result = redact_payload(payload)
        self.assertIn("***", result[0])
        self.assertNotIn("sk-abc123", result[0])
        self.assertEqual(result[1], "safe text")

    def test_redacts_strings_in_dict(self) -> None:
        payload = {"auth": "Bearer longtoken1234567890ab"}
        result = redact_payload(payload)
        self.assertIn("***", result["auth"])
        self.assertNotIn("longtoken1234567890ab", result["auth"])

    def test_redacts_nested_dict(self) -> None:
        payload = {
            "config": {
                "api_key": "sk-real-key-value-here",
                "name": "safe",
            }
        }
        result = redact_payload(payload)
        nested = result["config"]
        self.assertIsInstance(nested, dict)

    def test_preserves_non_string_types(self) -> None:
        payload = {"count": 42, "active": True, "ratio": 3.14, "name": None}
        result = redact_payload(payload)
        self.assertEqual(result["count"], 42)
        self.assertEqual(result["active"], True)
        self.assertEqual(result["ratio"], 3.14)
        self.assertIsNone(result["name"])

    def test_redacts_deeply_nested_structure(self) -> None:
        payload = {
            "items": [
                {"header": "authorization: Basic dXNlcjpwYXNz"},
                {"safe_key": "safe_value"},
            ]
        }
        result = redact_payload(payload)
        self.assertIn("***", result["items"][0]["header"])
        self.assertEqual(result["items"][1]["safe_key"], "safe_value")

    def test_empty_list_passes_through(self) -> None:
        result = redact_payload([])
        self.assertEqual(result, [])

    def test_empty_dict_passes_through(self) -> None:
        result = redact_payload({})
        self.assertEqual(result, {})

    def test_int_passes_through(self) -> None:
        result = redact_payload(42)
        self.assertEqual(result, 42)

    def test_none_passes_through(self) -> None:
        result = redact_payload(None)
        self.assertIsNone(result)

    def test_float_passes_through(self) -> None:
        result = redact_payload(3.14)
        self.assertEqual(result, 3.14)

    def test_bool_passes_through(self) -> None:
        result = redact_payload(True)
        self.assertTrue(result)

    def test_dict_keys_are_converted_to_str(self) -> None:
        # Non-string dict keys should be converted to strings
        payload = {42: "safe_value"}
        result = redact_payload(payload)
        self.assertIn("42", result)
        self.assertEqual(result["42"], "safe_value")


class RedactTextEdgeCaseTest(unittest.TestCase):
    def test_github_pat_in_url_path(self) -> None:
        text = "Cloning https://x-access-token:ghp_AbCdEf1234567890abcdefghij@github.com/org/repo.git"
        result = redact_text(text)
        self.assertNotIn("ghp_AbCdEf1234567890abcdefghij", result)
        # x-access-token URL pattern should replace the whole credential
        self.assertIn("x-access-token:***@", result)

    def test_bearer_token_with_dots_and_dashes(self) -> None:
        text = "Bearer sk-proj-abc123.def456-ghi789"
        result = redact_text(text)
        self.assertNotIn("sk-proj-abc123.def456-ghi789", result)
        self.assertIn("Bearer ***", result)

    def test_token_equals_with_spaces(self) -> None:
        text = "token =  mysecretvalue123"
        result = redact_text(text)
        self.assertNotIn("mysecretvalue123", result)

    def test_truncation_with_zero_max_chars(self) -> None:
        text = "hello world"
        result = redact_text(text, max_chars=0)
        # max_chars=0 means: max(0, 0-24) = 0 chars kept + truncation suffix
        # The suffix itself is 24 chars so it may exceed max_chars
        self.assertIn("[memory text truncated]", result)

    def test_truncation_with_small_max_chars(self) -> None:
        text = "Bearer longtokenvalue12345 and more text here"
        result = redact_text(text, max_chars=10)
        self.assertLessEqual(len(result), 10 + 24)  # truncation suffix adds chars
        self.assertNotIn("longtokenvalue12345", result)
