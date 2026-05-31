from __future__ import annotations

import unittest

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    GovernanceConfig,
    GuidanceConfig,
    OwnedRepositoryPolicy,
    PrSubmissionConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.guidance import (
    GuidanceInstallResult,
    _guidance_entry,
    _guidance_manifest,
    guidance_artifact_payload,
)


def _config(mode: str = "shadow") -> RunConfig:
    kwargs: dict[str, object] = dict(
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
        workspace=WorkspaceConfig(command_timeout_seconds=10),
        artifacts=ArtifactConfig(),
    )
    if mode in ("owned_live", "external_live"):
        kwargs["governance"] = GovernanceConfig(
            live_enabled=True,
            owned_repositories=[
                OwnedRepositoryPolicy(
                    owner="example",
                    repo="repo",
                    default_branch="main",
                    pr_submission=PrSubmissionConfig(strategy="fork", fork_owner="contribarena-bot"),
                )
            ],
        )
    return RunConfig(**kwargs)  # type: ignore[arg-type]


class GuidanceManifestTest(unittest.TestCase):
    """Tests for _guidance_manifest pure helper."""

    def test_manifest_returns_dict_with_top_level_keys(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "run-1", "example/repo")
        self.assertIsInstance(manifest, dict)
        for key in ("schema_version", "run_id", "repo_full_name", "run_mode", "enabled", "sources", "expected_repo_sources", "notes"):
            self.assertIn(key, manifest)

    def test_manifest_schema_version_is_one(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "run-1", "example/repo")
        self.assertEqual("1", manifest["schema_version"])

    def test_manifest_run_id_is_preserved(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "custom-run-id", "example/repo")
        self.assertEqual("custom-run-id", manifest["run_id"])

    def test_manifest_repo_full_name_is_preserved(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "run-1", "owner/name")
        self.assertEqual("owner/name", manifest["repo_full_name"])

    def test_manifest_run_mode_reflects_config(self) -> None:
        for mode in ("shadow", "owned_live", "external_live", "dry_run"):
            with self.subTest(mode=mode):
                config = _config(mode=mode)
                manifest = _guidance_manifest(config, "run-1", "example/repo")
                self.assertEqual(mode, manifest["run_mode"])

    def test_manifest_enabled_reflects_guidance_config(self) -> None:
        config = _config()
        config.guidance = GuidanceConfig(enabled=True)
        manifest = _guidance_manifest(config, "run-1", "example/repo")
        self.assertTrue(manifest["enabled"])

        config.guidance = GuidanceConfig(enabled=False)
        manifest = _guidance_manifest(config, "run-1", "example/repo")
        self.assertFalse(manifest["enabled"])

    def test_manifest_sources_contains_single_guidance_entry(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "run-1", "example/repo")
        sources = manifest["sources"]
        self.assertIsInstance(sources, list)
        self.assertEqual(1, len(sources))
        self.assertEqual("guidance_entry", sources[0]["kind"])
        self.assertEqual(".contribarena/guidance/guidance_entry.md", sources[0]["path"])

    def test_manifest_sources_present_tracks_guidance_enabled(self) -> None:
        config = _config()
        config.guidance = GuidanceConfig(enabled=True)
        manifest = _guidance_manifest(config, "run-1", "example/repo")
        self.assertTrue(manifest["sources"][0]["present"])

        config.guidance = GuidanceConfig(enabled=False)
        manifest = _guidance_manifest(config, "run-1", "example/repo")
        self.assertFalse(manifest["sources"][0]["present"])

    def test_manifest_expected_repo_sources_contains_all_known_files(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "run-1", "example/repo")
        expected = manifest["expected_repo_sources"]
        self.assertIn("AGENTS.md", expected)
        self.assertIn("CONTRIBUTING.md", expected)
        self.assertIn(".github/PULL_REQUEST_TEMPLATE.md", expected)
        self.assertIn(".github/PULL_REQUEST_TEMPLATE/", expected)
        self.assertIn(".github/ISSUE_TEMPLATE/", expected)
        self.assertIn("SECURITY.md", expected)
        self.assertIn("CODE_OF_CONDUCT.md", expected)

    def test_manifest_notes_is_nonempty_list(self) -> None:
        config = _config()
        manifest = _guidance_manifest(config, "run-1", "example/repo")
        self.assertIsInstance(manifest["notes"], list)
        self.assertGreater(len(manifest["notes"]), 0)


class GuidanceEntryTest(unittest.TestCase):
    """Tests for _guidance_entry pure helper."""

    def test_entry_returns_string(self) -> None:
        config = _config()
        entry = _guidance_entry(config)
        self.assertIsInstance(entry, str)
        self.assertGreater(len(entry), 0)

    def test_entry_starts_with_title(self) -> None:
        config = _config()
        entry = _guidance_entry(config)
        self.assertTrue(entry.startswith("# ContribArena Agent Guidance"))

    def test_entry_includes_run_mode(self) -> None:
        for mode in ("shadow", "owned_live", "external_live", "dry_run"):
            with self.subTest(mode=mode):
                config = _config(mode=mode)
                entry = _guidance_entry(config)
                self.assertIn(f"Run mode: {mode}", entry)

    def test_entry_includes_phase_instructions(self) -> None:
        config = _config()
        entry = _guidance_entry(config)
        self.assertIn("Call aci_runtime_get_context(scope='run')", entry)
        self.assertIn("Scout/project", entry)
        self.assertIn("Scout/opportunity", entry)

    def test_entry_live_pr_recipe_present_only_for_live_modes(self) -> None:
        for mode, expect_recipe in (
            ("owned_live", True),
            ("external_live", True),
            ("shadow", False),
            ("dry_run", False),
        ):
            with self.subTest(mode=mode, expect_recipe=expect_recipe):
                config = _config(mode=mode)
                entry = _guidance_entry(config)
                if expect_recipe:
                    self.assertIn("Live PR submission recipe", entry)
                    self.assertIn("github_prepare_fork", entry)
                    self.assertIn("github_open_pr returns", entry)
                else:
                    self.assertNotIn("Live PR submission recipe", entry)

    def test_entry_live_pr_recipe_references_all_github_tools(self) -> None:
        config = _config(mode="owned_live")
        entry = _guidance_entry(config)
        for tool in ("github_prepare_fork", "github_prepare_branch", "github_commit", "github_push_branch", "github_open_pr"):
            self.assertIn(tool, entry)

    def test_entry_includes_branch_naming_convention(self) -> None:
        config = _config(mode="owned_live")
        entry = _guidance_entry(config)
        self.assertIn("contribarena/<run_id>-<short-slug>", entry)

    def test_entry_includes_aci_tool_reference(self) -> None:
        config = _config()
        entry = _guidance_entry(config)
        self.assertIn("aci_view", entry)
        self.assertIn("aci_search", entry)
        self.assertIn("aci_find_files", entry)


class GuidanceArtifactPayloadTest(unittest.TestCase):
    """Tests for guidance_artifact_payload pure helper."""

    def _result(self, **kwargs: object) -> GuidanceInstallResult:
        defaults: dict[str, object] = {
            "installed": True,
            "command": None,
            "manifest": {"schema_version": "1"},
            "enabled": True,
            "error": "",
            "skipped_reason": "",
        }
        defaults.update(kwargs)
        return GuidanceInstallResult(**defaults)  # type: ignore[arg-type]

    def test_payload_returns_dict(self) -> None:
        result = self._result()
        payload = guidance_artifact_payload(result)
        self.assertIsInstance(payload, dict)

    def test_payload_schema_version_is_one(self) -> None:
        result = self._result()
        payload = guidance_artifact_payload(result)
        self.assertEqual("1", payload["schema_version"])

    def test_payload_enabled_reflects_result(self) -> None:
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                result = self._result(enabled=enabled)
                payload = guidance_artifact_payload(result)
                self.assertEqual(enabled, payload["enabled"])
                # available reflects installed (not enabled)
                self.assertEqual(result.installed, payload["available"])

    def test_payload_installed_and_available_match(self) -> None:
        for installed in (True, False):
            with self.subTest(installed=installed):
                result = self._result(installed=installed)
                payload = guidance_artifact_payload(result)
                self.assertEqual(installed, payload["installed"])
                self.assertEqual(installed, payload["available"])

    def test_payload_degraded_is_true_when_error_present(self) -> None:
        result = self._result(error="some error")
        payload = guidance_artifact_payload(result)
        self.assertTrue(payload["degraded"])

        result = self._result(error="")
        payload = guidance_artifact_payload(result)
        self.assertFalse(payload["degraded"])

    def test_payload_contains_fixed_paths(self) -> None:
        result = self._result()
        payload = guidance_artifact_payload(result)
        self.assertEqual(".contribarena/guidance", payload["sidecar_path"])
        self.assertEqual(".contribarena/guidance/guidance_entry.md", payload["entry_path"])
        self.assertEqual(".contribarena/guidance/guidance_manifest.json", payload["manifest_path"])

    def test_payload_passes_through_manifest_and_error(self) -> None:
        manifest = {"schema_version": "1", "custom": True}
        result = self._result(manifest=manifest, error="install failed", skipped_reason="guidance_disabled")
        payload = guidance_artifact_payload(result)
        self.assertEqual(manifest, payload["manifest"])
        self.assertEqual("install failed", payload["error"])
        self.assertEqual("guidance_disabled", payload["skipped_reason"])


class GuidanceInstallResultTest(unittest.TestCase):
    """Tests for GuidanceInstallResult dataclass."""

    def test_defaults(self) -> None:
        result = GuidanceInstallResult(installed=False, command=None, manifest={})
        self.assertFalse(result.installed)
        self.assertIsNone(result.command)
        self.assertEqual({}, result.manifest)
        self.assertTrue(result.enabled)
        self.assertEqual("", result.error)
        self.assertEqual("", result.skipped_reason)

    def test_explicit_values(self) -> None:
        result = GuidanceInstallResult(
            installed=True,
            command=None,
            manifest={"key": "value"},
            enabled=False,
            error="oops",
            skipped_reason="disabled",
        )
        self.assertTrue(result.installed)
        self.assertEqual({"key": "value"}, result.manifest)
        self.assertFalse(result.enabled)
        self.assertEqual("oops", result.error)
        self.assertEqual("disabled", result.skipped_reason)

    def test_is_dataclass(self) -> None:
        from dataclasses import is_dataclass
        self.assertTrue(is_dataclass(GuidanceInstallResult))

    def test_equality_semantics(self) -> None:
        a = GuidanceInstallResult(installed=True, command=None, manifest={})
        b = GuidanceInstallResult(installed=True, command=None, manifest={})
        self.assertEqual(a, b)
        c = GuidanceInstallResult(installed=False, command=None, manifest={})
        self.assertNotEqual(a, c)
