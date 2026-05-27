from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.artifacts import ArtifactEntry, ArtifactManifest


class ArtifactEntryTest(unittest.TestCase):
    """Unit tests for the ArtifactEntry Pydantic model."""

    # -- Required field construction --

    def test_minimal_required_fields(self) -> None:
        entry = ArtifactEntry(name="quality_gate", kind="json", path="quality_gate.json")
        self.assertEqual("quality_gate", entry.name)
        self.assertEqual("json", entry.kind)
        self.assertEqual("quality_gate.json", entry.path)
        self.assertTrue(entry.required)

    def test_explicit_required_false(self) -> None:
        entry = ArtifactEntry(
            name="debug_log", kind="text", path="debug.log", required=False
        )
        self.assertFalse(entry.required)

    # -- Valid kind literals --

    def test_kind_json(self) -> None:
        entry = ArtifactEntry(name="gate", kind="json", path="gate.json")
        self.assertEqual("json", entry.kind)

    def test_kind_jsonl(self) -> None:
        entry = ArtifactEntry(name="trace", kind="jsonl", path="trace.jsonl")
        self.assertEqual("jsonl", entry.kind)

    def test_kind_markdown(self) -> None:
        entry = ArtifactEntry(name="readme", kind="markdown", path="README.md")
        self.assertEqual("markdown", entry.kind)

    def test_kind_text(self) -> None:
        entry = ArtifactEntry(name="log", kind="text", path="output.txt")
        self.assertEqual("text", entry.kind)

    def test_kind_diff(self) -> None:
        entry = ArtifactEntry(name="patch", kind="diff", path="patch.diff")
        self.assertEqual("diff", entry.kind)

    # -- Invalid kind rejected --

    def test_invalid_kind_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(name="entry", kind="csv", path="data.csv")

    # -- Missing required fields --

    def test_missing_name_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(kind="json", path="gate.json")

    def test_missing_kind_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(name="gate", path="gate.json")

    def test_missing_path_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(name="gate", kind="json")


class ArtifactManifestTest(unittest.TestCase):
    """Unit tests for the ArtifactManifest Pydantic model."""

    # -- Required field construction --

    def test_minimal_manifest_with_empty_artifacts(self) -> None:
        manifest = ArtifactManifest(run_id="run-1", artifacts=[])
        self.assertEqual("run-1", manifest.run_id)
        self.assertEqual([], manifest.artifacts)

    def test_manifest_with_entries(self) -> None:
        entries = [
            ArtifactEntry(name="quality_gate", kind="json", path="quality_gate.json"),
            ArtifactEntry(name="patch", kind="diff", path="patch.diff", required=True),
        ]
        manifest = ArtifactManifest(run_id="run-2", artifacts=entries)
        self.assertEqual("run-2", manifest.run_id)
        self.assertEqual(2, len(manifest.artifacts))
        self.assertEqual("quality_gate", manifest.artifacts[0].name)
        self.assertEqual("patch", manifest.artifacts[1].name)

    # -- Missing required fields --

    def test_missing_run_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactManifest(artifacts=[])

    def test_missing_artifacts_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactManifest(run_id="run-1")

    # -- List independence --

    def test_artifacts_list_not_shared_between_instances(self) -> None:
        m1 = ArtifactManifest(run_id="r1", artifacts=[])
        m2 = ArtifactManifest(run_id="r2", artifacts=[])
        m1.artifacts.append(
            ArtifactEntry(name="gate", kind="json", path="gate.json")
        )
        self.assertEqual(1, len(m1.artifacts))
        self.assertEqual(0, len(m2.artifacts))
