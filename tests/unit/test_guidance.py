from __future__ import annotations

import unittest

from contribarena.config.schema import (
    DiscoveryConfig,
    GuidanceConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.guidance import (
    GuidanceInstallResult,
    guidance_artifact_payload,
    _guidance_manifest,
)


def _config(mode: str = "shadow", guidance_enabled: bool = True) -> RunConfig:
    return RunConfig(
        run=RunSection(mode=mode, model="local-stub"),
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
        guidance=GuidanceConfig(enabled=guidance_enabled),
    )


class GuidanceManifestTest(unittest.TestCase):
    def test_manifest_includes_run_id_and_repo(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "run-abc", "example/repo")
        self.assertEqual(manifest["run_id"], "run-abc")
        self.assertEqual(manifest["repo_full_name"], "example/repo")

    def test_manifest_reflects_run_mode(self) -> None:
        config = _config(mode="shadow")
        manifest = _guidance_manifest(config, "r", "a/b")
        self.assertEqual(manifest["run_mode"], "shadow")

    def test_manifest_reflects_guidance_enabled_flag(self) -> None:
        enabled = _config(guidance_enabled=True)
        disabled = _config(guidance_enabled=False)
        self.assertTrue(
            _guidance_manifest(enabled, "r", "a/b")["enabled"]
        )
        self.assertFalse(
            _guidance_manifest(disabled, "r", "a/b")["enabled"]
        )

    def test_manifest_has_schema_version(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "r", "a/b")
        self.assertEqual(manifest["schema_version"], "1")

    def test_manifest_sources_entry_present_when_guidance_enabled(self) -> None:
        config = _config(guidance_enabled=True)
        manifest = _guidance_manifest(config, "r", "a/b")
        sources = manifest["sources"]
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["kind"], "guidance_entry")
        self.assertTrue(sources[0]["present"])

    def test_manifest_sources_entry_not_present_when_guidance_disabled(self) -> None:
        config = _config(guidance_enabled=False)
        manifest = _guidance_manifest(config, "r", "a/b")
        sources = manifest["sources"]
        self.assertEqual(len(sources), 1)
        self.assertFalse(sources[0]["present"])

    def test_manifest_includes_expected_repo_sources(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "r", "a/b")
        expected = manifest["expected_repo_sources"]
        self.assertIn("AGENTS.md", expected)
        self.assertIn("CONTRIBUTING.md", expected)
        self.assertIn(".github/PULL_REQUEST_TEMPLATE.md", expected)
        self.assertIn("SECURITY.md", expected)
        self.assertIn("CODE_OF_CONDUCT.md", expected)

    def test_manifest_includes_notes(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "r", "a/b")
        self.assertIsInstance(manifest["notes"], list)
        self.assertGreater(len(manifest["notes"]), 0)


class GuidanceArtifactPayloadTest(unittest.TestCase):
    def test_installed_success_returns_available_true(self) -> None:
        result = GuidanceInstallResult(
            installed=True,
            command=None,
            manifest={"key": "value"},
        )
        payload = guidance_artifact_payload(result)
        self.assertTrue(payload["available"])
        self.assertTrue(payload["installed"])
        self.assertFalse(payload["degraded"])
        self.assertEqual(payload["error"], "")

    def test_not_installed_returns_available_false(self) -> None:
        result = GuidanceInstallResult(
            installed=False,
            command=None,
            manifest={},
        )
        payload = guidance_artifact_payload(result)
        self.assertFalse(payload["available"])
        self.assertFalse(payload["installed"])

    def test_error_marks_degraded_true(self) -> None:
        result = GuidanceInstallResult(
            installed=False,
            command=None,
            manifest={},
            error="something went wrong",
        )
        payload = guidance_artifact_payload(result)
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["error"], "something went wrong")

    def test_guidance_disabled_skipped_reason_present(self) -> None:
        result = GuidanceInstallResult(
            installed=False,
            command=None,
            manifest={},
            enabled=False,
            skipped_reason="guidance_disabled",
        )
        payload = guidance_artifact_payload(result)
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["skipped_reason"], "guidance_disabled")

    def test_payload_includes_sidecar_paths(self) -> None:
        result = GuidanceInstallResult(
            installed=True,
            command=None,
            manifest={},
        )
        payload = guidance_artifact_payload(result)
        self.assertEqual(
            payload["sidecar_path"], ".contribarena/guidance"
        )
        self.assertEqual(
            payload["entry_path"],
            ".contribarena/guidance/guidance_entry.md",
        )
        self.assertEqual(
            payload["manifest_path"],
            ".contribarena/guidance/guidance_manifest.json",
        )

    def test_payload_includes_manifest(self) -> None:
        manifest = {"run_id": "test-123"}
        result = GuidanceInstallResult(
            installed=True,
            command=None,
            manifest=manifest,
        )
        payload = guidance_artifact_payload(result)
        self.assertEqual(payload["manifest"], manifest)

    def test_payload_schema_version_is_one(self) -> None:
        result = GuidanceInstallResult(
            installed=True,
            command=None,
            manifest={},
        )
        payload = guidance_artifact_payload(result)
        self.assertEqual(payload["schema_version"], "1")


if __name__ == "__main__":
    unittest.main()
