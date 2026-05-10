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


if __name__ == "__main__":
    unittest.main()
