from __future__ import annotations

import unittest

from contribarena.tools.repo_prs import (
    _assignees,
    _from_gh,
    _from_rest,
    _labels,
    _linked_issues,
    _optional_str,
)


class LabelsTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertEqual([], _labels([]))

    def test_non_list_input(self) -> None:
        self.assertEqual([], _labels("not a list"))
        self.assertEqual([], _labels(None))
        self.assertEqual([], _labels(42))

    def test_standard_dict_labels(self) -> None:
        raw = [{"name": "bug"}, {"name": "enhancement"}]
        self.assertEqual(["bug", "enhancement"], _labels(raw))

    def test_empty_name_filtered(self) -> None:
        raw = [{"name": "bug"}, {"name": ""}, {"name": "feature"}]
        self.assertEqual(["bug", "feature"], _labels(raw))

    def test_string_items_in_list(self) -> None:
        raw = ["bug", "enhancement"]
        self.assertEqual(["bug", "enhancement"], _labels(raw))

    def test_empty_string_items_filtered(self) -> None:
        raw = ["bug", "", None, 0]
        self.assertEqual(["bug"], _labels(raw))

    def test_mixed_dict_and_string(self) -> None:
        raw = [{"name": "bug"}, "feature", {"name": ""}, "urgent"]
        self.assertEqual(["bug", "feature", "urgent"], _labels(raw))


class LinkedIssuesTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertEqual([], _linked_issues([]))

    def test_non_list_input(self) -> None:
        self.assertEqual([], _linked_issues(None))
        self.assertEqual([], _linked_issues({"number": 1}))

    def test_valid_linked_issues(self) -> None:
        raw = [{"number": 1}, {"number": 42}, {"number": 7}]
        self.assertEqual([1, 42, 7], _linked_issues(raw))

    def test_filters_non_dict_items(self) -> None:
        raw = [{"number": 1}, "not a dict", 42, {"number": 7}]
        self.assertEqual([1, 7], _linked_issues(raw))

    def test_filters_missing_or_zero_number(self) -> None:
        raw = [{"number": 0}, {"title": "no number"}, {"number": 5}]
        self.assertEqual([5], _linked_issues(raw))


class AssigneesTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertEqual([], _assignees({"assignees": []}))

    def test_no_assignees_key(self) -> None:
        self.assertEqual([], _assignees({}))

    def test_non_list_assignees(self) -> None:
        self.assertEqual([], _assignees({"assignees": "not a list"}))
        self.assertEqual([], _assignees({"assignees": None}))

    def test_valid_assignees(self) -> None:
        item = {"assignees": [{"login": "alice"}, {"login": "bob"}]}
        self.assertEqual(["alice", "bob"], _assignees(item))

    def test_filters_non_dict_users(self) -> None:
        item = {"assignees": [{"login": "alice"}, "not a dict", 42]}
        self.assertEqual(["alice"], _assignees(item))

    def test_empty_login(self) -> None:
        item = {"assignees": [{"login": ""}, {"login": "bob"}]}
        self.assertEqual(["", "bob"], _assignees(item))


class OptionalStrTest(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        self.assertIsNone(_optional_str(None))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_optional_str(""))

    def test_truthy_string(self) -> None:
        self.assertEqual("hello", _optional_str("hello"))

    def test_integer(self) -> None:
        self.assertEqual("42", _optional_str(42))

    def test_zero_returns_none(self) -> None:
        self.assertIsNone(_optional_str(0))

    def test_false_returns_none(self) -> None:
        self.assertIsNone(_optional_str(False))


class FromGhTest(unittest.TestCase):
    def _make_item(self, **overrides: object) -> dict:
        item = {
            "number": 1,
            "title": "Fix bug",
            "url": "https://github.com/owner/repo/pull/1",
            "state": "open",
            "author": {"login": "alice"},
            "body": "Some body text",
            "labels": [{"name": "bug"}],
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-02T00:00:00Z",
            "mergedAt": None,
            "isDraft": False,
            "closingIssuesReferences": [],
        }
        item.update(overrides)
        return item

    def test_basic_from_gh(self) -> None:
        pr = _from_gh(self._make_item())
        self.assertEqual(1, pr.number)
        self.assertEqual("Fix bug", pr.title)
        self.assertEqual("https://github.com/owner/repo/pull/1", pr.url)
        self.assertEqual("open", pr.state)
        self.assertEqual("alice", pr.author)
        self.assertEqual("Some body text", pr.body)
        self.assertEqual(["bug"], pr.labels)
        self.assertEqual("2026-01-01T00:00:00Z", pr.created_at)
        self.assertEqual("2026-01-02T00:00:00Z", pr.updated_at)
        self.assertIsNone(pr.merged_at)
        self.assertFalse(pr.draft)
        self.assertEqual([], pr.linked_issues)

    def test_merged_pr(self) -> None:
        pr = _from_gh(self._make_item(mergedAt="2026-01-03T00:00:00Z"))
        self.assertEqual("2026-01-03T00:00:00Z", pr.merged_at)

    def test_draft_pr(self) -> None:
        pr = _from_gh(self._make_item(isDraft=True))
        self.assertTrue(pr.draft)

    def test_author_as_string(self) -> None:
        pr = _from_gh(self._make_item(author="ghost"))
        self.assertEqual("ghost", pr.author)

    def test_linked_issues(self) -> None:
        pr = _from_gh(
            self._make_item(closingIssuesReferences=[{"number": 5}, {"number": 10}])
        )
        self.assertEqual([5, 10], pr.linked_issues)

    def test_missing_fields_defaults(self) -> None:
        pr = _from_gh({})
        self.assertEqual(0, pr.number)
        self.assertEqual("", pr.title)
        self.assertEqual("", pr.url)
        self.assertEqual("", pr.state)
        self.assertEqual("", pr.author)
        self.assertEqual("", pr.body)
        self.assertEqual([], pr.labels)
        self.assertIsNone(pr.created_at)
        self.assertIsNone(pr.updated_at)
        self.assertIsNone(pr.merged_at)
        self.assertFalse(pr.draft)
        self.assertEqual([], pr.linked_issues)


class FromRestTest(unittest.TestCase):
    def _make_item(self, **overrides: object) -> dict:
        item = {
            "number": 1,
            "title": "Fix bug",
            "html_url": "https://github.com/owner/repo/pull/1",
            "state": "open",
            "user": {"login": "alice"},
            "body": "Some body text",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "merged_at": None,
            "draft": False,
        }
        item.update(overrides)
        return item

    def test_basic_from_rest(self) -> None:
        pr = _from_rest(self._make_item())
        self.assertEqual(1, pr.number)
        self.assertEqual("Fix bug", pr.title)
        self.assertEqual("https://github.com/owner/repo/pull/1", pr.url)
        self.assertEqual("open", pr.state)
        self.assertEqual("alice", pr.author)
        self.assertEqual("Some body text", pr.body)
        self.assertEqual([], pr.labels)
        self.assertEqual("2026-01-01T00:00:00Z", pr.created_at)
        self.assertEqual("2026-01-02T00:00:00Z", pr.updated_at)
        self.assertIsNone(pr.merged_at)
        self.assertFalse(pr.draft)

    def test_merged_pr(self) -> None:
        pr = _from_rest(self._make_item(merged_at="2026-01-03T00:00:00Z"))
        self.assertEqual("2026-01-03T00:00:00Z", pr.merged_at)

    def test_draft_pr(self) -> None:
        pr = _from_rest(self._make_item(draft=True))
        self.assertTrue(pr.draft)

    def test_author_as_string(self) -> None:
        pr = _from_rest(self._make_item(user="ghost"))
        self.assertEqual("", pr.author)

    def test_user_missing(self) -> None:
        pr = _from_rest(self._make_item(user=None))
        self.assertEqual("", pr.author)

    def test_missing_fields_defaults(self) -> None:
        pr = _from_rest({})
        self.assertEqual(0, pr.number)
        self.assertEqual("", pr.title)
        self.assertEqual("", pr.url)
        self.assertEqual("", pr.state)
        self.assertEqual("", pr.author)
        self.assertEqual("", pr.body)
        self.assertEqual([], pr.labels)
        self.assertIsNone(pr.created_at)
        self.assertIsNone(pr.updated_at)
        self.assertIsNone(pr.merged_at)
        self.assertFalse(pr.draft)


if __name__ == "__main__":
    unittest.main()
