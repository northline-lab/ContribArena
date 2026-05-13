from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import MemoryConfig
from contribarena.memory.history_index import HistoryIndex
from contribarena.memory.service import MemoryService


class MemoryServiceTest(unittest.TestCase):
    def test_derives_contributing_fact_and_agent_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = MemoryService(
                MemoryConfig(root=Path(tmp) / "memory"),
                run_id="run-1",
                repo_full_name="example/repo",
            )

            service.record_tool_observation(
                "1",
                "aci_view",
                {"path": "repo/CONTRIBUTING.md"},
                {"success": True, "output": "guidance"},
            )
            service.note_agent_memory(
                "run",
                "Project uses pytest with a token=ghs_secretsecretsecretsecret.",
                ["testing"],
                "medium",
                "unit-test",
            )

            self.assertEqual("repo/CONTRIBUTING.md", service.working.facts["contributing_checked"].value)
            self.assertEqual("harness", service.working.facts["contributing_checked"].source)
            self.assertIn("testing", service.working.facts)
            self.assertNotIn("ghs_secret", service.working.facts["testing"].value)

    def test_history_index_indexes_text_artifacts_with_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "postmortem.md").write_text(
                "Provider failure with token=ghs_secretsecretsecretsecret.\n",
                encoding="utf-8",
            )
            (run_dir / "binary.bin").write_bytes(b"\x00\x01")
            index = HistoryIndex(root / "memory")

            result = index.index_run_dir(run_dir, run_id="run-1", repo_full_name="example/repo")
            hits = index.search("Provider failure", repo_full_name="example/repo")

            self.assertEqual(1, result.entries_written)
            self.assertEqual(["postmortem.md"], result.indexed_sources)
            self.assertEqual(1, len(hits))
            self.assertEqual("history_index", hits[0].source)
            self.assertNotIn("ghs_secret", hits[0].text)
            self.assertIn("token=***", hits[0].text)


if __name__ == "__main__":
    unittest.main()
