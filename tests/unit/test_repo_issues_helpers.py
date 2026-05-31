from __future__ import annotations

import unittest

from contribarena.tools.repo_issues import (
    _apply_local_filters,
    _from_gh,
    _from_rest,
    _has_assignee,
    _labels,
    _optional_str,
)


class LabelsHelperTest(unittest.TestCase):
    """_labels extracts label names from heterogeneous GitHub payloads."""

    def test_labels_from_list_of_dicts(self) -> None:
        raw = [{"name": "bug"}, {"name": "enhancement"}]
        self.assertEqual(["bug", "enhancement"], _labels(raw))

    def test_labels_from_list_of_strings(self) -> None:
        raw = ["bug", "enhancement"]
        self.assertEqual(["bug", "enhancement"], _labels(raw))

    def test_labels_skips_empty_names(self) -> None:
        raw = [{"name": "bug"}, {"name": ""}, "good first issue"]
        self.assertEqual(["bug", "good first issue"], _labels(raw))

    def test_labels_returns_empty_when_not_list(self) -> None:
        self.assertEqual([], _labels(None))
        self.assertEqual([], _labels("bug"))
        self.assertEqual([], _labels(42))

    def test_labels_handles_mixed_types(self) -> None:
        raw = [{"name": "bug"}, "enhancement", 123, None]
        result = _labels(raw)
        self.assertIn("bug", result)
        self.assertIn("enhancement", result)
        self.assertIn("123", result)


class HasAssigneeHelperTest(unittest.TestCase):
    """_has_assignee detects assignee presence for a specific issue number."""

    def test_has_assignee_true_when_assignee_present(self) -> None:
        items = [{"number": 5, "assignee": {"login": "alice"}}]
        self.assertTrue(_has_assignee(items, 5))

    def test_has_assignee_true_when_assignees_list(self) -> None:
        items = [{"number": 7, "assignees": [{"login": "bob"}]}]
        self.assertTrue(_has_assignee(items, 7))

    def test_has_assignee_false_when_no_matching_number(self) -> None:
        items = [{"number": 3, "assignee": {"login": "alice"}}]
        self.assertFalse(_has_assignee(items, 5))

    def test_has_assignee_false_when_no_assignee(self) -> None:
        items = [{"number": 5, "title": "no assignee"}]
        self.assertFalse(_has_assignee(items, 5))

    def test_has_assignee_false_when_not_list(self) -> None:
        self.assertFalse(_has_assignee(None, 5))
        self.assertFalse(_has_assignee("not a list", 5))
        self.assertFalse(_has_assignee({}, 5))


class OptionalStrHelperTest(unittest.TestCase):
    """_optional_str converts truthy values or returns None."""

    def test_returns_string_for_truthy(self) -> None:
        self.assertEqual("2026-01-01", _optional_str("2026-01-01"))
        self.assertEqual("123", _optional_str(123))

    def test_returns_none_for_falsy(self) -> None:
        self.assertIsNone(_optional_str(None))
        self.assertIsNone(_optional_str(""))
        self.assertIsNone(_optional_str(0))
        self.assertIsNone(_optional_str([]))


class FromGhHelperTest(unittest.TestCase):
    """_from_gh normalizes GitHub GraphQL issue payloads."""

    def test_minimal_payload(self) -> None:
        item = {"number": 42, "title": "Fix bug", "url": "https://example.com/42"}
        issue = _from_gh(item)
        self.assertEqual(42, issue.number)
        self.assertEqual("Fix bug", issue.title)
        self.assertEqual("https://example.com/42", issue.url)

    def test_payload_with_labels(self) -> None:
        item = {
            "number": 10,
            "title": "Add feature",
            "url": "https://example.com/10",
            "labels": [{"name": "enhancement"}],
            "body": "Description here",
            "createdAt": "2026-01-15T10:00:00Z",
        }
        issue = _from_gh(item)
        self.assertEqual(["enhancement"], issue.labels)
        self.assertEqual("Description here", issue.body)
        self.assertEqual("2026-01-15T10:00:00Z", issue.created_at)

    def test_missing_fields_default_to_empty(self) -> None:
        item = {"number": 1, "title": "Minimal"}
        issue = _from_gh(item)
        self.assertEqual("", issue.url)
        self.assertEqual("", issue.body)
        self.assertEqual([], issue.labels)
        self.assertIsNone(issue.created_at)


class FromRestHelperTest(unittest.TestCase):
    """_from_rest normalizes GitHub REST API issue payloads."""

    def test_minimal_payload(self) -> None:
        item = {"number": 99, "title": "Bug report", "html_url": "https://example.com/99"}
        issue = _from_rest(item)
        self.assertEqual(99, issue.number)
        self.assertEqual("Bug report", issue.title)
        self.assertEqual("https://example.com/99", issue.url)

    def test_payload_with_labels_and_dates(self) -> None:
        item = {
            "number": 50,
            "title": "Feature request",
            "html_url": "https://example.com/50",
            "labels": [{"name": "feature"}],
            "body": "Please add this",
            "created_at": "2026-02-01T12:00:00Z",
            "updated_at": "2026-02-02T14:00:00Z",
        }
        issue = _from_rest(item)
        self.assertEqual(["feature"], issue.labels)
        self.assertEqual("Please add this", issue.body)
        self.assertEqual("2026-02-01T12:00:00Z", issue.created_at)
        self.assertEqual("2026-02-02T14:00:00Z", issue.updated_at)

    def test_missing_fields_default_to_empty(self) -> None:
        item = {"number": 1, "title": "Minimal"}
        issue = _from_rest(item)
        self.assertEqual("", issue.url)
        self.assertEqual("", issue.body)
        self.assertEqual([], issue.labels)
        self.assertIsNone(issue.created_at)
        self.assertIsNone(issue.updated_at)


class ApplyLocalFiltersHelperTest(unittest.TestCase):
    """_apply_local_filters enforces created_after and no_assignee constraints."""

    def test_created_after_filters_old_issues(self) -> None:
        issues = [
            _from_gh({"number": 1, "title": "Old", "createdAt": "2026-01-01T00:00:00Z"}),
            _from_gh({"number": 2, "title": "New", "createdAt": "2026-03-01T00:00:00Z"}),
        ]
        raw = [
            {"number": 1, "createdAt": "2026-01-01T00:00:00Z"},
            {"number": 2, "createdAt": "2026-03-01T00:00:00Z"},
        ]
        filters = {"created_after": "2026-02-01T00:00:00Z"}
        result = _apply_local_filters(issues, raw, filters)
        self.assertEqual(1, len(result))
        self.assertEqual(2, result[0].number)

    def test_no_assignee_filters_assigned_issues(self) -> None:
        issues = [
            _from_gh({"number": 10, "title": "Assigned"}),
            _from_gh({"number": 20, "title": "Unassigned"}),
        ]
        raw = [
            {"number": 10, "assignee": {"login": "alice"}},
            {"number": 20},
        ]
        filters = {"no_assignee": True}
        result = _apply_local_filters(issues, raw, filters)
        self.assertEqual(1, len(result))
        self.assertEqual(20, result[0].number)

    def test_combined_filters(self) -> None:
        issues = [
            _from_gh({"number": 5, "title": "Old assigned", "createdAt": "2026-01-01T00:00:00Z"}),
            _from_gh({"number": 15, "title": "New unassigned", "createdAt": "2026-03-01T00:00:00Z"}),
        ]
        raw = [
            {"number": 5, "assignee": {"login": "bob"}, "createdAt": "2026-01-01T00:00:00Z"},
            {"number": 15, "createdAt": "2026-03-01T00:00:00Z"},
        ]
        filters = {"created_after": "2026-02-01T00:00:00Z", "no_assignee": True}
        result = _apply_local_filters(issues, raw, filters)
        self.assertEqual(1, len(result))
        self.assertEqual(15, result[0].number)

    def test_empty_filters_returns_all(self) -> None:
        issues = [
            _from_gh({"number": 1, "title": "A"}),
            _from_gh({"number": 2, "title": "B"}),
        ]
        raw = [{"number": 1}, {"number": 2}]
        result = _apply_local_filters(issues, raw, {})
        self.assertEqual(2, len(result))


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
