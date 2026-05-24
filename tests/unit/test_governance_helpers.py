from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from contribarena.config.schema import (
    BotIdentityConfig,
    GovernanceConfig,
    GovernanceRateLimits,
    OwnedRepositoryPolicy,
)
from contribarena.engine.middleware.governance import (
    _bot_identity_reasons,
    _maintainer_signal_reasons,
    _owned_repo_policy,
    _parse_timestamp,
    _rate_limit_reasons,
    record_governance_attempt,
    record_maintainer_signal,
    update_governance_pr_state,
    upsert_lifecycle_record,
)
from contribarena.models import GovernanceAttempt, GovernancePrRef, GovernanceState
from contribarena.models.governance import MaintainerSignal, PrLifecycleRecord


class OwnedRepoPolicyTest(unittest.TestCase):
    """Tests for _owned_repo_policy."""

    def test_returns_matching_policy(self) -> None:
        policy = OwnedRepositoryPolicy(owner="example", repo="repo")
        governance = GovernanceConfig(owned_repositories=[policy])
        result = _owned_repo_policy(governance, "example", "repo")
        self.assertEqual(policy, result)

    def test_returns_none_when_no_match(self) -> None:
        policy = OwnedRepositoryPolicy(owner="example", repo="repo")
        governance = GovernanceConfig(owned_repositories=[policy])
        result = _owned_repo_policy(governance, "other", "repo")
        self.assertIsNone(result)

    def test_returns_none_when_empty_list(self) -> None:
        governance = GovernanceConfig(owned_repositories=[])
        result = _owned_repo_policy(governance, "example", "repo")
        self.assertIsNone(result)

    def test_returns_first_match_when_multiple_policies(self) -> None:
        policy1 = OwnedRepositoryPolicy(owner="example", repo="repo")
        policy2 = OwnedRepositoryPolicy(owner="example", repo="repo2")
        governance = GovernanceConfig(owned_repositories=[policy1, policy2])
        result = _owned_repo_policy(governance, "example", "repo")
        self.assertEqual(policy1, result)

    def test_owner_and_repo_both_must_match(self) -> None:
        policy = OwnedRepositoryPolicy(owner="example", repo="repo")
        governance = GovernanceConfig(owned_repositories=[policy])
        # Same owner but different repo
        self.assertIsNone(_owned_repo_policy(governance, "example", "other"))
        # Different owner but same repo name
        self.assertIsNone(_owned_repo_policy(governance, "other", "repo"))


class BotIdentityReasonsTest(unittest.TestCase):
    """Tests for _bot_identity_reasons."""

    def test_returns_missing_actor_reason_when_actor_empty(self) -> None:
        governance = GovernanceConfig(
            bot_identity=BotIdentityConfig(actor="", token_env="GITHUB_TOKEN")
        )
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            reasons = _bot_identity_reasons(governance, "")
        self.assertIn("bot identity actor is missing", reasons)

    def test_returns_missing_token_reason_when_env_not_set(self) -> None:
        governance = GovernanceConfig(
            bot_identity=BotIdentityConfig(actor="bot", token_env="MISSING_TOKEN")
        )
        with patch.dict(os.environ, {}, clear=True):
            reasons = _bot_identity_reasons(governance, "bot")
        self.assertIn("bot token env is missing: MISSING_TOKEN", reasons)

    def test_returns_actor_mismatch_reason(self) -> None:
        governance = GovernanceConfig(
            bot_identity=BotIdentityConfig(actor="expected-bot", token_env="GITHUB_TOKEN")
        )
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            reasons = _bot_identity_reasons(governance, "different-bot")
        self.assertIn(
            "authenticated actor different-bot does not match expected expected-bot", reasons
        )

    def test_returns_no_reasons_when_all_valid(self) -> None:
        governance = GovernanceConfig(
            bot_identity=BotIdentityConfig(actor="expected-bot", token_env="GITHUB_TOKEN")
        )
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            reasons = _bot_identity_reasons(governance, "expected-bot")
        self.assertEqual([], reasons)

    def test_no_actor_mismatch_reason_when_empty_actor(self) -> None:
        governance = GovernanceConfig(
            bot_identity=BotIdentityConfig(actor="expected-bot", token_env="GITHUB_TOKEN")
        )
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            reasons = _bot_identity_reasons(governance, "")
        self.assertNotIn("authenticated actor", "; ".join(reasons))

    def test_actor_is_stripped_before_comparison(self) -> None:
        governance = GovernanceConfig(
            bot_identity=BotIdentityConfig(actor="  expected-bot  ", token_env="GITHUB_TOKEN")
        )
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            reasons = _bot_identity_reasons(governance, "expected-bot")
        self.assertEqual([], reasons)

    def test_returns_both_actor_and_token_reasons(self) -> None:
        governance = GovernanceConfig(
            bot_identity=BotIdentityConfig(actor="", token_env="MISSING_TOKEN")
        )
        with patch.dict(os.environ, {}, clear=True):
            reasons = _bot_identity_reasons(governance, "")
        self.assertEqual(2, len(reasons))
        self.assertIn("bot identity actor is missing", reasons)
        self.assertIn("bot token env is missing: MISSING_TOKEN", reasons)


class ParseTimestampTest(unittest.TestCase):
    """Tests for _parse_timestamp."""

    def test_utc_timestamp(self) -> None:
        result = _parse_timestamp("2024-01-01T00:00:00+00:00")
        self.assertEqual(datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), result)

    def test_naive_timestamp_assumes_utc(self) -> None:
        result = _parse_timestamp("2024-01-01T00:00:00")
        self.assertEqual(datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), result)

    def test_offset_timestamp_converts_to_utc(self) -> None:
        result = _parse_timestamp("2024-01-01T06:00:00+06:00")
        self.assertEqual(datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), result)

    def test_negative_offset_timestamp(self) -> None:
        result = _parse_timestamp("2024-01-01T00:00:00-05:00")
        expected = datetime(2024, 1, 1, 5, 0, 0, tzinfo=UTC)
        self.assertEqual(expected, result)


class MaintainerSignalReasonsTest(unittest.TestCase):
    """Tests for _maintainer_signal_reasons."""

    def test_no_signals_returns_empty(self) -> None:
        state = GovernanceState()
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual([], reasons)

    def test_repo_specific_high_severity_opt_out_blocks(self) -> None:
        signal = MaintainerSignal(
            repository="example/repo",
            organization="example",
            kind="opt_out",
            severity="high",
            message="No automated PRs",
        )
        state = GovernanceState(maintainer_signals=[signal])
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual(1, len(reasons))
        self.assertIn("high-severity maintainer signal", reasons[0])
        self.assertIn("opt_out", reasons[0])

    def test_repo_specific_high_severity_anti_ai_blocks(self) -> None:
        signal = MaintainerSignal(
            repository="example/repo",
            organization="example",
            kind="anti_ai_or_bot",
            severity="high",
            message="No bots",
        )
        state = GovernanceState(maintainer_signals=[signal])
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual(1, len(reasons))
        self.assertIn("anti_ai_or_bot", reasons[0])

    def test_repo_specific_medium_severity_does_not_block(self) -> None:
        signal = MaintainerSignal(
            repository="example/repo",
            organization="example",
            kind="opt_out",
            severity="medium",
            message="",
        )
        state = GovernanceState(maintainer_signals=[signal])
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual([], reasons)

    def test_org_level_high_severity_opt_out_applies_to_all_repos(self) -> None:
        signal = MaintainerSignal(
            repository="",
            organization="example",
            kind="opt_out",
            severity="high",
            message="Org-wide opt out",
        )
        state = GovernanceState(maintainer_signals=[signal])
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual(1, len(reasons))
        self.assertIn("high-severity maintainer signal", reasons[0])

    def test_org_level_high_severity_does_not_apply_to_different_org(self) -> None:
        signal = MaintainerSignal(
            repository="",
            organization="other",
            kind="opt_out",
            severity="high",
            message="",
        )
        state = GovernanceState(maintainer_signals=[signal])
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual([], reasons)

    def test_org_level_medium_severity_does_not_block(self) -> None:
        signal = MaintainerSignal(
            repository="",
            organization="example",
            kind="opt_out",
            severity="medium",
            message="",
        )
        state = GovernanceState(maintainer_signals=[signal])
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual([], reasons)

    def test_positive_signal_does_not_block(self) -> None:
        signal = MaintainerSignal(
            repository="example/repo",
            organization="example",
            kind="positive",
            severity="high",
            message="",
        )
        state = GovernanceState(maintainer_signals=[signal])
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual([], reasons)

    def test_multiple_signals_only_high_severity_blocks(self) -> None:
        signal1 = MaintainerSignal(
            repository="example/repo",
            organization="example",
            kind="opt_out",
            severity="medium",
            message="",
        )
        signal2 = MaintainerSignal(
            repository="example/repo",
            organization="example",
            kind="anti_ai_or_bot",
            severity="high",
            message="",
        )
        state = GovernanceState(maintainer_signals=[signal1, signal2])
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual(1, len(reasons))

    def test_repo_signal_does_not_apply_to_different_repo(self) -> None:
        signal = MaintainerSignal(
            repository="other/repo",
            organization="other",
            kind="opt_out",
            severity="high",
            message="",
        )
        state = GovernanceState(maintainer_signals=[signal])
        reasons = _maintainer_signal_reasons(state, "example/repo", "example")
        self.assertEqual([], reasons)


class RateLimitReasonsTest(unittest.TestCase):
    """Tests for _rate_limit_reasons."""

    def _default_limits(self) -> GovernanceRateLimits:
        return GovernanceRateLimits(
            max_open_prs_per_repo=1,
            max_prs_per_repo_per_day=3,
            min_minutes_between_prs_per_repo=30,
            max_open_prs_per_org=3,
            max_prs_per_org_per_day=5,
            min_minutes_between_prs_per_org=60,
            max_open_prs_global=10,
            max_prs_global_per_day=10,
            min_minutes_between_prs_global=15,
        )

    def test_no_prs_no_attempts_returns_empty(self) -> None:
        governance = GovernanceConfig(rate_limits=self._default_limits())
        state = GovernanceState()
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertEqual([], reasons)

    def test_repo_open_pr_limit(self) -> None:
        governance = GovernanceConfig(rate_limits=self._default_limits())
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="test")
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertIn("open PR limit reached", "; ".join(reasons))

    def test_repo_open_pr_limit_not_reached(self) -> None:
        limits = GovernanceRateLimits(max_open_prs_per_repo=2)
        governance = GovernanceConfig(rate_limits=limits)
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="test")
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertNotIn("open PR limit reached", "; ".join(reasons))

    def test_org_open_pr_limit(self) -> None:
        limits = GovernanceRateLimits(max_open_prs_per_org=1)
        governance = GovernanceConfig(rate_limits=limits)
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo1", number=1, branch="test"),
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo2")
        self.assertIn("open PR limit reached for organization", "; ".join(reasons))

    def test_global_open_pr_limit(self) -> None:
        limits = GovernanceRateLimits(max_open_prs_global=1)
        governance = GovernanceConfig(rate_limits=limits)
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="other/repo", number=1, branch="test")
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertIn("global open PR limit reached", "; ".join(reasons))

    def test_closed_prs_do_not_count_against_open_limit(self) -> None:
        governance = GovernanceConfig(rate_limits=self._default_limits())
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="test", state="closed")
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertNotIn("open PR limit reached", "; ".join(reasons))

    def test_merged_prs_do_not_count_against_open_limit(self) -> None:
        governance = GovernanceConfig(rate_limits=self._default_limits())
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="test", state="merged")
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertNotIn("open PR limit reached", "; ".join(reasons))

    def test_prs_in_different_repo_do_not_count_against_repo_limit(self) -> None:
        governance = GovernanceConfig(rate_limits=self._default_limits())
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="other/repo", number=1, branch="test")
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertNotIn("open PR limit reached for example/repo", "; ".join(reasons))

    def test_repo_cooldown_active(self) -> None:
        limits = GovernanceRateLimits(min_minutes_between_prs_per_repo=30)
        governance = GovernanceConfig(rate_limits=limits)
        now = datetime.now(UTC)
        recent_timestamp = (now - timedelta(minutes=5)).isoformat()
        state = GovernanceState(
            attempts=[
                GovernanceAttempt(
                    repository="example/repo",
                    action="github.open_pr",
                    status="opened",
                    created_at=recent_timestamp,
                )
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertIn("PR cooldown active for example/repo", "; ".join(reasons))

    def test_repo_cooldown_expired(self) -> None:
        limits = GovernanceRateLimits(min_minutes_between_prs_per_repo=30)
        governance = GovernanceConfig(rate_limits=limits)
        now = datetime.now(UTC)
        past_timestamp = (now - timedelta(minutes=60)).isoformat()
        state = GovernanceState(
            attempts=[
                GovernanceAttempt(
                    repository="example/repo",
                    action="github.open_pr",
                    status="opened",
                    created_at=past_timestamp,
                )
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertNotIn("PR cooldown active for example/repo", "; ".join(reasons))

    def test_org_cooldown_active(self) -> None:
        limits = GovernanceRateLimits(min_minutes_between_prs_per_org=60)
        governance = GovernanceConfig(rate_limits=limits)
        now = datetime.now(UTC)
        recent_timestamp = (now - timedelta(minutes=5)).isoformat()
        state = GovernanceState(
            attempts=[
                GovernanceAttempt(
                    repository="example/repo",
                    action="github.open_pr",
                    status="opened",
                    created_at=recent_timestamp,
                )
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertIn("organization PR cooldown active", "; ".join(reasons))

    def test_global_cooldown_active(self) -> None:
        limits = GovernanceRateLimits(min_minutes_between_prs_global=15)
        governance = GovernanceConfig(rate_limits=limits)
        now = datetime.now(UTC)
        recent_timestamp = (now - timedelta(minutes=2)).isoformat()
        state = GovernanceState(
            attempts=[
                GovernanceAttempt(
                    repository="example/repo",
                    action="github.open_pr",
                    status="opened",
                    created_at=recent_timestamp,
                )
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertIn("global PR cooldown active", "; ".join(reasons))

    def test_cooldown_not_checked_when_all_min_minutes_zero(self) -> None:
        limits = GovernanceRateLimits(
            max_open_prs_per_repo=1,
            max_prs_per_repo_per_day=3,
            min_minutes_between_prs_per_repo=0,
            max_open_prs_per_org=3,
            max_prs_per_org_per_day=5,
            min_minutes_between_prs_per_org=0,
            max_open_prs_global=10,
            max_prs_global_per_day=10,
            min_minutes_between_prs_global=0,
        )
        governance = GovernanceConfig(rate_limits=limits)
        now = datetime.now(UTC)
        recent_timestamp = (now - timedelta(minutes=1)).isoformat()
        state = GovernanceState(
            attempts=[
                GovernanceAttempt(
                    repository="example/repo",
                    action="github.open_pr",
                    status="opened",
                    created_at=recent_timestamp,
                )
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        cooldown_reasons = [r for r in reasons if "cooldown" in r.lower()]
        self.assertEqual([], cooldown_reasons)

    def test_daily_repo_pr_limit(self) -> None:
        limits = GovernanceRateLimits(max_prs_per_repo_per_day=1)
        governance = GovernanceConfig(rate_limits=limits)
        now = datetime.now(UTC)
        today_timestamp = now.isoformat()
        state = GovernanceState(
            attempts=[
                GovernanceAttempt(
                    repository="example/repo",
                    action="github.open_pr",
                    status="opened",
                    created_at=today_timestamp,
                )
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertIn("daily PR limit reached", "; ".join(reasons))

    def test_daily_repo_pr_limit_not_counted_when_different_day(self) -> None:
        limits = GovernanceRateLimits(max_prs_per_repo_per_day=1)
        governance = GovernanceConfig(rate_limits=limits)
        now = datetime.now(UTC)
        yesterday_timestamp = (now - timedelta(days=1)).isoformat()
        state = GovernanceState(
            attempts=[
                GovernanceAttempt(
                    repository="example/repo",
                    action="github.open_pr",
                    status="opened",
                    created_at=yesterday_timestamp,
                )
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertNotIn("daily PR limit reached", "; ".join(reasons))

    def test_attempts_with_non_opened_status_not_counted_daily(self) -> None:
        limits = GovernanceRateLimits(max_prs_per_repo_per_day=1)
        governance = GovernanceConfig(rate_limits=limits)
        now = datetime.now(UTC)
        today_timestamp = now.isoformat()
        state = GovernanceState(
            attempts=[
                GovernanceAttempt(
                    repository="example/repo",
                    action="github.open_pr",
                    status="blocked",
                    created_at=today_timestamp,
                )
            ]
        )
        reasons = _rate_limit_reasons(governance, state, "example/repo")
        self.assertNotIn("daily PR limit reached", "; ".join(reasons))


class RecordGovernanceAttemptTest(unittest.TestCase):
    """Tests for record_governance_attempt."""

    def test_appends_attempt_to_state(self) -> None:
        state = GovernanceState()
        record_governance_attempt(
            state,
            repository="example/repo",
            status="opened",
            action="github.open_pr",
        )
        self.assertEqual(1, len(state.attempts))
        self.assertEqual("example/repo", state.attempts[0].repository)
        self.assertEqual("opened", state.attempts[0].status)
        self.assertEqual("github.open_pr", state.attempts[0].action)

    def test_appends_multiple_attempts(self) -> None:
        state = GovernanceState()
        record_governance_attempt(state, repository="example/repo", status="opened")
        record_governance_attempt(state, repository="example/repo", status="blocked")
        self.assertEqual(2, len(state.attempts))

    def test_preserves_decision_id(self) -> None:
        state = GovernanceState()
        record_governance_attempt(
            state,
            repository="example/repo",
            status="opened",
            decision_id="abc123",
        )
        self.assertEqual("abc123", state.attempts[0].decision_id)


class UpdateGovernancePrStateTest(unittest.TestCase):
    """Tests for update_governance_pr_state."""

    def test_updates_matching_pr_state_to_merged(self) -> None:
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="test")
            ]
        )
        update_governance_pr_state(state, repository="example/repo", number=1, pr_state="merged")
        self.assertEqual("merged", state.pull_requests[0].state)

    def test_updates_matching_pr_state_to_closed(self) -> None:
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="test")
            ]
        )
        update_governance_pr_state(state, repository="example/repo", number=1, pr_state="closed")
        self.assertEqual("closed", state.pull_requests[0].state)

    def test_open_pr_state_normalized_to_open(self) -> None:
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="test", state="merged")
            ]
        )
        update_governance_pr_state(state, repository="example/repo", number=1, pr_state="open")
        self.assertEqual("open", state.pull_requests[0].state)

    def test_no_update_when_pr_not_found(self) -> None:
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="test")
            ]
        )
        update_governance_pr_state(state, repository="other/repo", number=2, pr_state="merged")
        # Original state unchanged
        self.assertEqual("open", state.pull_requests[0].state)

    def test_matches_on_repository_and_number(self) -> None:
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="test1"),
                GovernancePrRef(repository="example/repo", number=2, branch="test2"),
            ]
        )
        update_governance_pr_state(state, repository="example/repo", number=2, pr_state="merged")
        self.assertEqual("open", state.pull_requests[0].state)
        self.assertEqual("merged", state.pull_requests[1].state)


class UpsertLifecycleRecordTest(unittest.TestCase):
    """Tests for upsert_lifecycle_record."""

    def test_appends_new_record(self) -> None:
        state = GovernanceState()
        record = PrLifecycleRecord(repository="example/repo", number=1)
        upsert_lifecycle_record(state, record)
        self.assertEqual(1, len(state.lifecycle_records))
        self.assertEqual("example/repo", state.lifecycle_records[0].repository)

    def test_updates_existing_record_by_repo_and_number(self) -> None:
        old = PrLifecycleRecord(repository="example/repo", number=1, state="open")
        state = GovernanceState(lifecycle_records=[old])
        new = PrLifecycleRecord(repository="example/repo", number=1, state="merged")
        upsert_lifecycle_record(state, new)
        self.assertEqual(1, len(state.lifecycle_records))
        self.assertEqual("merged", state.lifecycle_records[0].state)

    def test_preserves_season_id_from_existing_when_new_has_none(self) -> None:
        old = PrLifecycleRecord(
            repository="example/repo",
            number=1,
            season_id="season_0",
            participant_id="season_0:bot",
        )
        state = GovernanceState(lifecycle_records=[old])
        new = PrLifecycleRecord(repository="example/repo", number=1, state="merged")
        upsert_lifecycle_record(state, new)
        self.assertEqual("season_0", state.lifecycle_records[0].season_id)

    def test_preserves_participant_id_from_existing_when_new_has_none(self) -> None:
        old = PrLifecycleRecord(
            repository="example/repo",
            number=1,
            season_id="season_0",
            participant_id="season_0:bot",
        )
        state = GovernanceState(lifecycle_records=[old])
        new = PrLifecycleRecord(repository="example/repo", number=1, state="merged")
        upsert_lifecycle_record(state, new)
        self.assertEqual("season_0:bot", state.lifecycle_records[0].participant_id)

    def test_does_not_match_different_repo(self) -> None:
        old = PrLifecycleRecord(repository="example/repo", number=1)
        state = GovernanceState(lifecycle_records=[old])
        new = PrLifecycleRecord(repository="other/repo", number=1)
        upsert_lifecycle_record(state, new)
        self.assertEqual(2, len(state.lifecycle_records))

    def test_does_not_match_different_number(self) -> None:
        old = PrLifecycleRecord(repository="example/repo", number=1)
        state = GovernanceState(lifecycle_records=[old])
        new = PrLifecycleRecord(repository="example/repo", number=2)
        upsert_lifecycle_record(state, new)
        self.assertEqual(2, len(state.lifecycle_records))


class RecordMaintainerSignalTest(unittest.TestCase):
    """Tests for record_maintainer_signal."""

    def test_appends_signal_to_state(self) -> None:
        state = GovernanceState()
        signal = MaintainerSignal(
            repository="example/repo",
            organization="example",
            kind="opt_out",
            severity="high",
            message="No bots",
        )
        record_maintainer_signal(state, signal)
        self.assertEqual(1, len(state.maintainer_signals))
        self.assertEqual(signal, state.maintainer_signals[0])

    def test_appends_multiple_signals(self) -> None:
        state = GovernanceState()
        signal1 = MaintainerSignal(
            repository="example/repo", organization="example", kind="opt_out", severity="high"
        )
        signal2 = MaintainerSignal(
            repository="other/repo", organization="other", kind="positive", severity="low"
        )
        record_maintainer_signal(state, signal1)
        record_maintainer_signal(state, signal2)
        self.assertEqual(2, len(state.maintainer_signals))

    def test_appends_signal_to_matching_lifecycle_record(self) -> None:
        record = PrLifecycleRecord(repository="example/repo", number=1)
        state = GovernanceState(lifecycle_records=[record])
        signal = MaintainerSignal(
            repository="example/repo", organization="example", kind="opt_out", severity="high"
        )
        record_maintainer_signal(state, signal)
        self.assertEqual(1, len(state.lifecycle_records[0].maintainer_signals))
        self.assertEqual(signal, state.lifecycle_records[0].maintainer_signals[0])

    def test_does_not_append_signal_to_different_repo_lifecycle_record(self) -> None:
        record = PrLifecycleRecord(repository="other/repo", number=1)
        state = GovernanceState(lifecycle_records=[record])
        signal = MaintainerSignal(
            repository="example/repo", organization="example", kind="opt_out", severity="high"
        )
        record_maintainer_signal(state, signal)
        self.assertEqual(0, len(state.lifecycle_records[0].maintainer_signals))

    def test_signal_added_to_all_matching_lifecycle_records(self) -> None:
        record1 = PrLifecycleRecord(repository="example/repo", number=1)
        record2 = PrLifecycleRecord(repository="example/repo", number=2)
        state = GovernanceState(lifecycle_records=[record1, record2])
        signal = MaintainerSignal(
            repository="example/repo", organization="example", kind="opt_out", severity="high"
        )
        record_maintainer_signal(state, signal)
        self.assertEqual(1, len(state.lifecycle_records[0].maintainer_signals))
        self.assertEqual(1, len(state.lifecycle_records[1].maintainer_signals))

    def test_signal_accumulates_on_lifecycle_record(self) -> None:
        record = PrLifecycleRecord(repository="example/repo", number=1)
        state = GovernanceState(lifecycle_records=[record])
        signal1 = MaintainerSignal(
            repository="example/repo", organization="example", kind="opt_out", severity="medium"
        )
        signal2 = MaintainerSignal(
            repository="example/repo", organization="example", kind="anti_ai_or_bot", severity="high"
        )
        record_maintainer_signal(state, signal1)
        record_maintainer_signal(state, signal2)
        self.assertEqual(2, len(state.lifecycle_records[0].maintainer_signals))
