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

            atomic_write_text(path, "hello")

            self.assertEqual("hello", path.read_text(encoding="utf-8"))

    def test_creates_missing_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "deep" / "out.txt"

            atomic_write_text(path, "data")

            self.assertTrue(path.parent.is_dir())
            self.assertEqual("data", path.read_text(encoding="utf-8"))

    def test_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.txt"
            path.write_text("old contents", encoding="utf-8")

            atomic_write_text(path, "new contents")

            self.assertEqual("new contents", path.read_text(encoding="utf-8"))

    def test_preserves_non_ascii_characters_with_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.txt"

            atomic_write_text(path, "caf\u00e9 \u2603 \U0001f600")

            self.assertEqual(
                "caf\u00e9 \u2603 \U0001f600",
                path.read_text(encoding="utf-8"),
            )

    def test_does_not_leave_temp_file_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            path = parent / "out.txt"

            atomic_write_text(path, "data")

            siblings = [p.name for p in parent.iterdir()]
            self.assertEqual(["out.txt"], siblings)

    def test_writes_empty_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.txt"

            atomic_write_text(path, "")

            self.assertTrue(path.exists())
            self.assertEqual("", path.read_text(encoding="utf-8"))


class AtomicWriteJsonTest(unittest.TestCase):
    def test_writes_pretty_printed_json_with_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            payload = {"b": 1, "a": 2}

            atomic_write_json(path, payload)

            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(payload, json.loads(text))

    def test_indent_is_two_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"

            atomic_write_json(path, {"key": "value"})

            text = path.read_text(encoding="utf-8")
            self.assertIn('\n  "key": "value"', text)

    def test_sort_keys_true_sorts_keys_alphabetically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"

            atomic_write_json(path, {"b": 1, "a": 2, "c": 3}, sort_keys=True)

            text = path.read_text(encoding="utf-8")
            a_pos = text.index('"a"')
            b_pos = text.index('"b"')
            c_pos = text.index('"c"')
            self.assertLess(a_pos, b_pos)
            self.assertLess(b_pos, c_pos)

    def test_sort_keys_false_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"

            atomic_write_json(path, {"b": 1, "a": 2})

            text = path.read_text(encoding="utf-8")
            self.assertLess(text.index('"b"'), text.index('"a"'))

    def test_ensure_ascii_true_escapes_non_ascii_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"

            atomic_write_json(path, {"label": "caf\u00e9"})

            text = path.read_text(encoding="utf-8")
            self.assertIn("caf\\u00e9", text)
            self.assertNotIn("caf\u00e9", text)
            self.assertEqual({"label": "caf\u00e9"}, json.loads(text))

    def test_ensure_ascii_false_preserves_non_ascii(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"

            atomic_write_json(path, {"label": "caf\u00e9"}, ensure_ascii=False)

            text = path.read_text(encoding="utf-8")
            self.assertIn("caf\u00e9", text)
            self.assertEqual({"label": "caf\u00e9"}, json.loads(text))

    def test_creates_missing_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "deep" / "out.json"

            atomic_write_json(path, {"ok": True})

            self.assertTrue(path.parent.is_dir())
            self.assertEqual({"ok": True}, json.loads(path.read_text(encoding="utf-8")))

    def test_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            path.write_text("stale", encoding="utf-8")

            atomic_write_json(path, {"fresh": True})

            self.assertEqual({"fresh": True}, json.loads(path.read_text(encoding="utf-8")))

    def test_writes_list_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"

            atomic_write_json(path, [1, 2, 3])

            self.assertEqual([1, 2, 3], json.loads(path.read_text(encoding="utf-8")))

    def test_does_not_leave_temp_file_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            path = parent / "out.json"

            atomic_write_json(path, {"ok": True})

            siblings = [p.name for p in parent.iterdir()]
            self.assertEqual(["out.json"], siblings)


if __name__ == "__main__":
    unittest.main()
