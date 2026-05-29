from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from contribarena.config.loader import _clean_env_value, _load_dotenv, _load_yaml_like


class LoadYamlLikeTest(unittest.TestCase):
    # -- YAML parsing --

    def test_parses_simple_yaml(self) -> None:
        result = _load_yaml_like("name: test\nvalue: 42\n")
        self.assertEqual({"name": "test", "value": 42}, result)

    def test_parses_nested_yaml(self) -> None:
        text = "parent:\n  child: hello\n  count: 3\n"
        result = _load_yaml_like(text)
        self.assertEqual({"parent": {"child": "hello", "count": 3}}, result)

    def test_parses_yaml_with_leading_whitespace(self) -> None:
        result = _load_yaml_like("  \nname: test\n")
        self.assertEqual({"name": "test"}, result)

    # -- JSON parsing --

    def test_parses_json_starting_with_brace(self) -> None:
        result = _load_yaml_like('{"name": "test", "value": 42}')
        self.assertEqual({"name": "test", "value": 42}, result)

    def test_parses_json_with_leading_whitespace(self) -> None:
        result = _load_yaml_like('   {"key": "val"}')
        self.assertEqual({"key": "val"}, result)

    # -- Error cases --

    def test_raises_on_yaml_list_root(self) -> None:
        with self.assertRaises(ValueError):
            _load_yaml_like("- item1\n- item2\n")

    def test_raises_on_yaml_scalar_root(self) -> None:
        with self.assertRaises(ValueError):
            _load_yaml_like("just a string")

    def test_raises_on_json_list_root(self) -> None:
        with self.assertRaises(ValueError):
            _load_yaml_like('["a", "b"]')

    def test_empty_yaml_mapping_is_valid(self) -> None:
        result = _load_yaml_like("{}")
        self.assertEqual({}, result)


class CleanEnvValueTest(unittest.TestCase):
    def test_strips_double_quotes(self) -> None:
        self.assertEqual("hello", _clean_env_value('"hello"'))

    def test_strips_single_quotes(self) -> None:
        self.assertEqual("hello", _clean_env_value("'hello'"))

    def test_no_strip_mismatched_quotes(self) -> None:
        self.assertEqual("\"hello'", _clean_env_value("\"hello'"))

    def test_no_strip_unquoted(self) -> None:
        self.assertEqual("hello", _clean_env_value("hello"))

    def test_no_strip_single_char(self) -> None:
        self.assertEqual("'", _clean_env_value("'"))

    def test_strips_empty_quoted_string(self) -> None:
        self.assertEqual("", _clean_env_value('""'))

    def test_no_strip_two_different_quote_chars(self) -> None:
        self.assertEqual("\"'", _clean_env_value("\"'"))

    def test_preserves_inner_quotes(self) -> None:
        self.assertEqual("it's", _clean_env_value('"it\'s"'))


class LoadDotenvTest(unittest.TestCase):
    def test_sets_env_variable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("TEST_KEY_12345=myvalue\n", encoding="utf-8")
            os.environ.pop("TEST_KEY_12345", None)

            _load_dotenv(env_path)

            self.assertEqual("myvalue", os.environ.get("TEST_KEY_12345"))
            os.environ.pop("TEST_KEY_12345", None)

    def test_does_not_overwrite_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("TEST_KEY_67890=newvalue\n", encoding="utf-8")
            os.environ["TEST_KEY_67890"] = "existingvalue"

            _load_dotenv(env_path)

            self.assertEqual("existingvalue", os.environ.get("TEST_KEY_67890"))
            os.environ.pop("TEST_KEY_67890", None)

    def test_skips_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("# comment\nTEST_COMMENT_KEY=val\n", encoding="utf-8")
            os.environ.pop("TEST_COMMENT_KEY", None)

            _load_dotenv(env_path)

            self.assertEqual("val", os.environ.get("TEST_COMMENT_KEY"))
            os.environ.pop("TEST_COMMENT_KEY", None)

    def test_skips_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("\n\nTEST_BLANK_KEY=val\n\n", encoding="utf-8")
            os.environ.pop("TEST_BLANK_KEY", None)

            _load_dotenv(env_path)

            self.assertEqual("val", os.environ.get("TEST_BLANK_KEY"))
            os.environ.pop("TEST_BLANK_KEY", None)

    def test_strips_quotes_from_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('TEST_QUOTED_KEY="quoted_val"\n', encoding="utf-8")
            os.environ.pop("TEST_QUOTED_KEY", None)

            _load_dotenv(env_path)

            self.assertEqual("quoted_val", os.environ.get("TEST_QUOTED_KEY"))
            os.environ.pop("TEST_QUOTED_KEY", None)

    def test_handles_export_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("export TEST_EXPORT_KEY=exported\n", encoding="utf-8")
            os.environ.pop("TEST_EXPORT_KEY", None)

            _load_dotenv(env_path)

            self.assertEqual("exported", os.environ.get("TEST_EXPORT_KEY"))
            os.environ.pop("TEST_EXPORT_KEY", None)

    def test_noop_when_file_missing(self) -> None:
        missing = Path("/tmp/nonexistent_contribarena_env_file_12345")
        # Should not raise
        _load_dotenv(missing)

    def test_skips_lines_without_equals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("NOEQUALSSIGN\nTEST_NOEQ_KEY=val\n", encoding="utf-8")
            os.environ.pop("TEST_NOEQ_KEY", None)

            _load_dotenv(env_path)

            self.assertEqual("val", os.environ.get("TEST_NOEQ_KEY"))
            os.environ.pop("TEST_NOEQ_KEY", None)

    def test_value_with_equals_in_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("TEST_EQ_IN_VAL_KEY=a=b=c\n", encoding="utf-8")
            os.environ.pop("TEST_EQ_IN_VAL_KEY", None)

            _load_dotenv(env_path)

            self.assertEqual("a=b=c", os.environ.get("TEST_EQ_IN_VAL_KEY"))
            os.environ.pop("TEST_EQ_IN_VAL_KEY", None)
