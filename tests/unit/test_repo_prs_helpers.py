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

    def test_none_input(self) -> None:
        self.assertEqual([], _labels(None))

    def test_non_list_input(self) -> None:
        self.assertEqual([], _labels("not a list"))
        self.assertEqual([], _labels({}))
        self.assertEqual([], _labels(123))

    def test_dict_items_with_name(self) -> None:
        raw = [{"name": "bug"}, {"name": "enhancement"}]
        self.assertEqual(["bug", "enhancement"], _labels(raw))

    def test_string_items(self) -> None:
        raw = ["bug", "enhancement"]
        self.assertEqual(["bug", "enhancement"], _labels(raw))

    def test_mixed_dict_and_string(self) -> None:
        raw = [{"name": "bug"}, "feature", {"name": "docs"}]
        self.assertEqual(["bug", "feature", "docs"], _labels(raw))

    def test_empty_name_filtered(self) -> None:
        raw = [{"name": ""}, {"name": "bug"}]
        self.assertEqual(["bug"], _labels(raw))

    def test_none_name_filtered(self) -> None:
        raw = [{"name": None}, {"name": "bug"}]
        self.assertEqual(["bug"], _labels(raw))

    def test_empty_string_filtered(self) -> None:
        raw = ["", "bug"]
        self.assertEqual(["bug"], _labels(raw))

    def test_dict_without_name_key(self) -> None:
        raw = [{"label": "bug"}, {"name": "feature"}]
        self.assertEqual(["feature"], _labels(raw))


class LinkedIssuesTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertEqual([], _linked_issues([]))

    def test_none_input(self) -> None:
        self.assertEqual([], _linked_issues(None))

    def test_non_list_input(self) -> None:
        self.assertEqual([], _linked_issues("not a list"))
        self.assertEqual([], _linked_issues({}))

    def test_dict_items_with_number(self) -> None:
        raw = [{"number": 42}, {"number": 123}]
        self.assertEqual([42, 123], _linked_issues(raw))

    def test_string_number_converted(self) -> None:
        raw = [{"number": "42"}, {"number": "123"}]
        self.assertEqual([42, 123], _linked_issues(raw))

    def test_missing_number_filtered(self) -> None:
        raw = [{}, {"number": 42}]
        self.assertEqual([42], _linked_issues(raw))

    def test_zero_number_filtered(self) -> None:
        raw = [{"number": 0}, {"number": 42}]
        self.assertEqual([42], _linked_issues(raw))

    def test_none_number_filtered(self) -> None:
        raw = [{"number": None}, {"number": 42}]
        self.assertEqual([42], _linked_issues(raw))

    def test_non_dict_item_skipped(self) -> None:
        raw = ["not a dict", {"number": 42}]
        self.assertEqual([42], _linked_issues(raw))


class AssigneesTest(unittest.TestCase):
    def test_empty_assignees(self) -> None:
        self.assertEqual([], _assignees({}))

    def test_no_assignees_key(self) -> None:
        self.assertEqual([], _assignees({"title": "issue"}))

    def test_assignees_not_list(self) -> None:
        self.assertEqual([], _assignees({"assignees": "not a list"}))
        self.assertEqual([], _assignees({"assignees": None}))

    def test_single_assignee(self) -> None:
        item = {"assignees": [{"login": "alice"}]}
        self.assertEqual(["alice"], _assignees(item))

    def test_multiple_assignees(self) -> None:
        item = {"assignees": [{"login": "alice"}, {"login": "bob"}]}
        self.assertEqual(["alice", "bob"], _assignees(item))

    def test_empty_login_returns_empty_string(self) -> None:
        item = {"assignees": [{"login": ""}, {"login": "alice"}]}
        self.assertEqual(["", "alice"], _assignees(item))

    def test_none_login_returns_empty_string(self) -> None:
        item = {"assignees": [{"login": None}, {"login": "alice"}]}
        self.assertEqual(["", "alice"], _assignees(item))

    def test_non_dict_item_skipped(self) -> None:
        item = {"assignees": ["not a dict", {"login": "alice"}]}
        self.assertEqual(["alice"], _assignees(item))


class OptionalStrTest(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        self.assertIsNone(_optional_str(None))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_optional_str(""))

    def test_string_value(self) -> None:
        self.assertEqual("2026-05-31T00:00:00Z", _optional_str("2026-05-31T00:00:00Z"))

    def test_non_string_converted(self) -> None:
        self.assertEqual("42", _optional_str(42))
        self.assertEqual("True", _optional_str(True))

    def test_zero_returns_none(self) -> None:
        self.assertIsNone(_optional_str(0))

    def test_false_returns_none(self) -> None:
        self.assertIsNone(_optional_str(False))


class FromGhTest(unittest.TestCase):
    def test_minimal_item(self) -> None:
        item = {"number": 1}
        pr = _from_gh(item)
        self.assertEqual(1, pr.number)
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

    def test_full_item(self) -> None:
        item = {
            "number": 42,
            "title": "Add feature",
            "url": "https://github.com/owner/repo/pull/42",
            "state": "OPEN",
            "author": {"login": "alice"},
            "body": "Description",
            "labels": [{"name": "enhancement"}],
            "createdAt": "2026-05-31T00:00:00Z",
            "updatedAt": "2026-05-31T01:00:00Z",
            "mergedAt": "2026-05-31T02:00:00Z",
            "isDraft": False,
            "closingIssuesReferences": [{"number": 123}],
        }
        pr = _from_gh(item)
        self.assertEqual(42, pr.number)
        self.assertEqual("Add feature", pr.title)
        self.assertEqual("https://github.com/owner/repo/pull/42", pr.url)
        self.assertEqual("OPEN", pr.state)
        self.assertEqual("alice", pr.author)
        self.assertEqual("Description", pr.body)
        self.assertEqual(["enhancement"], pr.labels)
        self.assertEqual("2026-05-31T00:00:00Z", pr.created_at)
        self.assertEqual("2026-05-31T01:00:00Z", pr.updated_at)
        self.assertEqual("2026-05-31T02:00:00Z", pr.merged_at)
        self.assertFalse(pr.draft)
        self.assertEqual([123], pr.linked_issues)

    def test_author_not_dict(self) -> None:
        item = {"number": 1, "author": "alice"}
        pr = _from_gh(item)
        self.assertEqual("alice", pr.author)

    def test_author_none(self) -> None:
        item = {"number": 1, "author": None}
        pr = _from_gh(item)
        self.assertEqual("", pr.author)

    def test_draft_true(self) -> None:
        item = {"number": 1, "isDraft": True}
        pr = _from_gh(item)
        self.assertTrue(pr.draft)

    def test_number_as_string(self) -> None:
        item = {"number": "42"}
        pr = _from_gh(item)
        self.assertEqual(42, pr.number)


class FromRestTest(unittest.TestCase):
    def test_minimal_item(self) -> None:
        item = {"number": 1}
        pr = _from_rest(item)
        self.assertEqual(1, pr.number)
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

    def test_full_item(self) -> None:
        item = {
            "number": 42,
            "title": "Add feature",
            "html_url": "https://github.com/owner/repo/pull/42",
            "state": "open",
            "user": {"login": "alice"},
            "body": "Description",
            "created_at": "2026-05-31T00:00:00Z",
            "updated_at": "2026-05-31T01:00:00Z",
            "merged_at": "2026-05-31T02:00:00Z",
            "draft": False,
        }
        pr = _from_rest(item)
        self.assertEqual(42, pr.number)
        self.assertEqual("Add feature", pr.title)
        self.assertEqual("https://github.com/owner/repo/pull/42", pr.url)
        self.assertEqual("open", pr.state)
        self.assertEqual("alice", pr.author)
        self.assertEqual("Description", pr.body)
        self.assertEqual([], pr.labels)  # REST does not include labels
        self.assertEqual("2026-05-31T00:00:00Z", pr.created_at)
        self.assertEqual("2026-05-31T01:00:00Z", pr.updated_at)
        self.assertEqual("2026-05-31T02:00:00Z", pr.merged_at)
        self.assertFalse(pr.draft)

    def test_user_not_dict(self) -> None:
        item = {"number": 1, "user": "alice"}
        pr = _from_rest(item)
        self.assertEqual("", pr.author)

    def test_user_none(self) -> None:
        item = {"number": 1, "user": None}
        pr = _from_rest(item)
        self.assertEqual("", pr.author)

    def test_draft_true(self) -> None:
        item = {"number": 1, "draft": True}
        pr = _from_rest(item)
        self.assertTrue(pr.draft)

    def test_labels_always_empty(self) -> None:
        item = {"number": 1, "labels": [{"name": "bug"}]}
        pr = _from_rest(item)
        self.assertEqual([], pr.labels)


if __name__ == "__main__":
    unittest.main()
