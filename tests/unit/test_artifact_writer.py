from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.engine.artifacts import ArtifactWriter


class ArtifactWriterTest(unittest.TestCase):
    def test_manifest_records_written_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ArtifactWriter(Path(tmp), "run-1", "owner/repo", "model")
            writer.write_json("config.json", {"ok": True})
            writer.write_markdown("repo_profile.md", "# Repo")
            manifest_path = writer.finalize_manifest()

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            names = {entry["name"] for entry in manifest["artifacts"]}
            self.assertIn("config.json", names)
            self.assertIn("repo_profile.md", names)

    def test_manifest_finalize_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ArtifactWriter(Path(tmp), "run-1", "owner/repo", "model")
            writer.write_json("config.json", {"ok": True})
            first = writer.finalize_manifest()
            second = writer.finalize_manifest()

            self.assertEqual(first, second)
            manifest = json.loads(first.read_text(encoding="utf-8"))
            names = [entry["name"] for entry in manifest["artifacts"]]
            self.assertEqual(1, names.count("artifact_manifest.json"))

    def test_manifest_records_registered_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ArtifactWriter(Path(tmp), "run-1", "owner/repo", "model")
            path = writer.register("operator_events.jsonl", kind="jsonl")
            manifest_path = writer.finalize_manifest()

            self.assertTrue(path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = {entry["name"]: entry for entry in manifest["artifacts"]}
            self.assertEqual("jsonl", entries["operator_events.jsonl"]["kind"])


if __name__ == "__main__":
    unittest.main()
