from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from contribarena.config.loader import _clean_env_value, _load_dotenv, _load_yaml_like


class CleanEnvValueTest(unittest.TestCase):
    def test_strips_double_quotes(self) -> None:
        self.assertEqual("hello", _clean_env_value('"hello"'))

    def test_strips_single_quotes(self) -> None:
        self.assertEqual("hello", _clean_env_value("'hello'"))

    def test_preserves_unquoted_value(self) -> None:
        self.assertEqual("plain", _clean_env_value("plain"))

    def test_preserves_mismatched_quotes(self) -> None:
        self.assertEqual('"hello\'', _clean_env_value('"hello\''))

    def test_preserves_short_string(self) -> None:
        self.assertEqual("a", _clean_env_value("a"))

    def test_preserves_empty_string(self) -> None:
        self.assertEqual("", _clean_env_value(""))

    def test_preserves_value_with_internal_quotes(self) -> None:
        self.assertEqual("a='b'", _clean_env_value("a='b'"))


class LoadYamlLikeTest(unittest.TestCase):
    def test_parses_yaml_dict(self) -> None:
        result = _load_yaml_like("key: value\nnum: 1")
        self.assertEqual({"key": "value", "num": 1}, result)

    def test_parses_json_dict(self) -> None:
        result = _load_yaml_like('{"key": "value"}')
        self.assertEqual({"key": "value"}, result)

    def test_parses_json_dict_with_leading_whitespace(self) -> None:
        result = _load_yaml_like('  {"key": "value"}')
        self.assertEqual({"key": "value"}, result)

    def test_raises_value_error_for_yaml_list(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _load_yaml_like("- item1\n- item2")
        self.assertIn("config root must be a mapping", str(ctx.exception))

    def test_raises_value_error_for_json_list(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _load_yaml_like("[1, 2, 3]")
        self.assertIn("config root must be a mapping", str(ctx.exception))

    def test_raises_value_error_for_yaml_scalar(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _load_yaml_like("42")
        self.assertIn("config root must be a mapping", str(ctx.exception))

    def test_parses_nested_yaml_dict(self) -> None:
        result = _load_yaml_like("a:\n  b:\n    c: 1")
        self.assertEqual({"a": {"b": {"c": 1}}}, result)


class LoadDotenvTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old = {}

    def tearDown(self) -> None:
        for key, old_value in self._old.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value

    def _snapshot(self, *keys: str) -> None:
        for key in keys:
            self._old[key] = os.environ.get(key)

    def test_loads_key_value_pair(self) -> None:
        self._snapshot("TEST_LOADER_KEY")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("TEST_LOADER_KEY=hello\n", encoding="utf-8")
            _load_dotenv(path)
        self.assertEqual("hello", os.environ["TEST_LOADER_KEY"])

    def test_skips_comment_lines(self) -> None:
        self._snapshot("COMMENT_KEY")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# COMMENT_KEY=hidden\n", encoding="utf-8")
            _load_dotenv(path)
        self.assertIsNone(os.environ.get("COMMENT_KEY"))

    def test_skips_empty_lines(self) -> None:
        self._snapshot("EMPTY_KEY")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("\n\n", encoding="utf-8")
            _load_dotenv(path)
        self.assertIsNone(os.environ.get("EMPTY_KEY"))

    def test_skips_missing_file(self) -> None:
        path = Path("/nonexistent/.env")
        _load_dotenv(path)

    def test_handles_export_prefix(self) -> None:
        self._snapshot("EXPORTED_KEY")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("export EXPORTED_KEY=value\n", encoding="utf-8")
            _load_dotenv(path)
        self.assertEqual("value", os.environ["EXPORTED_KEY"])

    def test_strips_quotes_from_value(self) -> None:
        self._snapshot("QUOTED_KEY")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('QUOTED_KEY="quoted value"\n', encoding="utf-8")
            _load_dotenv(path)
        self.assertEqual("quoted value", os.environ["QUOTED_KEY"])

    def test_setdefault_does_not_override_existing(self) -> None:
        old = os.environ.get("EXISTING_DOTENV_KEY")
        try:
            os.environ["EXISTING_DOTENV_KEY"] = "from-env"
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ".env"
                path.write_text("EXISTING_DOTENV_KEY=from-file\n", encoding="utf-8")
                _load_dotenv(path)
            self.assertEqual("from-env", os.environ["EXISTING_DOTENV_KEY"])
        finally:
            if old is None:
                os.environ.pop("EXISTING_DOTENV_KEY", None)
            else:
                os.environ["EXISTING_DOTENV_KEY"] = old

    def test_skips_lines_without_equals(self) -> None:
        self._snapshot("NOEQUALS")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("NOEQUALS\n", encoding="utf-8")
            _load_dotenv(path)
        self.assertIsNone(os.environ.get("NOEQUALS"))


if __name__ == "__main__":
    unittest.main()
