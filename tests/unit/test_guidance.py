from __future__ import annotations

import unittest

from contribarena.config.schema import (
    DiscoveryConfig,
    GuidanceConfig,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.guidance import (
    GuidanceInstallResult,
    guidance_artifact_payload,
    install_guidance_sidecar,
)
from contribarena.models import CommandResult


def _config(*, guidance_enabled: bool = True) -> RunConfig:
    return RunConfig(
        run=RunSection(mode="shadow"),
        discovery=DiscoveryConfig(query="agent framework"),
        workspace=WorkspaceConfig(),
        guidance=GuidanceConfig(enabled=guidance_enabled),
    )


def _command_result(*, exit_code: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(
        command="install guidance",
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_seconds=0.01,
    )


class FakeWorkspace:
    def __init__(self, result: CommandResult | Exception) -> None:
        self.result = result
        self.commands: list[str] = []

    def run(self, command: str) -> CommandResult:
        self.commands.append(command)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class GuidanceInstallSidecarTest(unittest.TestCase):
    def test_install_guidance_sidecar_writes_entry_and_manifest(self) -> None:
        workspace = FakeWorkspace(_command_result())

        result = install_guidance_sidecar(
            workspace,
            _config(),
            run_id="run-123",
            repo_full_name="owner/repo",
        )

        self.assertTrue(result.installed)
        self.assertTrue(result.enabled)
        self.assertEqual("", result.error)
        self.assertIsNotNone(result.command)
        self.assertEqual("run-123", result.manifest["run_id"])
        self.assertEqual("owner/repo", result.manifest["repo_full_name"])
        self.assertEqual("shadow", result.manifest["run_mode"])
        self.assertEqual(1, len(workspace.commands))
        self.assertIn(".contribarena/guidance/guidance_entry.md", workspace.commands[0])
        self.assertIn(".contribarena/guidance/guidance_manifest.json", workspace.commands[0])

    def test_install_guidance_sidecar_skips_when_disabled(self) -> None:
        workspace = FakeWorkspace(_command_result())

        result = install_guidance_sidecar(
            workspace,
            _config(guidance_enabled=False),
            run_id="run-123",
            repo_full_name="owner/repo",
        )

        self.assertFalse(result.installed)
        self.assertFalse(result.enabled)
        self.assertEqual("guidance_disabled", result.skipped_reason)
        self.assertFalse(result.manifest["enabled"])
        self.assertEqual([], workspace.commands)

    def test_install_guidance_sidecar_reports_command_failure(self) -> None:
        workspace = FakeWorkspace(_command_result(exit_code=2, stdout="fallback", stderr="boom"))

        result = install_guidance_sidecar(
            workspace,
            _config(),
            run_id="run-123",
            repo_full_name="owner/repo",
        )

        self.assertFalse(result.installed)
        self.assertTrue(result.enabled)
        self.assertEqual("boom", result.error)
        self.assertEqual(1, len(workspace.commands))

    def test_install_guidance_sidecar_reports_workspace_exception(self) -> None:
        workspace = FakeWorkspace(RuntimeError("workspace unavailable"))

        result = install_guidance_sidecar(
            workspace,
            _config(),
            run_id="run-123",
            repo_full_name="owner/repo",
        )

        self.assertFalse(result.installed)
        self.assertTrue(result.enabled)
        self.assertIsNone(result.command)
        self.assertEqual("workspace unavailable", result.error)


class GuidanceArtifactPayloadTest(unittest.TestCase):
    def test_guidance_artifact_payload_marks_available_and_degraded(self) -> None:
        result = GuidanceInstallResult(
            installed=False,
            command=None,
            manifest={"run_id": "run-123"},
            enabled=True,
            error="install failed",
            skipped_reason="",
        )

        payload = guidance_artifact_payload(result)

        self.assertEqual("1", payload["schema_version"])
        self.assertTrue(payload["enabled"])
        self.assertFalse(payload["installed"])
        self.assertFalse(payload["available"])
        self.assertTrue(payload["degraded"])
        self.assertEqual(".contribarena/guidance", payload["sidecar_path"])
        self.assertEqual(".contribarena/guidance/guidance_entry.md", payload["entry_path"])
        self.assertEqual(".contribarena/guidance/guidance_manifest.json", payload["manifest_path"])
        self.assertEqual({"run_id": "run-123"}, payload["manifest"])
        self.assertEqual("install failed", payload["error"])


if __name__ == "__main__":
    unittest.main()
