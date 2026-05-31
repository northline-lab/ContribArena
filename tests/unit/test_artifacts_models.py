from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.artifacts import ArtifactEntry, ArtifactManifest
from contribarena.models import ArtifactEntry as ImportArtifactEntry, ArtifactManifest as ImportArtifactManifest

# --- ArtifactEntry ---

VALID_KINDS = ("json", "jsonl", "markdown", "text", "diff")


class ArtifactEntryRequiredFieldsTest(unittest.TestCase):
    """Test that required fields are enforced."""

    def test_all_required_fields_present(self) -> None:
        entry = ArtifactEntry(name="trace.jsonl", kind="jsonl", path="trace.jsonl")
        self.assertEqual(entry.name, "trace.jsonl")
        self.assertEqual(entry.kind, "jsonl")
        self.assertEqual(entry.path, "trace.jsonl")

    def test_missing_name_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(kind="json", path="config.json")  # type: ignore[call-arg]

    def test_missing_kind_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(name="config", path="config.json")  # type: ignore[call-arg]

    def test_missing_path_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(name="config", kind="json")  # type: ignore[call-arg]


class ArtifactEntryDefaultsTest(unittest.TestCase):
    """Test default values."""

    def test_required_defaults_to_true(self) -> None:
        entry = ArtifactEntry(name="trace.jsonl", kind="jsonl", path="trace.jsonl")
        self.assertTrue(entry.required)

    def test_required_can_be_set_false(self) -> None:
        entry = ArtifactEntry(name="optional.md", kind="markdown", path="optional.md", required=False)
        self.assertFalse(entry.required)


class ArtifactEntryKindLiteralTest(unittest.TestCase):
    """Test the Literal type constraint on kind."""

    def test_all_kind_literals_accepted(self) -> None:
        for kind in VALID_KINDS:
            entry = ArtifactEntry(name=f"file.{kind}", kind=kind, path=f"file.{kind}")
            self.assertEqual(entry.kind, kind)

    def test_invalid_kind_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(name="file.csv", kind="csv", path="file.csv")


class ArtifactEntryExplicitValuesTest(unittest.TestCase):
    """Test explicit value construction."""

    def test_all_explicit_values(self) -> None:
        entry = ArtifactEntry(
            name="patch.diff",
            kind="diff",
            required=True,
            path="artifacts/patch.diff",
        )
        self.assertEqual(entry.name, "patch.diff")
        self.assertEqual(entry.kind, "diff")
        self.assertTrue(entry.required)
        self.assertEqual(entry.path, "artifacts/patch.diff")

    def test_dict_input_coerces_to_model(self) -> None:
        entry = ArtifactEntry.model_validate({
            "name": "run_summary.json",
            "kind": "json",
            "path": "run_summary.json",
        })
        self.assertIsInstance(entry, ArtifactEntry)
        self.assertEqual(entry.kind, "json")


class ArtifactEntrySerializationTest(unittest.TestCase):
    """Test JSON serialization round-trip."""

    def test_model_dump_json_round_trip(self) -> None:
        entry = ArtifactEntry(name="trace.jsonl", kind="jsonl", path="trace.jsonl")
        data = entry.model_dump(mode="json")
        restored = ArtifactEntry.model_validate(data)
        self.assertEqual(entry, restored)

    def test_model_dump_preserves_required_false(self) -> None:
        entry = ArtifactEntry(name="optional.md", kind="markdown", path="optional.md", required=False)
        data = entry.model_dump(mode="json")
        self.assertFalse(data["required"])
        restored = ArtifactEntry.model_validate(data)
        self.assertFalse(restored.required)


# --- ArtifactManifest ---


class ArtifactManifestRequiredFieldsTest(unittest.TestCase):
    """Test that required fields are enforced."""

    def test_all_required_fields_present(self) -> None:
        entry = ArtifactEntry(name="trace.jsonl", kind="jsonl", path="trace.jsonl")
        manifest = ArtifactManifest(run_id="run-1", artifacts=[entry])
        self.assertEqual(manifest.run_id, "run-1")
        self.assertEqual(len(manifest.artifacts), 1)

    def test_missing_run_id_raises(self) -> None:
        entry = ArtifactEntry(name="trace.jsonl", kind="jsonl", path="trace.jsonl")
        with self.assertRaises(ValidationError):
            ArtifactManifest(artifacts=[entry])  # type: ignore[call-arg]

    def test_missing_artifacts_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactManifest(run_id="run-1")  # type: ignore[call-arg]

    def test_empty_artifacts_list_valid(self) -> None:
        manifest = ArtifactManifest(run_id="run-1", artifacts=[])
        self.assertEqual(manifest.artifacts, [])


class ArtifactManifestExplicitValuesTest(unittest.TestCase):
    """Test explicit value construction."""

    def test_multiple_artifacts(self) -> None:
        entries = [
            ArtifactEntry(name="trace.jsonl", kind="jsonl", path="trace.jsonl"),
            ArtifactEntry(name="patch.diff", kind="diff", path="patch.diff", required=True),
            ArtifactEntry(name="postmortem.md", kind="markdown", path="postmortem.md", required=False),
        ]
        manifest = ArtifactManifest(run_id="run-42", artifacts=entries)
        self.assertEqual(manifest.run_id, "run-42")
        self.assertEqual(len(manifest.artifacts), 3)
        self.assertEqual(manifest.artifacts[0].kind, "jsonl")
        self.assertEqual(manifest.artifacts[1].kind, "diff")
        self.assertEqual(manifest.artifacts[2].kind, "markdown")
        self.assertFalse(manifest.artifacts[2].required)

    def test_dict_input_coerces_nested_entries(self) -> None:
        manifest = ArtifactManifest.model_validate({
            "run_id": "run-99",
            "artifacts": [
                {"name": "config.json", "kind": "json", "path": "config.json"},
                {"name": "readme.md", "kind": "markdown", "path": "readme.md"},
            ],
        })
        self.assertEqual(manifest.run_id, "run-99")
        self.assertEqual(len(manifest.artifacts), 2)
        self.assertIsInstance(manifest.artifacts[0], ArtifactEntry)
        self.assertIsInstance(manifest.artifacts[1], ArtifactEntry)


class ArtifactManifestSerializationTest(unittest.TestCase):
    """Test JSON serialization round-trip."""

    def test_model_dump_json_round_trip(self) -> None:
        entries = [
            ArtifactEntry(name="trace.jsonl", kind="jsonl", path="trace.jsonl"),
            ArtifactEntry(name="quality_gate.json", kind="json", path="quality_gate.json"),
        ]
        manifest = ArtifactManifest(run_id="run-1", artifacts=entries)
        data = manifest.model_dump(mode="json")
        restored = ArtifactManifest.model_validate(data)
        self.assertEqual(manifest, restored)


class ArtifactsImportTest(unittest.TestCase):
    """Test that both symbols are importable from the models package."""

    def test_artifact_entry_importable(self) -> None:
        self.assertIs(ImportArtifactEntry, ArtifactEntry)

    def test_artifact_manifest_importable(self) -> None:
        self.assertIs(ImportArtifactManifest, ArtifactManifest)


if __name__ == "__main__":
    unittest.main()
