from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from contribarena.config import load_run_config, write_starter_config
from contribarena.config.schema import (
    DiscoveryConfig,
    GovernanceConfig,
    IssueConfig,
    OwnedRepositoryPolicy,
    PrSubmissionConfig,
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
            self.assertEqual("always", config.workspace.cleanup_policy)
            self.assertFalse(config.governance.live_enabled)
            self.assertFalse(config.controller.enabled)
            self.assertEqual(
                "openai/openai-agents-python", config.discovery.candidates[0].full_name
            )

    def test_load_run_config_loads_dotenv_without_overriding_environment(self) -> None:
        old_token = os.environ.pop("GITHUB_TOKEN", None)
        old_existing = os.environ.get("EXISTING_ENV")
        os.environ["EXISTING_ENV"] = "from-process"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "run_config.yaml"
                write_starter_config(path)
                (Path(tmp) / ".env").write_text(
                    "GITHUB_TOKEN=from-dotenv\nEXISTING_ENV=from-dotenv\n",
                    encoding="utf-8",
                )

                load_run_config(path)

                self.assertEqual("from-dotenv", os.environ.get("GITHUB_TOKEN"))
                self.assertEqual("from-process", os.environ.get("EXISTING_ENV"))
        finally:
            if old_token is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = old_token
            if old_existing is None:
                os.environ.pop("EXISTING_ENV", None)
            else:
                os.environ["EXISTING_ENV"] = old_existing

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

    def test_owned_live_requires_configured_owned_repository(self) -> None:
        with self.assertRaisesRegex(ValidationError, "governance.owned_repositories"):
            RunConfig(
                run=RunSection(mode="owned_live"),
                discovery=DiscoveryConfig(
                    candidates=[
                        RepoCandidate(
                            owner="example",
                            repo="repo",
                            url="https://github.com/example/repo",
                        )
                    ]
                ),
                workspace=WorkspaceConfig(),
            )

        config = RunConfig(
            run=RunSection(mode="owned_live"),
            discovery=DiscoveryConfig(
                candidates=[
                    RepoCandidate(
                        owner="example",
                        repo="repo",
                        url="https://github.com/example/repo",
                    )
                ]
            ),
            workspace=WorkspaceConfig(),
            governance=GovernanceConfig(
                owned_repositories=[
                    OwnedRepositoryPolicy(owner="example", repo="repo", default_branch="main")
                ]
            ),
        )
        self.assertEqual("owned_live", config.run.mode)
        self.assertEqual("example/repo", config.governance.owned_repositories[0].full_name)
        self.assertEqual("fork", config.governance.owned_repositories[0].pr_submission.strategy)

    def test_owned_repository_can_select_upstream_branch_strategy(self) -> None:
        policy = OwnedRepositoryPolicy(
            owner="example",
            repo="repo",
            pr_submission=PrSubmissionConfig(strategy="upstream_branch"),
        )

        self.assertEqual("upstream_branch", policy.pr_submission.strategy)


if __name__ == "__main__":
    unittest.main()
