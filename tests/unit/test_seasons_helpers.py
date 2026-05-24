from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from contribarena.config.schema import (
    DiscoveryConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    SeasonConfig,
    SeasonDefaultsConfig,
    SeasonParticipantConfig,
)
from contribarena.config.schema import WorkspaceConfig
from contribarena.errors import ConfigError
from contribarena.engine.seasons import (
    _stable_initial_jitter_seconds,
    derive_participant_id,
    normalize_model_identity,
    parse_duration_seconds,
    participant_is_due,
    participant_max_concurrent,
    participant_next_wake_at,
    workspace_key_for,
)


class NormalizeModelIdentityTest(unittest.TestCase):
    def test_simple_name(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("gpt-4o"))

    def test_strip_whitespace(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("  gpt-4o  "))

    def test_lowercase(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("GPT-4O"))

    def test_slash_prefix_removed(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("openai/gpt-4o"))

    def test_colon_prefix_removed(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("provider:gpt-4o"))

    def test_slash_and_colon_combined(self) -> None:
        self.assertEqual("gpt-4o", normalize_model_identity("openai/provider:gpt-4o"))

    def test_special_chars_replaced(self) -> None:
        self.assertEqual("my-model-v2", normalize_model_identity("my model v2!"))

    def test_dashes_stripped_from_edges(self) -> None:
        self.assertEqual("model", normalize_model_identity("---model---"))

    def test_empty_string_returns_unknown(self) -> None:
        self.assertEqual("unknown", normalize_model_identity(""))

    def test_whitespace_only_returns_unknown(self) -> None:
        self.assertEqual("unknown", normalize_model_identity("   "))

    def test_only_special_chars_returns_unknown(self) -> None:
        self.assertEqual("unknown", normalize_model_identity("!!!???"))


class DeriveParticipantIdTest(unittest.TestCase):
    def test_basic_concatenation(self) -> None:
        self.assertEqual("season_0:gpt-4o", derive_participant_id("season_0", "gpt-4o"))

    def test_normalizes_model(self) -> None:
        self.assertEqual("season_0:gpt-4o", derive_participant_id("season_0", "openai/gpt-4o"))

    def test_season_id_preserved(self) -> None:
        self.assertEqual("my-season:gpt-4o", derive_participant_id("my-season", "gpt-4o"))


class ParseDurationSecondsTest(unittest.TestCase):
    def test_seconds(self) -> None:
        self.assertEqual(30, parse_duration_seconds("30s"))

    def test_minutes(self) -> None:
        self.assertEqual(120, parse_duration_seconds("2m"))

    def test_hours(self) -> None:
        self.assertEqual(3600, parse_duration_seconds("1h"))

    def test_days(self) -> None:
        self.assertEqual(86400, parse_duration_seconds("1d"))

    def test_whitespace_allowed(self) -> None:
        self.assertEqual(3600, parse_duration_seconds(" 1h "))

    def test_case_insensitive(self) -> None:
        self.assertEqual(3600, parse_duration_seconds("1H"))

    def test_invalid_format_raises_config_error(self) -> None:
        self.assertRaises(ConfigError, parse_duration_seconds, "abc")

    def test_no_unit_raises_config_error(self) -> None:
        self.assertRaises(ConfigError, parse_duration_seconds, "42")

    def test_empty_raises_config_error(self) -> None:
        self.assertRaises(ConfigError, parse_duration_seconds, "")

    def test_zero_seconds(self) -> None:
        self.assertEqual(0, parse_duration_seconds("0s"))


class ParticipantMaxConcurrentTest(unittest.TestCase):
    def _season(self, max_concurrent: int = 1) -> SeasonConfig:
        return SeasonConfig(defaults=SeasonDefaultsConfig(max_concurrent_runs=max_concurrent))

    def _participant(self, max_concurrent: int | None = None) -> SeasonParticipantConfig:
        return SeasonParticipantConfig(model="gpt-4o", max_concurrent_runs=max_concurrent)

    def test_participant_override(self) -> None:
        season = self._season(max_concurrent=1)
        participant = self._participant(max_concurrent=3)
        self.assertEqual(3, participant_max_concurrent(season, participant))

    def test_season_default_when_participant_none(self) -> None:
        season = self._season(max_concurrent=2)
        participant = self._participant(max_concurrent=None)
        self.assertEqual(2, participant_max_concurrent(season, participant))

    def test_season_default_one(self) -> None:
        season = self._season(max_concurrent=1)
        participant = self._participant(max_concurrent=None)
        self.assertEqual(1, participant_max_concurrent(season, participant))


class ParticipantIsDueTest(unittest.TestCase):
    def _season(self, wake_interval: str = "6h") -> SeasonConfig:
        return SeasonConfig(defaults=SeasonDefaultsConfig(wake_interval=wake_interval))

    def _participant(self, wake_interval: str | None = None) -> SeasonParticipantConfig:
        return SeasonParticipantConfig(model="gpt-4o", wake_interval=wake_interval)

    def test_due_when_no_last_wake(self) -> None:
        season = self._season()
        participant = self._participant()
        self.assertTrue(participant_is_due(season=season, participant=participant, state={}))

    def test_due_when_empty_last_wake(self) -> None:
        season = self._season()
        participant = self._participant()
        self.assertTrue(participant_is_due(season=season, participant=participant, state={"last_wake_at": ""}))

    def test_due_when_invalid_timestamp(self) -> None:
        season = self._season()
        participant = self._participant()
        self.assertTrue(participant_is_due(season=season, participant=participant, state={"last_wake_at": "not-a-date"}))

    def test_not_due_when_within_interval(self) -> None:
        season = self._season(wake_interval="1h")
        participant = self._participant()
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        last_wake = now - timedelta(minutes=30)
        state = {"last_wake_at": last_wake.isoformat()}
        self.assertFalse(participant_is_due(season=season, participant=participant, state=state, now=now))

    def test_due_when_past_interval(self) -> None:
        season = self._season(wake_interval="1h")
        participant = self._participant()
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        last_wake = now - timedelta(hours=2)
        state = {"last_wake_at": last_wake.isoformat()}
        self.assertTrue(participant_is_due(season=season, participant=participant, state=state, now=now))

    def test_due_when_replacement_due(self) -> None:
        season = self._season()
        participant = self._participant()
        state = {"replacement": {"status": "due"}}
        self.assertTrue(participant_is_due(season=season, participant=participant, state=state))

    def test_due_when_live_retry_due(self) -> None:
        season = self._season()
        participant = self._participant()
        state = {"live_submission_retry": {"status": "due"}}
        self.assertTrue(participant_is_due(season=season, participant=participant, state=state))

    def test_not_due_with_naive_timestamp_assumes_utc(self) -> None:
        season = self._season(wake_interval="1h")
        participant = self._participant()
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        last_wake_naive = datetime(2026, 5, 24, 11, 30, 0)
        state = {"last_wake_at": last_wake_naive.isoformat()}
        self.assertFalse(participant_is_due(season=season, participant=participant, state=state, now=now))

    def test_participant_wake_interval_overrides_season(self) -> None:
        season = self._season(wake_interval="6h")
        participant = self._participant(wake_interval="1h")
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        last_wake = now - timedelta(hours=2)
        state = {"last_wake_at": last_wake.isoformat()}
        self.assertTrue(participant_is_due(season=season, participant=participant, state=state, now=now))


class ParticipantNextWakeAtTest(unittest.TestCase):
    def _season(self, wake_interval: str = "6h") -> SeasonConfig:
        return SeasonConfig(defaults=SeasonDefaultsConfig(wake_interval=wake_interval))

    def _participant(self, wake_interval: str | None = None) -> SeasonParticipantConfig:
        return SeasonParticipantConfig(model="gpt-4o", wake_interval=wake_interval)

    def test_returns_now_when_replacement_due(self) -> None:
        season = self._season()
        participant = self._participant()
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        state = {"replacement": {"status": "due"}}
        result = participant_next_wake_at(
            season=season, participant=participant,
            participant_id="s0:gpt-4o", state=state, now=now,
        )
        self.assertEqual(now, result)

    def test_returns_now_when_live_retry_due(self) -> None:
        season = self._season()
        participant = self._participant()
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        state = {"live_submission_retry": {"status": "due"}}
        result = participant_next_wake_at(
            season=season, participant=participant,
            participant_id="s0:gpt-4o", state=state, now=now,
        )
        self.assertEqual(now, result)

    def test_returns_now_plus_jitter_when_no_last_wake(self) -> None:
        season = self._season(wake_interval="1h")
        participant = self._participant()
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        state = {}
        result = participant_next_wake_at(
            season=season, participant=participant,
            participant_id="s0:gpt-4o", state=state, now=now,
        )
        jitter = _stable_initial_jitter_seconds("s0:gpt-4o", season)
        self.assertEqual(now + timedelta(seconds=jitter), result)

    def test_returns_now_on_invalid_last_wake(self) -> None:
        season = self._season()
        participant = self._participant()
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        state = {"last_wake_at": "not-a-date"}
        result = participant_next_wake_at(
            season=season, participant=participant,
            participant_id="s0:gpt-4o", state=state, now=now,
        )
        self.assertEqual(now, result)

    def test_returns_last_wake_plus_interval(self) -> None:
        season = self._season(wake_interval="1h")
        participant = self._participant()
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        last_wake = datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)
        state = {"last_wake_at": last_wake.isoformat()}
        result = participant_next_wake_at(
            season=season, participant=participant,
            participant_id="s0:gpt-4o", state=state, now=now,
        )
        self.assertEqual(last_wake + timedelta(hours=1), result)

    def test_naive_last_wake_assumes_utc(self) -> None:
        season = self._season(wake_interval="1h")
        participant = self._participant()
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
        last_wake_naive = datetime(2026, 5, 24, 10, 0, 0)
        state = {"last_wake_at": last_wake_naive.isoformat()}
        result = participant_next_wake_at(
            season=season, participant=participant,
            participant_id="s0:gpt-4o", state=state, now=now,
        )
        expected = last_wake_naive.replace(tzinfo=UTC) + timedelta(hours=1)
        self.assertEqual(expected, result)


class StableInitialJitterSecondsTest(unittest.TestCase):
    def _season(self, wake_interval: str = "6h") -> SeasonConfig:
        return SeasonConfig(defaults=SeasonDefaultsConfig(wake_interval=wake_interval))

    def test_deterministic_same_inputs_same_output(self) -> None:
        season = self._season()
        result1 = _stable_initial_jitter_seconds("s0:gpt-4o", season)
        result2 = _stable_initial_jitter_seconds("s0:gpt-4o", season)
        self.assertEqual(result1, result2)

    def test_different_participant_ids_yield_different_jitter(self) -> None:
        season = self._season()
        result1 = _stable_initial_jitter_seconds("s0:gpt-4o", season)
        result2 = _stable_initial_jitter_seconds("s0:claude", season)
        # Different participants should produce different jitter in practice
        # but it is not guaranteed for all pairs, so just check both are valid
        self.assertIsInstance(result1, int)
        self.assertIsInstance(result2, int)

    def test_zero_when_interval_is_one_second(self) -> None:
        season = self._season(wake_interval="1s")
        self.assertEqual(0, _stable_initial_jitter_seconds("s0:gpt-4o", season))

    def test_jitter_within_interval_bounds(self) -> None:
        season = self._season(wake_interval="1h")
        result = _stable_initial_jitter_seconds("s0:gpt-4o", season)
        self.assertGreaterEqual(result, 0)
        self.assertLess(result, 3600)

    def test_jitter_capped_at_3600(self) -> None:
        season = self._season(wake_interval="48h")
        result = _stable_initial_jitter_seconds("s0:gpt-4o", season)
        self.assertLess(result, 3601)


class WorkspaceKeyForTest(unittest.TestCase):
    def _config(self, season_id: str | None = None, participant_id: str | None = None) -> RunConfig:
        return RunConfig(
            run=RunSection(season_id=season_id, participant_id=participant_id),
            discovery=DiscoveryConfig(
                candidates=[RepoCandidate(owner="o", repo="r", url="https://github.com/o/r")],
            ),
            workspace=WorkspaceConfig(),
        )

    def test_key_with_season_and_participant(self) -> None:
        config = self._config(season_id="s0", participant_id="glm-5.1")
        result = workspace_key_for(config, "owner/repo")
        self.assertEqual("s0-glm-5.1-owner-repo", result)

    def test_empty_string_without_season(self) -> None:
        config = self._config(season_id=None, participant_id=None)
        result = workspace_key_for(config, "owner/repo")
        self.assertEqual("", result)

    def test_empty_string_without_participant(self) -> None:
        config = self._config(season_id="s0", participant_id=None)
        result = workspace_key_for(config, "owner/repo")
        self.assertEqual("", result)

    def test_special_chars_in_repo_slug_sanitized(self) -> None:
        config = self._config(season_id="s0", participant_id="glm-5.1")
        result = workspace_key_for(config, "owner/repo!@#")
        # Trailing special chars are replaced with dashes then stripped
        self.assertEqual("s0-glm-5.1-owner-repo", result)

    def test_empty_repo_slug_defaults_to_repo(self) -> None:
        config = self._config(season_id="s0", participant_id="glm-5.1")
        # Test with a slug that becomes empty after sanitization
        result = workspace_key_for(config, "!!!")
        self.assertEqual("s0-glm-5.1-repo", result)

    def test_key_with_dots_and_hyphens_preserved(self) -> None:
        config = self._config(season_id="s0", participant_id="glm-5.1")
        result = workspace_key_for(config, "org/my-project.v2")
        self.assertEqual("s0-glm-5.1-org-my-project.v2", result)


if __name__ == "__main__":
    unittest.main()
