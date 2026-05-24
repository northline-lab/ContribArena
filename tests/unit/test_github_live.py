from __future__ import annotations

import unittest

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    OwnedRepositoryPolicy,
    PrSubmissionConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.models import CommandResult
from contribarena.tools.github_live import (
    _configured_repo_full_name,
    _has_patch_diff,
    _last_sha,
    _owned_repo_policy,
    _patch_paths,
    _prefixed_line,
    _redact_command_result,
    _suspicious_patch_paths,
    _transient_message,
)


def _run_config() -> RunConfig:
    """Build a minimal valid RunConfig for tests."""
    return RunConfig(
        run=RunSection(),
        discovery=DiscoveryConfig(query="dummy"),
        workspace=WorkspaceConfig(),
        artifacts=ArtifactConfig(),
    )


# ---------------------------------------------------------------------------
# _has_patch_diff
# ---------------------------------------------------------------------------


class HasPatchDiffTest(unittest.TestCase):
    def test_starts_with_diff_marker(self) -> None:
        self.assertTrue(_has_patch_diff("diff --git a/f.py b/f.py\n"))

    def test_contains_embedded_diff(self) -> None:
        self.assertTrue(_has_patch_diff("some header\ndiff --git a/f.py b/f.py\n"))

    def test_empty_string(self) -> None:
        self.assertFalse(_has_patch_diff(""))

    def test_whitespace_only(self) -> None:
        self.assertFalse(_has_patch_diff("   \n  \n"))

    def test_diff_marker_in_middle_of_line(self) -> None:
        self.assertFalse(_has_patch_diff("foo diff --git a/f.py b/f.py"))

    def test_diff_marker_at_start_after_whitespace(self) -> None:
        self.assertTrue(_has_patch_diff("  diff --git a/f.py b/f.py"))


# ---------------------------------------------------------------------------
# _patch_paths
# ---------------------------------------------------------------------------


class PatchPathsTest(unittest.TestCase):
    def test_single_file(self) -> None:
        diff = "diff --git a/src/app.py b/src/app.py\n"
        self.assertEqual(_patch_paths(diff), ["src/app.py"])

    def test_multiple_files(self) -> None:
        diff = (
            "diff --git a/a.py b/a.py\n"
            "diff --git a/b.py b/b.py\n"
            "diff --git a/c.py b/c.py\n"
        )
        self.assertEqual(_patch_paths(diff), ["a.py", "b.py", "c.py"])

    def test_deduplicates(self) -> None:
        diff = "diff --git a/a.py b/a.py\ndiff --git a/a.py b/a.py\n"
        self.assertEqual(_patch_paths(diff), ["a.py"])

    def test_returns_sorted(self) -> None:
        diff = (
            "diff --git a/z.py b/z.py\n"
            "diff --git a/a.py b/a.py\n"
        )
        self.assertEqual(_patch_paths(diff), ["a.py", "z.py"])

    def test_empty_patch(self) -> None:
        self.assertEqual(_patch_paths(""), [])

    def test_no_diff_lines(self) -> None:
        self.assertEqual(_patch_paths("just some text\n"), [])

    def test_removes_b_prefix(self) -> None:
        diff = "diff --git a/tests/test_f.py b/tests/test_f.py\n"
        self.assertEqual(_patch_paths(diff), ["tests/test_f.py"])


# ---------------------------------------------------------------------------
# _suspicious_patch_paths
# ---------------------------------------------------------------------------


class SuspiciousPatchPathsTest(unittest.TestCase):
    def test_clean_paths(self) -> None:
        self.assertEqual(_suspicious_patch_paths(["src/app.py"]), [])

    def test_pycache_is_suspicious(self) -> None:
        self.assertEqual(
            _suspicious_patch_paths(["src/__pycache__/app.cpython-311.pyc"]),
            ["src/__pycache__/app.cpython-311.pyc"],
        )

    def test_pyc_is_suspicious(self) -> None:
        self.assertEqual(_suspicious_patch_paths(["app.pyc"]), ["app.pyc"])

    def test_pyo_is_suspicious(self) -> None:
        self.assertEqual(_suspicious_patch_paths(["app.pyo"]), ["app.pyo"])

    def test_ds_store_is_suspicious(self) -> None:
        self.assertEqual(_suspicious_patch_paths([".DS_Store"]), [".DS_Store"])

    def test_egg_info_is_suspicious(self) -> None:
        self.assertEqual(
            _suspicious_patch_paths(["src/lib.egg-info/PKG-INFO"]),
            ["src/lib.egg-info/PKG-INFO"],
        )

    def test_pytest_cache_is_suspicious(self) -> None:
        self.assertEqual(
            _suspicious_patch_paths([".pytest_cache/v/cache/stepwise"]),
            [".pytest_cache/v/cache/stepwise"],
        )

    def test_mixed_clean_and_suspicious(self) -> None:
        paths = ["src/app.py", "src/__pycache__/x.pyc", "tests/test_app.py"]
        self.assertEqual(_suspicious_patch_paths(paths), ["src/__pycache__/x.pyc"])

    def test_empty_list(self) -> None:
        self.assertEqual(_suspicious_patch_paths([]), [])


# ---------------------------------------------------------------------------
# _prefixed_line
# ---------------------------------------------------------------------------


class PrefixedLineTest(unittest.TestCase):
    def test_basic_match(self) -> None:
        self.assertEqual(_prefixed_line("commit_sha=abc123\n", "commit_sha="), "abc123")

    def test_returns_first_match(self) -> None:
        text = "base_sha=aaa\nbase_sha=bbb\n"
        self.assertEqual(_prefixed_line(text, "base_sha="), "aaa")

    def test_no_match(self) -> None:
        self.assertEqual(_prefixed_line("no prefix here", "commit_sha="), "")

    def test_empty_text(self) -> None:
        self.assertEqual(_prefixed_line("", "sha="), "")

    def test_strips_value(self) -> None:
        self.assertEqual(_prefixed_line("sha= abc123 \n", "sha="), "abc123")


# ---------------------------------------------------------------------------
# _last_sha
# ---------------------------------------------------------------------------


class LastShaTest(unittest.TestCase):
    def test_finds_last_sha(self) -> None:
        sha1 = "1111111111111111111111111111111111111111"
        sha2 = "2222222222222222222222222222222222222222"
        text = f"prefix text\n{sha1}\n{sha2}\n"
        self.assertEqual(_last_sha(text), sha2)

    def test_uppercase_hex(self) -> None:
        sha = "AABBCCDD00112233445566778899AABBCCDDEEFF"
        self.assertEqual(_last_sha(f"{sha}\n"), sha)

    def test_no_sha(self) -> None:
        self.assertEqual(_last_sha("no sha here\n"), "")

    def test_empty(self) -> None:
        self.assertEqual(_last_sha(""), "")

    def test_too_short_hex(self) -> None:
        self.assertEqual(_last_sha("aabbcc\n"), "")

    def test_non_hex_40_chars(self) -> None:
        self.assertEqual(_last_sha("x" * 40 + "\n"), "")


# ---------------------------------------------------------------------------
# _transient_message
# ---------------------------------------------------------------------------


class TransientMessageTest(unittest.TestCase):
    def test_connection_error(self) -> None:
        self.assertTrue(_transient_message("Connection error: refused"))

    def test_timeout(self) -> None:
        self.assertTrue(_transient_message("Operation timed out"))

    def test_502_bad_gateway(self) -> None:
        self.assertTrue(_transient_message("502 Bad Gateway"))

    def test_503(self) -> None:
        self.assertTrue(_transient_message("HTTP 503"))

    def test_api_connection_error(self) -> None:
        self.assertTrue(_transient_message("ApiConnectionError: failed"))

    def test_permanent_error(self) -> None:
        self.assertFalse(_transient_message("fork is not valid"))

    def test_empty(self) -> None:
        self.assertFalse(_transient_message(""))

    def test_case_insensitive(self) -> None:
        self.assertTrue(_transient_message("TIMEOUT after 30s"))

    def test_500_internal(self) -> None:
        self.assertTrue(_transient_message("500 Internal Server Error"))


# ---------------------------------------------------------------------------
# _redact_command_result
# ---------------------------------------------------------------------------


class RedactCommandResultTest(unittest.TestCase):
    def test_redacts_token_in_all_fields(self) -> None:
        result = CommandResult(
            command="echo ghp_SECRET",
            stdout="ghp_SECRET",
            stderr="",
            exit_code=0,
            duration_seconds=0.1,
        )
        redacted = _redact_command_result(result, "ghp_SECRET")
        self.assertNotIn("ghp_SECRET", redacted.command)
        self.assertNotIn("ghp_SECRET", redacted.stdout)
        self.assertIn("***", redacted.command)
        self.assertIn("***", redacted.stdout)

    def test_empty_token_returns_original(self) -> None:
        result = CommandResult(
            command="echo hello", stdout="hello", stderr="",
            exit_code=0, duration_seconds=0.1,
        )
        redacted = _redact_command_result(result, "")
        self.assertIs(redacted, result)

    def test_token_not_present(self) -> None:
        result = CommandResult(
            command="echo hello", stdout="hello", stderr="",
            exit_code=0, duration_seconds=0.1,
        )
        redacted = _redact_command_result(result, "ghp_NOTFOUND")
        self.assertEqual(redacted.command, "echo hello")
        self.assertEqual(redacted.stdout, "hello")


# ---------------------------------------------------------------------------
# _owned_repo_policy
# ---------------------------------------------------------------------------


class OwnedRepoPolicyTest(unittest.TestCase):
    def test_finds_matching_policy(self) -> None:
        policy = OwnedRepositoryPolicy(
            owner="test", repo="repo", pr_submission=PrSubmissionConfig(),
        )
        config = _run_config()
        config.governance.owned_repositories = [policy]
        self.assertIs(_owned_repo_policy(config, "test", "repo"), policy)

    def test_returns_none_for_no_match(self) -> None:
        policy = OwnedRepositoryPolicy(
            owner="test", repo="repo", pr_submission=PrSubmissionConfig(),
        )
        config = _run_config()
        config.governance.owned_repositories = [policy]
        self.assertIsNone(_owned_repo_policy(config, "other", "repo"))

    def test_returns_none_for_empty_list(self) -> None:
        config = _run_config()
        config.governance.owned_repositories = []
        self.assertIsNone(_owned_repo_policy(config, "test", "repo"))


# ---------------------------------------------------------------------------
# _configured_repo_full_name
# ---------------------------------------------------------------------------


class ConfiguredRepoFullNameTest(unittest.TestCase):
    def test_returns_first_candidate(self) -> None:
        candidate = RepoCandidate(
            owner="me", repo="proj", full_name="me/proj",
            url="https://github.com/me/proj",
        )
        config = _run_config()
        config.discovery.candidates = [candidate]
        self.assertEqual(_configured_repo_full_name(config), "me/proj")

    def test_returns_empty_for_no_candidates(self) -> None:
        config = _run_config()
        config.discovery.candidates = []
        self.assertEqual(_configured_repo_full_name(config), "")


if __name__ == "__main__":
    unittest.main()
