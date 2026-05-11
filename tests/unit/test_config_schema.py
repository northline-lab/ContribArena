from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from contribarena.config import load_run_config, write_starter_config
from contribarena.config.schema import (
    DiscoveryConfig,
    IssueConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)


class ConfigSchemaTest(unittest.TestCase):
    def test_init_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run_config.yaml"
            write_starter_config(path)

            config = load_run_config(path)

            self.assertEqual("shadow", config.run.mode)
            self.assertEqual("docker", config.workspace.backend)
            self.assertEqual(
                "openai/openai-agents-python", config.discovery.candidates[0].full_name
            )

    def test_issue_solving_requires_one_fixed_candidate_and_clone_url(self) -> None:
        with self.assertRaisesRegex(ValidationError, "exactly one fixed discovery candidate"):
            RunConfig(
                run=RunSection(),
                discovery=DiscoveryConfig(
                    query="agent framework",
                    filters={"language": "Python"},
                ),
                issue=IssueConfig(
                    problem_statement="Fix the configured bug.",
                    clone_url="https://github.com/example/repo.git",
                ),
                workspace=WorkspaceConfig(),
            )

        with self.assertRaisesRegex(ValidationError, "issue.clone_url"):
            RunConfig(
                run=RunSection(),
                discovery=DiscoveryConfig(
                    candidates=[
                        RepoCandidate(
                            owner="example",
                            repo="repo",
                            url="https://github.com/example/repo",
                        )
                    ]
                ),
                issue=IssueConfig(problem_statement="Fix the configured bug."),
                workspace=WorkspaceConfig(),
            )

        config = RunConfig(
            run=RunSection(),
            discovery=DiscoveryConfig(
                candidates=[
                    RepoCandidate(
                        owner="example",
                        repo="repo",
                        url="https://github.com/example/repo",
                    )
                ]
            ),
            issue=IssueConfig(
                problem_statement="Fix the configured bug.",
                clone_url="https://github.com/example/repo.git",
            ),
            workspace=WorkspaceConfig(),
        )
        self.assertEqual("example/repo", config.discovery.candidates[0].full_name)


if __name__ == "__main__":
    unittest.main()
