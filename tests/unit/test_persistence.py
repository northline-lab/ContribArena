from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.engine.persistence import atomic_write_json, atomic_write_text


class AtomicWriteTextTest(unittest.TestCase):
    def test_writes_text_to_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.txt"
            atomic_write_text(path, "hello world")
            self.assertEqual("hello world", path.read_text(encoding="utf-8"))

    def test_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "deeper" / "out.txt"
            atomic_write_text(path, "nested")
            self.assertTrue(path.exists())
            self.assertEqual("nested", path.read_text(encoding="utf-8"))

    def test_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.txt"
            path.write_text("old", encoding="utf-8")
            atomic_write_text(path, "new")
            self.assertEqual("new", path.read_text(encoding="utf-8"))

    def test_uses_utf8_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.txt"
            atomic_write_text(path, "caf\u00e9 \u2603")
            self.assertEqual("caf\u00e9 \u2603", path.read_text(encoding="utf-8"))
            # Confirm raw bytes are UTF-8 (multi-byte sequences present).
            self.assertIn(b"\xc3\xa9", path.read_bytes())

    def test_leaves_no_temp_files_behind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.txt"
            atomic_write_text(path, "content")
            entries = list(Path(tmp).iterdir())
            self.assertEqual([path], entries)


class AtomicWriteJsonTest(unittest.TestCase):
    def test_writes_json_with_indent_and_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            atomic_write_json(path, {"a": 1, "b": [2, 3]})
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n"))
            self.assertEqual({"a": 1, "b": [2, 3]}, json.loads(text))
            # Pretty-printed output uses two-space indent.
            self.assertIn("\n  ", text)

    def test_sort_keys_orders_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            atomic_write_json(path, {"b": 1, "a": 2}, sort_keys=True)
            text = path.read_text(encoding="utf-8")
            self.assertLess(text.index("\"a\""), text.index("\"b\""))

    def test_ensure_ascii_true_escapes_non_ascii(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            atomic_write_json(path, {"v": "caf\u00e9"}, ensure_ascii=True)
            text = path.read_text(encoding="utf-8")
            self.assertIn("\\u00e9", text)
            self.assertNotIn("caf\u00e9", text)

    def test_ensure_ascii_false_preserves_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            atomic_write_json(path, {"v": "caf\u00e9"}, ensure_ascii=False)
            text = path.read_text(encoding="utf-8")
            self.assertIn("caf\u00e9", text)
            self.assertNotIn("\\u00e9", text)

    def test_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "out.json"
            atomic_write_json(path, [1, 2, 3])
            self.assertEqual([1, 2, 3], json.loads(path.read_text(encoding="utf-8")))

    def test_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            atomic_write_json(path, {"v": 1})
            atomic_write_json(path, {"v": 2})
            self.assertEqual({"v": 2}, json.loads(path.read_text(encoding="utf-8")))

    def test_leaves_no_temp_files_behind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            atomic_write_json(path, {"v": 1})
            names = [entry.name for entry in Path(tmp).iterdir()]
            self.assertEqual([path.name], names)
            for name in names:
                self.assertFalse(name.endswith(".tmp"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
