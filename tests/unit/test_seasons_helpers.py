"""Unit tests for pure helper functions in contribarena.engine.seasons.

Tests cover:
- normalize_model_identity: string normalization for model identifiers
- derive_participant_id: season:identity ID construction
- parse_duration_seconds: duration string parsing (s/m/h/d)
- _append_runtime_event: bounded runtime event list management
- SeasonAdmission: frozen dataclass defaults
- SeasonStatus literal: accepted/rejected values
- Module constants: MAX_REPLACEMENT_ATTEMPTS, MAX_LIVE_SUBMISSION_RETRY_ATTEMPTS
"""

from __future__ import annotations

import unittest

from contribarena.engine.seasons import (
    MAX_LIVE_SUBMISSION_RETRY_ATTEMPTS,
    MAX_REPLACEMENT_ATTEMPTS,
    SeasonAdmission,
    _append_runtime_event,
    derive_participant_id,
    normalize_model_identity,
    parse_duration_seconds,
)
from contribarena.errors import ConfigError


class NormalizeModelIdentityTest(unittest.TestCase):
    """Tests for normalize_model_identity."""

    def test_simple_name(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("gpt-4o"))

    def test_strip_whitespace(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("  gpt-4o  "))

    def test_lowercase(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("GPT-4O"))

    def test_remove_slash_prefix(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("openai/gpt-4o"))

    def test_remove_colon_prefix(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("provider:gpt-4o"))

    def test_remove_nested_slash(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("org/openai/gpt-4o"))

    def test_remove_nested_colon(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("tier:provider:gpt-4o"))

    def test_special_chars_replaced_with_hyphen(self) -> None:
        self.assertEqual("my-model", normalize_model_identity("my model"))

    def test_preserve_dots(self) -> None:
        # Dots in input are preserved, but spaces become hyphens first
        self.assertEqual("model-v2.0", normalize_model_identity("model v2.0"))
        # Pure dot-separated values keep their dots
        self.assertEqual("model.v2.0", normalize_model_identity("model.v2.0"))

    def test_preserve_underscores(self) -> None:
        self.assertEqual("my_model", normalize_model_identity("my_model"))

    def test_strip_leading_trailing_hyphens(self) -> None:
        self.assertEqual("model", normalize_model_identity("---model---"))

    def test_empty_string_returns_unknown(self) -> None:
        self.assertEqual("unknown", normalize_model_identity(""))

    def test_whitespace_only_returns_unknown(self) -> None:
        self.assertEqual("unknown", normalize_model_identity("   "))

    def test_special_chars_only_returns_unknown(self) -> None:
        self.assertEqual("unknown", normalize_model_identity("!!!"))

    def test_slash_only_returns_unknown(self) -> None:
        self.assertEqual("unknown", normalize_model_identity("///"))

    def test_colon_only_returns_unknown(self) -> None:
        self.assertEqual("unknown", normalize_model_identity(":::"))

    def test_slash_with_empty_suffix(self) -> None:
        self.assertEqual("unknown", normalize_model_identity("openai/"))

    def test_colon_with_empty_suffix(self) -> None:
        self.assertEqual("unknown", normalize_model_identity("provider:"))

    def test_mixed_nested_prefixes_slash_and_colon(self) -> None:
        # Slash is stripped first, then colon from the remaining part
        self.assertEqual("gpt-4o", normalize_model_identity("org/provider:gpt-4o"))


class DeriveParticipantIdTest(unittest.TestCase):
    """Tests for derive_participant_id."""

    def test_basic_derivation(self) -> None:
        self.assertEqual("season_0:gpt-4o", derive_participant_id("season_0", "gpt-4o"))

    def test_model_normalization_applied(self) -> None:
        self.assertEqual("season_0:gpt-4o", derive_participant_id("season_0", "OpenAI/gpt-4o"))

    def test_whitespace_model_normalized(self) -> None:
        self.assertEqual("season_0:gpt-4o", derive_participant_id("season_0", "  gpt-4o  "))

    def test_empty_model_uses_unknown(self) -> None:
        self.assertEqual("season_0:unknown", derive_participant_id("season_0", ""))

    def test_season_id_preserved_exact(self) -> None:
        self.assertEqual("my-season:glm-5.1", derive_participant_id("my-season", "glm-5.1"))

    def test_colon_in_season_id(self) -> None:
        self.assertEqual("s0:gpt-4o", derive_participant_id("s0", "gpt-4o"))


class ParseDurationSecondsTest(unittest.TestCase):
    """Tests for parse_duration_seconds."""

    def test_seconds(self) -> None:
        self.assertEqual(30, parse_duration_seconds("30s"))

    def test_seconds_with_space(self) -> None:
        self.assertEqual(30, parse_duration_seconds("30 s"))

    def test_minutes(self) -> None:
        self.assertEqual(300, parse_duration_seconds("5m"))

    def test_hours(self) -> None:
        self.assertEqual(7200, parse_duration_seconds("2h"))

    def test_days(self) -> None:
        self.assertEqual(86400, parse_duration_seconds("1d"))

    def test_zero_seconds(self) -> None:
        self.assertEqual(0, parse_duration_seconds("0s"))

    def test_whitespace_stripped(self) -> None:
        self.assertEqual(60, parse_duration_seconds("  1m  "))

    def test_case_insensitive(self) -> None:
        self.assertEqual(30, parse_duration_seconds("30S"))

    def test_invalid_format_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_duration_seconds("30")

    def test_invalid_unit_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_duration_seconds("30x")

    def test_empty_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_duration_seconds("")

    def test_whitespace_only_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_duration_seconds("   ")

    def test_no_unit_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_duration_seconds("100")

    def test_double_unit_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_duration_seconds("5mm")

    def test_negative_raises_via_regex(self) -> None:
        with self.assertRaises(ConfigError):
            parse_duration_seconds("-5m")


class AppendRuntimeEventTest(unittest.TestCase):
    """Tests for _append_runtime_event."""

    def test_appends_event_to_empty_state(self) -> None:
        state: dict = {}
        event = {"type": "start", "run_id": "abc"}
        _append_runtime_event(state, event)
        self.assertEqual([event], state["runtime_events"])

    def test_appends_event_to_existing_list(self) -> None:
        state = {"runtime_events": [{"type": "first"}]}
        event = {"type": "second"}
        _append_runtime_event(state, event)
        self.assertEqual(2, len(state["runtime_events"]))
        self.assertEqual({"type": "second"}, state["runtime_events"][1])

    def test_preserves_order(self) -> None:
        state: dict = {}
        for i in range(3):
            _append_runtime_event(state, {"i": i})
        events = state["runtime_events"]
        self.assertEqual([{"i": 0}, {"i": 1}, {"i": 2}], events)

    def test_truncates_at_200(self) -> None:
        state: dict = {}
        for i in range(205):
            _append_runtime_event(state, {"i": i})
        events = state["runtime_events"]
        self.assertEqual(200, len(events))
        # First 5 events should be dropped; last 200 preserved
        self.assertEqual(5, events[0]["i"])
        self.assertEqual(204, events[-1]["i"])

    def test_replaces_non_list_runtime_events(self) -> None:
        state = {"runtime_events": "not a list"}
        event = {"type": "start"}
        _append_runtime_event(state, event)
        self.assertEqual([event], state["runtime_events"])

    def test_handles_missing_runtime_events_key(self) -> None:
        state: dict = {}
        event = {"type": "start"}
        _append_runtime_event(state, event)
        self.assertIn("runtime_events", state)


class SeasonAdmissionTest(unittest.TestCase):
    """Tests for SeasonAdmission frozen dataclass."""

    def test_defaults(self) -> None:
        admission = SeasonAdmission()
        self.assertEqual("", admission.season_id)
        self.assertEqual("", admission.participant_id)
        self.assertIsNone(admission.participant)
        self.assertFalse(admission.ranked)
        self.assertEqual("unranked", admission.wake_source)

    def test_explicit_values(self) -> None:
        admission = SeasonAdmission(
            season_id="season_0",
            participant_id="season_0:glm-5.1",
            ranked=True,
            wake_source="auto",
        )
        self.assertEqual("season_0", admission.season_id)
        self.assertTrue(admission.ranked)
        self.assertEqual("auto", admission.wake_source)

    def test_frozen_immutability(self) -> None:
        admission = SeasonAdmission()
        with self.assertRaises(AttributeError):
            admission.season_id = "modified"  # type: ignore[misc]

    def test_all_wake_source_literals(self) -> None:
        for source in ("manual", "auto", "unranked"):
            admission = SeasonAdmission(wake_source=source)
            self.assertEqual(source, admission.wake_source)


class SeasonStatusLiteralTest(unittest.TestCase):
    """Tests for the SeasonStatus Literal type alias."""

    def test_all_accepted_values(self) -> None:
        """Verify the SeasonStatus Literal accepts all four valid strings."""
        from contribarena.engine.seasons import SeasonStatus
        from typing import get_args
        expected = ("draft", "active", "observing", "completed")
        actual = get_args(SeasonStatus)
        self.assertEqual(expected, actual)

    def test_literal_type_is_str_subclass(self) -> None:
        """Verify each SeasonStatus member is a str instance."""
        from contribarena.engine.seasons import SeasonStatus
        from typing import get_args
        for value in get_args(SeasonStatus):
            self.assertIsInstance(value, str)


class ModuleConstantsTest(unittest.TestCase):
    """Tests for module-level constants."""

    def test_max_replacement_attempts(self) -> None:
        self.assertEqual(5, MAX_REPLACEMENT_ATTEMPTS)

    def test_max_live_submission_retry_attempts(self) -> None:
        self.assertEqual(3, MAX_LIVE_SUBMISSION_RETRY_ATTEMPTS)

    def test_constants_are_integers(self) -> None:
        self.assertIsInstance(MAX_REPLACEMENT_ATTEMPTS, int)
        self.assertIsInstance(MAX_LIVE_SUBMISSION_RETRY_ATTEMPTS, int)


if __name__ == "__main__":
    unittest.main()
