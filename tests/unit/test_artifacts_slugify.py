from __future__ import annotations

import unittest

from contribarena.engine.artifacts import slugify


class SlugifyTest(unittest.TestCase):
    def test_preserves_alphanumeric_and_special_chars(self) -> None:
        """Alphanumeric plus dots, underscores, hyphens are preserved."""
        self.assertEqual("hello-world_v0.1.0", slugify("hello-world_v0.1.0"))

    def test_replaces_spaces_with_hyphens(self) -> None:
        self.assertEqual("hello-world", slugify("hello world"))

    def test_replaces_special_characters(self) -> None:
        self.assertEqual("owner-repo", slugify("owner/repo"))

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        self.assertEqual("hello", slugify("  hello  "))

    def test_strips_leading_and_trailing_hyphens(self) -> None:
        self.assertEqual("hello", slugify("---hello---"))

    def test_lowercases_result(self) -> None:
        self.assertEqual("hello-world", slugify("HELLO-WORLD"))

    def test_empty_string_returns_value(self) -> None:
        self.assertEqual("value", slugify(""))

    def test_whitespace_only_returns_value(self) -> None:
        self.assertEqual("value", slugify("   "))

    def test_only_special_characters_returns_value(self) -> None:
        self.assertEqual("value", slugify("///"))

    def test_at_sign_replaced(self) -> None:
        self.assertEqual("owner-repo-v1.2.3", slugify("owner/repo@v1.2.3"))

    def test_underscores_preserved(self) -> None:
        self.assertEqual("my_file_name", slugify("my_file_name"))

    def test_dots_preserved(self) -> None:
        self.assertEqual("file.txt", slugify("file.txt"))

    def test_hyphens_preserved_inside_text(self) -> None:
        self.assertEqual("well-known", slugify("well-known"))

    def test_replaces_tabs_newlines_with_hyphens(self) -> None:
        self.assertEqual("a-b-c", slugify("a\tb\nc"))

    def test_leading_trailing_special_chars_removed(self) -> None:
        self.assertEqual("hello", slugify("!!!hello!!!"))

    def test_unicode_latin_characters_replaced(self) -> None:
        self.assertEqual("r-sum", slugify("résumé"))

    def test_unicode_emoji_replaced(self) -> None:
        self.assertEqual("hello-world", slugify("hello 😀 world"))

    def test_idempotent(self) -> None:
        value = "My Project/Version 2.0!"
        first = slugify(value)
        second = slugify(first)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
