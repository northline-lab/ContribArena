from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.artifacts import ArtifactEntry, ArtifactManifest


VALID_KINDS = ("json", "jsonl", "markdown", "text", "diff")


class ArtifactEntryTest(unittest.TestCase):
    """Unit tests for the ArtifactEntry Pydantic model."""

    def test_required_fields_construct(self) -> None:
        entry = ArtifactEntry(name="trace", kind="jsonl", path="trace.jsonl")

        self.assertEqual("trace", entry.name)
        self.assertEqual("jsonl", entry.kind)
        self.assertEqual("trace.jsonl", entry.path)

    def test_required_defaults_required_is_true(self) -> None:
        entry = ArtifactEntry(name="trace", kind="jsonl", path="trace.jsonl")

        self.assertTrue(entry.required)

    def test_required_false_explicit(self) -> None:
        entry = ArtifactEntry(
            name="postmortem",
            kind="markdown",
            required=False,
            path="postmortem.md",
        )

        self.assertFalse(entry.required)

    def test_all_kind_literals_accepted(self) -> None:
        for kind in VALID_KINDS:
            with self.subTest(kind=kind):
                entry = ArtifactEntry(name="file", kind=kind, path=f"file.{kind}")
                self.assertEqual(kind, entry.kind)

    def test_invalid_kind_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(name="file", kind="yaml", path="file.yaml")

    def test_missing_name_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(kind="json", path="file.json")  # type: ignore[call-arg]

    def test_missing_kind_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(name="file", path="file.json")  # type: ignore[call-arg]

    def test_missing_path_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactEntry(name="file", kind="json")  # type: ignore[call-arg]

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import ArtifactEntry as PackageArtifactEntry

        self.assertIs(PackageArtifactEntry, ArtifactEntry)


class ArtifactManifestTest(unittest.TestCase):
    """Unit tests for the ArtifactManifest Pydantic model."""

    def test_required_fields_construct_empty_artifacts(self) -> None:
        manifest = ArtifactManifest(run_id="abc123", artifacts=[])

        self.assertEqual("abc123", manifest.run_id)
        self.assertEqual([], manifest.artifacts)

    def test_construct_with_artifacts(self) -> None:
        entries = [
            ArtifactEntry(name="trace", kind="jsonl", path="trace.jsonl"),
            ArtifactEntry(
                name="postmortem",
                kind="markdown",
                required=False,
                path="postmortem.md",
            ),
        ]

        manifest = ArtifactManifest(run_id="run-1", artifacts=entries)

        self.assertEqual(2, len(manifest.artifacts))
        self.assertEqual("trace", manifest.artifacts[0].name)
        self.assertFalse(manifest.artifacts[1].required)

    def test_artifacts_field_accepts_dict_input(self) -> None:
        manifest = ArtifactManifest(
            run_id="run-2",
            artifacts=[{"name": "diff", "kind": "diff", "path": "patch.diff"}],
        )

        self.assertEqual(1, len(manifest.artifacts))
        self.assertIsInstance(manifest.artifacts[0], ArtifactEntry)
        self.assertEqual("diff", manifest.artifacts[0].kind)

    def test_invalid_nested_entry_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactManifest(
                run_id="run-3",
                artifacts=[{"name": "bad", "kind": "yaml", "path": "bad.yaml"}],
            )

    def test_missing_run_id_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactManifest(artifacts=[])  # type: ignore[call-arg]

    def test_missing_artifacts_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactManifest(run_id="abc123")  # type: ignore[call-arg]

    def test_round_trip_through_model_dump(self) -> None:
        manifest = ArtifactManifest(
            run_id="round-trip",
            artifacts=[
                ArtifactEntry(name="trace", kind="jsonl", path="trace.jsonl"),
            ],
        )

        dumped = manifest.model_dump()
        rebuilt = ArtifactManifest.model_validate(dumped)

        self.assertEqual(manifest, rebuilt)

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import ArtifactManifest as PackageArtifactManifest

        self.assertIs(PackageArtifactManifest, ArtifactManifest)


if __name__ == "__main__":
    unittest.main()
