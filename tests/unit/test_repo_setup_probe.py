from __future__ import annotations

import json
import unittest

from contribarena.config.schema import RepoCandidate
from contribarena.models import CommandResult
from contribarena.tools.repo_setup_probe import _parse_probe_result


def _candidate() -> RepoCandidate:
    return RepoCandidate(
        owner="example",
        repo="project",
        url="https://github.com/example/project",
    )


def _command_result(
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    duration_seconds: float = 1.25,
) -> CommandResult:
    return CommandResult(
        command="probe",
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
    )


class ParseProbeResultTest(unittest.TestCase):
    def test_parses_successful_probe_payload_from_last_stdout_line(self) -> None:
        payload = {
            "package_managers": ["python", "node"],
            "test_commands": ["python -m pytest", "npm test"],
            "ci_files": [".github/workflows/ci.yml"],
            "setup_difficulty": "low",
        }
        result = _parse_probe_result(
            _candidate(),
            "develop",
            _command_result(stdout="clone output\n" + json.dumps(payload) + "\n"),
        )

        self.assertTrue(result.success)
        self.assertFalse(result.probe_failed)
        self.assertEqual("example/project", result.full_name)
        self.assertEqual("develop", result.default_branch)
        self.assertEqual(["python", "node"], result.package_managers)
        self.assertEqual(["python -m pytest", "npm test"], result.test_commands)
        self.assertEqual([".github/workflows/ci.yml"], result.ci_files)
        self.assertEqual("low", result.setup_difficulty)
        self.assertEqual(1.25, result.duration_seconds)
        self.assertEqual("", result.error)

    def test_successful_probe_coerces_payload_items_to_strings(self) -> None:
        payload = {
            "package_managers": ["python", 42],
            "test_commands": ["python -m pytest", None],
            "ci_files": ["tox.ini", 7],
            "setup_difficulty": None,
        }
        result = _parse_probe_result(
            _candidate(),
            "main",
            _command_result(stdout=json.dumps(payload)),
        )

        self.assertEqual(["python", "42"], result.package_managers)
        self.assertEqual(["python -m pytest", "None"], result.test_commands)
        self.assertEqual(["tox.ini", "7"], result.ci_files)
        self.assertEqual("unknown", result.setup_difficulty)

    def test_command_failure_returns_failed_probe_with_error_output(self) -> None:
        result = _parse_probe_result(
            _candidate(),
            "main",
            _command_result(stdout="clone failed", stderr="fatal: missing branch", exit_code=128),
        )

        self.assertFalse(result.success)
        self.assertTrue(result.probe_failed)
        self.assertEqual("example/project", result.full_name)
        self.assertEqual("main", result.default_branch)
        self.assertEqual(1.25, result.duration_seconds)
        self.assertEqual("fatal: missing branch", result.error)
        self.assertEqual([], result.package_managers)
        self.assertEqual([], result.test_commands)
        self.assertEqual([], result.ci_files)

    def test_command_failure_uses_stdout_when_stderr_is_empty(self) -> None:
        result = _parse_probe_result(
            _candidate(),
            "main",
            _command_result(stdout="clone failed", exit_code=1),
        )

        self.assertFalse(result.success)
        self.assertTrue(result.probe_failed)
        self.assertEqual("clone failed", result.error)

    def test_malformed_success_output_returns_parse_failure(self) -> None:
        result = _parse_probe_result(
            _candidate(),
            "main",
            _command_result(stdout="clone output\nnot-json"),
        )

        self.assertFalse(result.success)
        self.assertTrue(result.probe_failed)
        self.assertEqual("main", result.default_branch)
        self.assertEqual(1.25, result.duration_seconds)
        self.assertIn("probe output parse failed", result.error)


if __name__ == "__main__":
    unittest.main()
