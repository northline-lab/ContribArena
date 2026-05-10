from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.trace import TraceWriter


class TraceWriterTest(unittest.TestCase):
    def test_writes_jsonl_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = TraceWriter(path, "run-1")
            writer.write("run_started", "run.started", {"ok": True})

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertEqual("run-1", event["run_id"])
            self.assertEqual("run.started", event["event"])
            self.assertTrue(event["payload"]["ok"])


if __name__ == "__main__":
    unittest.main()
