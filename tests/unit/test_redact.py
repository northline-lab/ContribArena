from __future__ import annotations

import unittest

from contribarena.memory.redact import redact_payload, redact_text


class RedactTextTest(unittest.TestCase):
    def test_plain_text_is_unmodified(self) -> None:
        self.assertEqual("hello world", redact_text("hello world"))

    def test_redacts_github_personal_access_token(self) -> None:
        text = "pushed using token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"
        result = redact_text(text)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234", result)
        self.assertIn("***", result)

    def test_redacts_github_oauth_token(self) -> None:
        text = "auth with gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        result = redact_text(text)
        self.assertNotIn("gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", result)
        self.assertIn("***", result)

    def test_redacts_github_user_to_server_token(self) -> None:
        text = "token ghu_ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678"
        result = redact_text(text)
        self.assertNotIn("ghu_ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678", result)
        self.assertIn("***", result)

    def test_redacts_github_server_to_server_token(self) -> None:
        text = "token ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678"
        result = redact_text(text)
        self.assertNotIn("ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678", result)
        self.assertIn("***", result)

    def test_redacts_github_refresh_token(self) -> None:
        text = "token ghr_ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678"
        result = redact_text(text)
        self.assertNotIn("ghr_ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678", result)
        self.assertIn("***", result)

    def test_redacts_x_access_token_url(self) -> None:
        text = "https://x-access-token:mysecrettoken@github.com/owner/repo"
        result = redact_text(text)
        self.assertNotIn("mysecrettoken", result)
        self.assertEqual("https://x-access-token:***@github.com/owner/repo", result)

    def test_redacts_authorization_header(self) -> None:
        text = "authorization: Basic dXNlcjpwYXNz\nnext line"
        result = redact_text(text)
        self.assertNotIn("dXNlcjpwYXNz", result)
        self.assertIn("authorization: ***", result)
        self.assertIn("\nnext line", result)

    def test_redacts_standalone_bearer_token(self) -> None:
        text = "bearer abcdefghijklmnopqrstuvwxyz1234"
        result = redact_text(text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz1234", result)
        self.assertIn("bearer ***", result)

    def test_authorization_header_overrides_bearer(self) -> None:
        text = "Authorization: bearer abcdefghijklmnopqrstuvwxyz1234"
        result = redact_text(text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz1234", result)
        # The authorization: pattern matches first and replaces the whole line
        self.assertIn("Authorization: ***", result)

    def test_redacts_api_key_assignment(self) -> None:
        text = "api_key = sk-mysecretkey123"
        result = redact_text(text)
        self.assertNotIn("sk-mysecretkey123", result)
        self.assertIn("api_key = ***", result)

    def test_redacts_token_assignment(self) -> None:
        text = "token = ghp_abc123def456ghi789jkl012mno345"
        result = redact_text(text)
        self.assertNotIn("ghp_abc123def456ghi789jkl012mno345", result)
        self.assertIn("***", result)

    def test_redacts_password_assignment(self) -> None:
        text = "password = mysecretpassword123"
        result = redact_text(text)
        self.assertNotIn("mysecretpassword123", result)
        self.assertIn("password = ***", result)

    def test_redacts_secret_assignment(self) -> None:
        text = "secret = mysecretvalue"
        result = redact_text(text)
        self.assertNotIn("mysecretvalue", result)
        self.assertIn("secret = ***", result)

    def test_short_bearer_token_is_not_redacted(self) -> None:
        text = "bearer short"
        result = redact_text(text)
        # "short" has only 5 chars, below the 12-char minimum in the regex
        self.assertEqual("bearer short", result)

    def test_short_github_token_prefix_is_not_redacted(self) -> None:
        text = "ghp_short"
        result = redact_text(text)
        # "short" has only 5 chars, below the 20-char minimum in the regex
        self.assertEqual("ghp_short", result)

    def test_max_chars_truncates_long_text(self) -> None:
        text = "a" * 200
        result = redact_text(text, max_chars=100)
        self.assertTrue(result.endswith("[memory text truncated]"))
        self.assertLess(len(result), 200)

    def test_max_chars_keeps_short_text(self) -> None:
        text = "hello"
        result = redact_text(text, max_chars=100)
        self.assertEqual("hello", result)

    def test_redacts_multiple_tokens_in_same_text(self) -> None:
        text = "token=ghp_abc123def456ghi789jkl012mno345 and password=s3cret"
        result = redact_text(text)
        self.assertNotIn("ghp_abc123def456ghi789jkl012mno345", result)
        self.assertNotIn("s3cret", result)
        # Both should be replaced with ***
        self.assertEqual(result.count("***"), 2)


class RedactPayloadTest(unittest.TestCase):
    def test_redacts_string_with_password(self) -> None:
        result = redact_payload("password = secret123")
        self.assertNotIn("secret123", result)
        self.assertIn("password = ***", result)

    def test_passthrough_int_values(self) -> None:
        self.assertEqual(42, redact_payload(42))

    def test_passthrough_float_values(self) -> None:
        self.assertEqual(3.14, redact_payload(3.14))

    def test_passthrough_bool_values(self) -> None:
        self.assertEqual(True, redact_payload(True))

    def test_passthrough_none_value(self) -> None:
        self.assertIsNone(redact_payload(None))

    def test_redacts_strings_in_list(self) -> None:
        payload = ["clean text", "token = mysecretkey"]
        result = redact_payload(payload)
        self.assertEqual("clean text", result[0])
        self.assertNotIn("mysecretkey", result[1])
        self.assertIn("***", result[1])

    def test_redacts_strings_in_dict(self) -> None:
        payload = {"name": "project", "auth": "bearer abcdefghijklmnop12345"}
        result = redact_payload(payload)
        self.assertEqual("project", result["name"])
        self.assertNotIn("abcdefghijklmnop12345", result["auth"])
        self.assertIn("***", result["auth"])

    def test_redacts_nested_payload_with_assignment_format(self) -> None:
        payload = {
            "config": {
                "api_key": "api_key = sk-mysecretkey12345678",
                "port": 8080,
            },
            "tokens": ["ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"],
        }
        result = redact_payload(payload)
        config = result["config"]
        self.assertNotIn("sk-mysecretkey12345678", config["api_key"])
        self.assertIn("api_key = ***", config["api_key"])
        self.assertEqual(8080, config["port"])
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234", result["tokens"][0])

    def test_converts_dict_keys_to_strings(self) -> None:
        payload = {1: "value"}
        result = redact_payload(payload)
        self.assertIn("1", result)
        self.assertIsInstance(list(result.keys())[0], str)


class RedactEdgeCaseTest(unittest.TestCase):
    def test_empty_string_is_unmodified(self) -> None:
        self.assertEqual("", redact_text(""))

    def test_url_without_token_is_unmodified(self) -> None:
        self.assertEqual("https://github.com/owner/repo", redact_text("https://github.com/owner/repo"))

    def test_x_access_token_case_insensitive(self) -> None:
        text = "https://X-ACCESS-TOKEN:mysecret@github.com/owner/repo"
        result = redact_text(text)
        self.assertNotIn("mysecret", result)

    def test_bearer_case_insensitive(self) -> None:
        text = "BEARER abcdefghijklmnopqrstuvwxyz1234"
        result = redact_text(text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz1234", result)

    def test_empty_list_payload(self) -> None:
        self.assertEqual([], redact_payload([]))

    def test_empty_dict_payload(self) -> None:
        self.assertEqual({}, redact_payload({}))


if __name__ == "__main__":
    unittest.main()
