from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from contextlib import contextmanager
from pathlib import Path

from contribarena.config.loader import _clean_env_value, _load_dotenv, _load_yaml_like


@contextmanager
def _temp_env_file(text: str):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / ".env"
        p.write_text(text, encoding="utf-8")
        yield p


@contextmanager
def _temp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class LoadYamlLikeTest(unittest.TestCase):
    def test_loads_json_object(self) -> None:
        text = '{"run": {"mode": "shadow"}, "count": 2}'
        result = _load_yaml_like(text)
        self.assertEqual(result, {"run": {"mode": "shadow"}, "count": 2})

    def test_loads_yaml_mapping(self) -> None:
        text = textwrap.dedent(
            """\
            run:
              mode: shadow
              model: local-stub
            count: 2
            """
        )
        result = _load_yaml_like(text)
        self.assertEqual(result, {"run": {"mode": "shadow", "model": "local-stub"}, "count": 2})

    def test_json_takes_precedence_over_yaml_when_leading_brace(self) -> None:
        # Text with leading '{' is routed to json.loads, not yaml.safe_load.
        text = '{"key": "value"}'
        result = _load_yaml_like(text)
        self.assertEqual(result, {"key": "value"})

    def test_leading_whitespace_is_stripped_for_json_detection(self) -> None:
        text = '   \n\t{"key": "value"}'
        result = _load_yaml_like(text)
        self.assertEqual(result, {"key": "value"})

    def test_rejects_json_non_object_root(self) -> None:
        # JSON list root should raise because configs must be mappings.
        with self.assertRaises(ValueError) as ctx:
            _load_yaml_like("[1, 2, 3]")
        self.assertIn("mapping", str(ctx.exception))

    def test_rejects_yaml_non_mapping_root(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _load_yaml_like("- one\n- two\n")
        self.assertIn("mapping", str(ctx.exception))

    def test_rejects_yaml_scalar_root(self) -> None:
        with self.assertRaises(ValueError):
            _load_yaml_like("just a string")

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(Exception):
            _load_yaml_like("{not valid json")

    def test_invalid_yaml_raises(self) -> None:
        # Unclosed block scalar / ambiguous structure can produce a parser error.
        with self.assertRaises(Exception):
            _load_yaml_like("key: [")

    def test_yaml_preserves_unicode(self) -> None:
        text = "name: café\nemoji: 🏆\n"
        result = _load_yaml_like(text)
        self.assertEqual(result["name"], "café")
        self.assertEqual(result["emoji"], "🏆")

    def test_json_preserves_nested_types(self) -> None:
        text = '{"items": [1, 2.5, null, true], "nested": {"x": "y"}}'
        result = _load_yaml_like(text)
        self.assertEqual(result["items"], [1, 2.5, None, True])
        self.assertEqual(result["nested"], {"x": "y"})


class CleanEnvValueTest(unittest.TestCase):
    def test_strips_double_quotes(self) -> None:
        self.assertEqual(_clean_env_value('"hello world"'), "hello world")

    def test_strips_single_quotes(self) -> None:
        self.assertEqual(_clean_env_value("'hello world'"), "hello world")

    def test_preserves_unquoted_value(self) -> None:
        self.assertEqual(_clean_env_value("plain_value"), "plain_value")

    def test_preserves_mismatched_quotes(self) -> None:
        # Mismatched quotes should not strip.
        self.assertEqual(_clean_env_value("\"hello'"), "\"hello'")
        self.assertEqual(_clean_env_value("'hello\""), "'hello\"")

    def test_preserves_single_char_quotes_around_empty(self) -> None:
        # Length < 2 is not stripped.
        self.assertEqual(_clean_env_value('"'), '"')
        self.assertEqual(_clean_env_value("'"), "'")

    def test_empty_string_preserved(self) -> None:
        self.assertEqual(_clean_env_value(""), "")

    def test_two_matching_quote_chars_collapses_to_empty(self) -> None:
        # '""' -> '' (quoted empty string)
        self.assertEqual(_clean_env_value('""'), "")
        self.assertEqual(_clean_env_value("''"), "")

    def test_does_not_strip_inner_quotes(self) -> None:
        # Only matching outer quotes are removed; inner quotes remain.
        self.assertEqual(_clean_env_value("'he said \"hi\"'", ), 'he said "hi"')

    def test_value_containing_equals_is_preserved(self) -> None:
        self.assertEqual(_clean_env_value("'key=value'"), "key=value")


class LoadDotenvTest(unittest.TestCase):
    def setUp(self) -> None:
        # Track keys we touch so we can clean them up regardless of prior env.
        self._touched_keys: list[str] = []
        self._original_values: dict[str, str | None] = {}

    def tearDown(self) -> None:
        for key in self._touched_keys:
            if self._original_values.get(key) is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = self._original_values[key]  # type: ignore[index]

    def _use_env(self, key: str) -> str:
        self._touched_keys.append(key)
        self._original_values[key] = os.environ.get(key)
        # Ensure the key is absent so setdefault actually sets it.
        os.environ.pop(key, None)
        return key

    def test_nonexistent_path_is_noop(self) -> None:
        path = Path("/definitely/does/not/exist/.env")
        # Should not raise.
        _load_dotenv(path)

    def test_skips_comments_and_blank_lines(self) -> None:
        key = self._use_env("CONTRIBARENA_TEST_SKIP")
        with _temp_env_file(
            textwrap.dedent(
                """\
                # this is a comment

                # another comment
                CONTRIBARENA_TEST_SKIP=loaded
                """
            )
        ) as path:
            _load_dotenv(path)
        self.assertEqual(os.environ.get(key), "loaded")

    def test_skips_lines_without_equals(self) -> None:
        key = self._use_env("CONTRIBARENA_TEST_NOEQ")
        with _temp_env_file("NO_EQUALS_HERE\nCONTRIBARENA_TEST_NOEQ=yes\n") as path:
            _load_dotenv(path)
        self.assertEqual(os.environ.get(key), "yes")

    def test_splits_on_first_equals_only(self) -> None:
        key = self._use_env("CONTRIBARENA_TEST_MULTI")
        with _temp_env_file("CONTRIBARENA_TEST_MULTI=a=b=c\n") as path:
            _load_dotenv(path)
        self.assertEqual(os.environ.get(key), "a=b=c")

    def test_strips_whitespace_and_quotes(self) -> None:
        key = self._use_env("CONTRIBARENA_TEST_QUOTED")
        with _temp_env_file('  CONTRIBARENA_TEST_QUOTED  =  "spaced value"  \n') as path:
            _load_dotenv(path)
        self.assertEqual(os.environ.get(key), "spaced value")

    def test_does_not_overwrite_existing_env(self) -> None:
        key = self._use_env("CONTRIBARENA_TEST_EXISTING")
        os.environ[key] = "preserved"
        with _temp_env_file("CONTRIBARENA_TEST_EXISTING=overwritten\n") as path:
            _load_dotenv(path)
        self.assertEqual(os.environ.get(key), "preserved")

    def test_handles_export_prefix(self) -> None:
        key = self._use_env("CONTRIBARENA_TEST_EXPORT")
        with _temp_env_file("export CONTRIBARENA_TEST_EXPORT=exported_value\n") as path:
            _load_dotenv(path)
        self.assertEqual(os.environ.get(key), "exported_value")

    def test_skips_empty_key_after_split(self) -> None:
        # '=value' has empty key and must be ignored.
        key = self._use_env("CONTRIBARENA_TEST_AFTER")
        with _temp_env_file("=ignored\nCONTRIBARENA_TEST_AFTER=present\n") as path:
            _load_dotenv(path)
        self.assertEqual(os.environ.get(key), "present")

    def test_directory_path_is_noop(self) -> None:
        # A directory is not a file; _load_dotenv should silently return.
        with _temp_dir() as tmp:
            _load_dotenv(tmp)  # must not raise

    def test_loads_multiple_entries(self) -> None:
        k1 = self._use_env("CONTRIBARENA_TEST_A")
        k2 = self._use_env("CONTRIBARENA_TEST_B")
        with _temp_env_file(
            textwrap.dedent(
                """\
                CONTRIBARENA_TEST_A=alpha
                CONTRIBARENA_TEST_B='beta'
                """
            )
        ) as path:
            _load_dotenv(path)
        self.assertEqual(os.environ.get(k1), "alpha")
        self.assertEqual(os.environ.get(k2), "beta")


if __name__ == "__main__":
    unittest.main()
