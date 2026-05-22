from __future__ import annotations

import unittest

from contribarena.config.schema import RepoCandidate
from contribarena.models import CommandResult
from contribarena.tools.repo_setup_probe import _parse_probe_result, repo_setup_probe


class RepoSetupProbeTest(unittest.TestCase):
    def _candidate(self, owner: str = "test", repo: str = "example", branch: str | None = None) -> RepoCandidate:
        return RepoCandidate(
            owner=owner,
            repo=repo,
            url=f"https://github.com/{owner}/{repo}.git",  # type: ignore[arg-type]
            branch=branch,
        )

    def _cmd(
        self,
        command: str = "",
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        duration: float = 0.1,
    ) -> CommandResult:
        return CommandResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=duration,
        )

    def test_probe_happy_path_returns_python_managers(self) -> None:
        """repo_setup_probe parses a successful Python probe output."""
        candidate = self._candidate()
        json_output = '{"package_managers": ["python"], "test_commands": ["python -m pytest"], "ci_files": [], "setup_difficulty": "low"}'

        class FakeWorkspace:
            def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
                return CommandResult(
                    command=cmd,
                    stdout=json_output + "\n",
                    exit_code=0,
                    duration_seconds=0.15,
                )

        workspace = FakeWorkspace()
        probe, result = repo_setup_probe(workspace, candidate)  # type: ignore[arg-type]

        self.assertTrue(probe.success)
        self.assertFalse(probe.probe_failed)
        self.assertEqual("test/example", probe.full_name)
        self.assertEqual(["python"], probe.package_managers)
        self.assertEqual(["python -m pytest"], probe.test_commands)
        self.assertEqual("low", probe.setup_difficulty)
        self.assertEqual(0, result.exit_code)

    def test_probe_failure_on_nonzero_exit_code(self) -> None:
        """repo_setup_probe reports probe_failed=True when clone or script fails."""
        candidate = self._candidate(owner="bad", repo="repo")

        class FakeWorkspace:
            def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
                return CommandResult(
                    command=cmd,
                    stderr="fatal: could not clone",
                    exit_code=128,
                    duration_seconds=2.0,
                )

        workspace = FakeWorkspace()
        probe, result = repo_setup_probe(workspace, candidate)  # type: ignore[arg-type]

        self.assertFalse(probe.success)
        self.assertTrue(probe.probe_failed)
        self.assertEqual("bad/repo", probe.full_name)
        self.assertIn("fatal: could not clone", probe.error)
        self.assertEqual(128, result.exit_code)

    def test_probe_appends_dotgit_to_github_url(self) -> None:
        """repo_setup_probe appends .git to GitHub HTTPS URLs without it."""
        candidate = RepoCandidate(
            owner="org",
            repo="project",
            url="https://github.com/org/project",  # type: ignore[arg-type]
        )

        class FakeWorkspace:
            def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
                self.last_cmd = cmd
                return CommandResult(
                    command=cmd,
                    stdout='{"package_managers": [], "test_commands": [], "ci_files": [], "setup_difficulty": "low"}\n',
                    exit_code=0,
                    duration_seconds=0.1,
                )

        workspace = FakeWorkspace()
        probe, _ = repo_setup_probe(workspace, candidate)  # type: ignore[arg-type]

        self.assertTrue(probe.success)
        self.assertIn("https://github.com/org/project.git", workspace.last_cmd)

    def test_probe_uses_branch_from_candidate(self) -> None:
        """repo_setup_probe uses the candidate.branch when set."""
        candidate = RepoCandidate(
            owner="org",
            repo="project",
            url="https://github.com/org/project.git",  # type: ignore[arg-type]
            branch="develop",
        )

        class FakeWorkspace:
            def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
                self.last_cmd = cmd
                return CommandResult(
                    command=cmd,
                    stdout='{"package_managers": [], "test_commands": [], "ci_files": [], "setup_difficulty": "low"}\n',
                    exit_code=0,
                    duration_seconds=0.1,
                )

        workspace = FakeWorkspace()
        probe, _ = repo_setup_probe(workspace, candidate)  # type: ignore[arg-type]

        self.assertTrue(probe.success)
        self.assertIn("--branch develop", workspace.last_cmd)

    def test_probe_defaults_branch_to_main(self) -> None:
        """repo_setup_probe defaults branch to 'main' when candidate.branch is None."""
        candidate = self._candidate()

        class FakeWorkspace:
            def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
                self.last_cmd = cmd
                return CommandResult(
                    command=cmd,
                    stdout='{"package_managers": [], "test_commands": [], "ci_files": [], "setup_difficulty": "low"}\n',
                    exit_code=0,
                    duration_seconds=0.1,
                )

        workspace = FakeWorkspace()
        probe, _ = repo_setup_probe(workspace, candidate)  # type: ignore[arg-type]

        self.assertTrue(probe.success)
        self.assertIn("--branch main", workspace.last_cmd)

    def test_probe_clamps_max_probe_seconds_to_min_1(self) -> None:
        """repo_setup_probe clamps max_probe_seconds to at least 1."""
        candidate = self._candidate()

        class FakeWorkspace:
            def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
                self.last_timeout = timeout_seconds
                return CommandResult(
                    command=cmd,
                    stdout='{"package_managers": [], "test_commands": [], "ci_files": [], "setup_difficulty": "low"}\n',
                    exit_code=0,
                    duration_seconds=0.1,
                )

        workspace = FakeWorkspace()
        probe, _ = repo_setup_probe(workspace, candidate, max_probe_seconds=0)  # type: ignore[arg-type]

        self.assertTrue(probe.success)
        self.assertEqual(1, workspace.last_timeout)

    def test_parse_probe_result_json_decode_error(self) -> None:
        """_parse_probe_result returns probe_failed=True on invalid JSON."""
        candidate = self._candidate()
        result = self._cmd(stdout="not valid json\n", exit_code=0)

        probe = _parse_probe_result(candidate, "main", result)

        self.assertFalse(probe.success)
        self.assertTrue(probe.probe_failed)
        self.assertIn("probe output parse failed", probe.error)

    def test_parse_probe_result_empty_stdout(self) -> None:
        """_parse_probe_result falls back to {} on empty stdout, returning empty success."""
        candidate = self._candidate()
        result = self._cmd(stdout="", exit_code=0)

        probe = _parse_probe_result(candidate, "main", result)

        self.assertTrue(probe.success)
        self.assertFalse(probe.probe_failed)
        self.assertEqual([], probe.package_managers)
        self.assertEqual([], probe.test_commands)
        self.assertEqual([], probe.ci_files)
        self.assertEqual("unknown", probe.setup_difficulty)

    def test_parse_probe_result_sets_difficulty_unknown_when_missing(self) -> None:
        """_parse_probe_result defaults setup_difficulty to 'unknown' when key is absent."""
        candidate = self._candidate()
        json_output = '{"package_managers": [], "test_commands": [], "ci_files": []}'
        result = self._cmd(stdout=json_output + "\n", exit_code=0)

        probe = _parse_probe_result(candidate, "main", result)

        self.assertTrue(probe.success)
        self.assertEqual("unknown", probe.setup_difficulty)


if __name__ == "__main__":
    unittest.main()
