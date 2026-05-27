from __future__ import annotations

import unittest

from contribarena.providers.tracing import (
    ModelUsageAccumulator,
    _classify_model_runtime_error,
    _error_message,
    _safe_error_message,
    _should_retry_model_runtime_error,
    _status_code_from_error_message,
    _usage_int,
)


class UsageIntTest(unittest.TestCase):
    """Tests for _usage_int coercion helper."""

    def test_none_returns_zero(self) -> None:
        self.assertEqual(0, _usage_int(None))

    def test_string_returns_zero(self) -> None:
        self.assertEqual(0, _usage_int("abc"))

    def test_int_passthrough(self) -> None:
        self.assertEqual(42, _usage_int(42))

    def test_float_truncated(self) -> None:
        self.assertEqual(7, _usage_int(7.9))

    def test_zero(self) -> None:
        self.assertEqual(0, _usage_int(0))

    def test_negative_int(self) -> None:
        self.assertEqual(-3, _usage_int(-3))

    def test_bool_true(self) -> None:
        self.assertEqual(1, _usage_int(True))

    def test_bool_false(self) -> None:
        self.assertEqual(0, _usage_int(False))

    def test_empty_dict_returns_zero(self) -> None:
        self.assertEqual(0, _usage_int({}))

    def test_list_returns_zero(self) -> None:
        self.assertEqual(0, _usage_int([1, 2]))


class ModelUsageAccumulatorTest(unittest.TestCase):
    """Tests for ModelUsageAccumulator token counting."""

    def test_initial_zero(self) -> None:
        acc = ModelUsageAccumulator()
        self.assertEqual(0, acc.requests)
        self.assertEqual(0, acc.input_tokens)
        self.assertEqual(0, acc.output_tokens)
        self.assertEqual(0, acc.total_tokens)

    def test_snapshot_initial(self) -> None:
        acc = ModelUsageAccumulator()
        self.assertEqual(
            {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            acc.snapshot(),
        )

    def test_add_single_usage(self) -> None:
        acc = ModelUsageAccumulator()
        acc.add({"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        self.assertEqual(1, acc.requests)
        self.assertEqual(10, acc.input_tokens)
        self.assertEqual(5, acc.output_tokens)
        self.assertEqual(15, acc.total_tokens)

    def test_add_accumulates(self) -> None:
        acc = ModelUsageAccumulator()
        acc.add({"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        acc.add({"requests": 1, "input_tokens": 20, "output_tokens": 8, "total_tokens": 28})
        self.assertEqual(2, acc.requests)
        self.assertEqual(30, acc.input_tokens)
        self.assertEqual(13, acc.output_tokens)
        self.assertEqual(43, acc.total_tokens)

    def test_add_missing_keys_default_zero(self) -> None:
        acc = ModelUsageAccumulator()
        acc.add({"requests": 1})
        self.assertEqual(1, acc.requests)
        self.assertEqual(0, acc.input_tokens)
        self.assertEqual(0, acc.output_tokens)
        self.assertEqual(0, acc.total_tokens)

    def test_add_empty_dict(self) -> None:
        acc = ModelUsageAccumulator()
        acc.add({})
        self.assertEqual(0, acc.requests)
        self.assertEqual(0, acc.input_tokens)

    def test_snapshot_after_add(self) -> None:
        acc = ModelUsageAccumulator()
        acc.add({"requests": 2, "input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
        snap = acc.snapshot()
        self.assertEqual(2, snap["requests"])
        self.assertEqual(100, snap["input_tokens"])
        self.assertEqual(50, snap["output_tokens"])
        self.assertEqual(150, snap["total_tokens"])

    def test_independent_instances(self) -> None:
        a = ModelUsageAccumulator()
        b = ModelUsageAccumulator()
        a.add({"requests": 5, "input_tokens": 50, "output_tokens": 25, "total_tokens": 75})
        self.assertEqual(0, b.requests)
        self.assertEqual(0, b.input_tokens)


class SafeErrorMessageTest(unittest.TestCase):
    """Tests for _safe_error_message secret redaction."""

    def test_plain_text_passthrough(self) -> None:
        self.assertEqual("rate limited", _safe_error_message("rate limited"))

    def test_redacts_github_pat(self) -> None:
        result = _safe_error_message("token github_pat_abc123XYZ failed")
        self.assertIn("github_pat_[REDACTED]", result)
        self.assertNotIn("abc123XYZ", result)

    def test_redacts_ghp_token(self) -> None:
        result = _safe_error_message("auth ghp_SecretValue123 rejected")
        self.assertIn("ghp_[REDACTED]", result)
        self.assertNotIn("SecretValue123", result)

    def test_redacts_sk_key(self) -> None:
        result = _safe_error_message("key sk-abcdef1234567890 expired")
        self.assertIn("sk-[REDACTED]", result)
        self.assertNotIn("abcdef1234567890", result)

    def test_truncates_at_500_chars(self) -> None:
        long_msg = "x" * 1000
        result = _safe_error_message(long_msg)
        self.assertEqual(500, len(result))

    def test_empty_string(self) -> None:
        self.assertEqual("", _safe_error_message(""))

    def test_exactly_500_chars(self) -> None:
        msg = "a" * 500
        self.assertEqual(msg, _safe_error_message(msg))

    def test_multiple_secrets_redacted(self) -> None:
        result = _safe_error_message("ghp_AAA and github_pat_BBB and sk-CCC")
        self.assertNotIn("AAA", result)
        self.assertNotIn("BBB", result)
        self.assertNotIn("CCC", result)
        self.assertIn("ghp_[REDACTED]", result)
        self.assertIn("github_pat_[REDACTED]", result)
        self.assertIn("sk-[REDACTED]", result)


class ErrorMessageTest(unittest.TestCase):
    """Tests for _error_message exception formatting."""

    def test_exception_with_message(self) -> None:
        result = _error_message(ValueError("bad input"))
        self.assertEqual("bad input", result)

    def test_exception_empty_message_uses_class_name(self) -> None:
        result = _error_message(ValueError())
        self.assertEqual("ValueError", result)

    def test_exception_strips_whitespace(self) -> None:
        result = _error_message(RuntimeError("  spaced  "))
        self.assertEqual("spaced", result)

    def test_exception_redacts_secrets(self) -> None:
        result = _error_message(RuntimeError("auth ghp_Secret rejected"))
        self.assertIn("ghp_[REDACTED]", result)
        self.assertNotIn("Secret", result)


class StatusCodeFromErrorMessageTest(unittest.TestCase):
    """Tests for _status_code_from_error_message HTTP code extraction."""

    def test_no_status_code(self) -> None:
        self.assertIsNone(_status_code_from_error_message("some random error"))

    def test_bare_400(self) -> None:
        self.assertEqual(400, _status_code_from_error_message("error 400 bad request"))

    def test_bare_429(self) -> None:
        self.assertEqual(429, _status_code_from_error_message("rate limit 429"))

    def test_bare_500(self) -> None:
        self.assertEqual(500, _status_code_from_error_message("server error 500"))

    def test_bare_502(self) -> None:
        self.assertEqual(502, _status_code_from_error_message("bad gateway 502"))

    def test_bare_503(self) -> None:
        self.assertEqual(503, _status_code_from_error_message("unavailable 503"))

    def test_bare_504(self) -> None:
        self.assertEqual(504, _status_code_from_error_message("timeout 504"))

    def test_http_prefix(self) -> None:
        self.assertEqual(401, _status_code_from_error_message("http 401 unauthorized"))

    def test_unrecognized_code(self) -> None:
        self.assertIsNone(_status_code_from_error_message("error 418 teapot"))

    def test_empty_string(self) -> None:
        self.assertIsNone(_status_code_from_error_message(""))

    def test_code_embedded_in_text(self) -> None:
        self.assertEqual(408, _status_code_from_error_message("request timed out with http408"))


class ClassifyModelRuntimeErrorTest(unittest.TestCase):
    """Tests for _classify_model_runtime_error error classification."""

    def test_duplicate_tool_call_id(self) -> None:
        exc = RuntimeError("duplicate tool_call_id found")
        self.assertEqual("duplicate_tool_call_id", _classify_model_runtime_error(exc))

    def test_unsupported_tool_format(self) -> None:
        exc = RuntimeError("unsupported tool format in request")
        self.assertEqual("unsupported_tool_format", _classify_model_runtime_error(exc))

    def test_invalid_tool_sequence(self) -> None:
        exc = RuntimeError("tool_calls must precede tool messages")
        self.assertEqual("invalid_tool_sequence", _classify_model_runtime_error(exc))

    def test_context_window_exceeded_length(self) -> None:
        exc = RuntimeError("context length exceeded")
        self.assertEqual("context_window_exceeded", _classify_model_runtime_error(exc))

    def test_context_window_exceeded_window(self) -> None:
        exc = RuntimeError("context window limit reached")
        self.assertEqual("context_window_exceeded", _classify_model_runtime_error(exc))

    def test_http_429(self) -> None:
        exc = RuntimeError("rate limited 429")
        self.assertEqual("http_429", _classify_model_runtime_error(exc))

    def test_http_500(self) -> None:
        exc = RuntimeError("server error 500")
        self.assertEqual("http_500", _classify_model_runtime_error(exc))

    def test_http_401(self) -> None:
        exc = RuntimeError("unauthorized 401")
        self.assertEqual("http_401", _classify_model_runtime_error(exc))

    def test_transport_transient_connection_error(self) -> None:
        exc = RuntimeError("connection error occurred")
        self.assertEqual("transport_transient", _classify_model_runtime_error(exc))

    def test_transport_transient_timeout(self) -> None:
        exc = RuntimeError("request timed out")
        self.assertEqual("transport_transient", _classify_model_runtime_error(exc))

    def test_transport_transient_socket_reset(self) -> None:
        exc = RuntimeError("socket reset by peer")
        self.assertEqual("transport_transient", _classify_model_runtime_error(exc))

    def test_transport_transient_dns(self) -> None:
        exc = RuntimeError("dns resolution failed")
        self.assertEqual("transport_transient", _classify_model_runtime_error(exc))

    def test_uncertain_provider_error_5xx(self) -> None:
        # _status_code_from_error_message only recognises a fixed set of codes,
        # so 599 is not extracted and the error falls through to provider_error.
        exc = RuntimeError("unexpected server error 599")
        self.assertEqual("provider_error", _classify_model_runtime_error(exc))

    def test_provider_error_fallback(self) -> None:
        exc = RuntimeError("something completely unknown")
        self.assertEqual("provider_error", _classify_model_runtime_error(exc))

    def test_duplicate_takes_priority_over_status_code(self) -> None:
        exc = RuntimeError("duplicate tool_call_id in 400 response")
        self.assertEqual("duplicate_tool_call_id", _classify_model_runtime_error(exc))


class ShouldRetryModelRuntimeErrorTest(unittest.TestCase):
    """Tests for _should_retry_model_runtime_error retry policy."""

    def test_http_429_retriable(self) -> None:
        self.assertTrue(_should_retry_model_runtime_error("http_429", 0))

    def test_http_500_retriable(self) -> None:
        self.assertTrue(_should_retry_model_runtime_error("http_500", 0))

    def test_http_502_retriable(self) -> None:
        self.assertTrue(_should_retry_model_runtime_error("http_502", 1))

    def test_transport_transient_retriable(self) -> None:
        self.assertTrue(_should_retry_model_runtime_error("transport_transient", 0))

    def test_max_attempts_exceeded(self) -> None:
        # _MAX_MODEL_ATTEMPTS is 4, so retries_attempted >= 3 means no retry
        self.assertFalse(_should_retry_model_runtime_error("http_500", 3))

    def test_provider_error_not_retriable(self) -> None:
        self.assertFalse(_should_retry_model_runtime_error("provider_error", 0))

    def test_duplicate_tool_call_id_not_retriable(self) -> None:
        self.assertFalse(_should_retry_model_runtime_error("duplicate_tool_call_id", 0))

    def test_http_401_not_retriable(self) -> None:
        self.assertFalse(_should_retry_model_runtime_error("http_401", 0))

    def test_uncertain_provider_error_first_retry(self) -> None:
        self.assertTrue(_should_retry_model_runtime_error("uncertain_provider_error", 0))

    def test_uncertain_provider_error_second_retry(self) -> None:
        self.assertFalse(_should_retry_model_runtime_error("uncertain_provider_error", 1))

    def test_http_408_retriable(self) -> None:
        self.assertTrue(_should_retry_model_runtime_error("http_408", 0))

    def test_http_503_retriable(self) -> None:
        self.assertTrue(_should_retry_model_runtime_error("http_503", 2))

    def test_http_504_retriable(self) -> None:
        self.assertTrue(_should_retry_model_runtime_error("http_504", 1))


if __name__ == "__main__":
    unittest.main()
