from __future__ import annotations

import unittest

from contribarena.models import IssueCandidate, QualityGateCheck
from contribarena.tools.github_live import _add_quality_check
from contribarena.tools.repo_issues import (
    _apply_local_filters,
    _has_assignee,
    _labels,
    _optional_str,
)


class LabelsTest(unittest.TestCase):
    def test_extracts_name_from_dict_items(self) -> None:
        raw = [{"name": "bug"}, {"name": "good first issue"}]
        self.assertEqual(["bug", "good first issue"], _labels(raw))

    def test_empty_name_in_dict_defaults_to_empty_and_is_filtered(self) -> None:
        raw = [{"name": ""}, {"name": "valid"}]
        self.assertEqual(["valid"], _labels(raw))

    def test_non_dict_items_are_stringified(self) -> None:
        raw = ["enhancement", 42]
        self.assertEqual(["enhancement", "42"], _labels(raw))

    def test_non_dict_falsy_items_are_filtered(self) -> None:
        raw = ["visible", 0, "", None]
        self.assertEqual(["visible"], _labels(raw))

    def test_non_list_input_returns_empty(self) -> None:
        self.assertEqual([], _labels(None))
        self.assertEqual([], _labels("not-a-list"))
        self.assertEqual([], _labels({}))

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual([], _labels([]))

    def test_missing_name_key_defaults_to_empty_and_is_filtered(self) -> None:
        raw = [{"color": "red"}, {"name": "docs"}]
        self.assertEqual(["docs"], _labels(raw))


class HasAssigneeTest(unittest.TestCase):
    def test_returns_true_when_assignee_field_is_truthy(self) -> None:
        items = [{"number": 7, "assignee": {"login": "maintainer"}}]
        self.assertTrue(_has_assignee(items, 7))

    def test_returns_true_when_assignees_field_is_truthy(self) -> None:
        items = [{"number": 7, "assignees": [{"login": "helper"}]}]
        self.assertTrue(_has_assignee(items, 7))

    def test_returns_false_when_assignee_and_assignees_are_falsy(self) -> None:
        items = [{"number": 7, "assignee": None, "assignees": []}]
        self.assertFalse(_has_assignee(items, 7))

    def test_returns_false_when_number_not_found(self) -> None:
        items = [{"number": 7, "assignee": {"login": "maintainer"}}]
        self.assertFalse(_has_assignee(items, 99))

    def test_returns_false_when_items_is_not_a_list(self) -> None:
        self.assertFalse(_has_assignee(None, 7))
        self.assertFalse(_has_assignee("not-a-list", 7))

    def test_returns_false_when_item_is_not_a_dict(self) -> None:
        items = ["not-a-dict", {"number": 7, "assignee": {"login": "x"}}]
        self.assertTrue(_has_assignee(items, 7))

    def test_returns_false_when_items_list_is_empty(self) -> None:
        self.assertFalse(_has_assignee([], 7))


class ApplyLocalFiltersTest(unittest.TestCase):
    def _issue(self, number: int, created_at: str | None = None) -> IssueCandidate:
        return IssueCandidate(
            number=number,
            title=f"Issue {number}",
            created_at=created_at,
        )

    def test_no_filters_returns_issues_unchanged(self) -> None:
        issues = [self._issue(1), self._issue(2)]
        result = _apply_local_filters(issues, None, {})
        self.assertEqual([1, 2], [i.number for i in result])

    def test_created_after_filters_older_issues(self) -> None:
        issues = [
            self._issue(1, "2026-05-01T00:00:00Z"),
            self._issue(2, "2026-05-10T00:00:00Z"),
            self._issue(3, None),
        ]
        result = _apply_local_filters(issues, None, {"created_after": "2026-05-05T00:00:00Z"})
        self.assertEqual([2], [i.number for i in result])

    def test_created_after_empty_string_is_noop(self) -> None:
        issues = [self._issue(1)]
        result = _apply_local_filters(issues, None, {"created_after": ""})
        self.assertEqual([1], [i.number for i in result])

    def test_no_assignee_filters_out_assigned_issues(self) -> None:
        issues = [self._issue(7), self._issue(8)]
        raw_items = [
            {"number": 7, "assignee": {"login": "maintainer"}},
            {"number": 8},
        ]
        result = _apply_local_filters(issues, raw_items, {"no_assignee": True})
        self.assertEqual([8], [i.number for i in result])

    def test_no_assignee_falsy_is_noop(self) -> None:
        issues = [self._issue(7)]
        result = _apply_local_filters(issues, None, {"no_assignee": False})
        self.assertEqual([7], [i.number for i in result])

    def test_both_filters_applied_together(self) -> None:
        issues = [
            self._issue(7, "2026-05-10T00:00:00Z"),
            self._issue(8, "2026-05-10T00:00:00Z"),
            self._issue(9, "2026-05-01T00:00:00Z"),
        ]
        raw_items = [
            {"number": 7, "assignee": {"login": "maintainer"}},
            {"number": 8},
        ]
        result = _apply_local_filters(
            issues, raw_items, {"created_after": "2026-05-05T00:00:00Z", "no_assignee": True}
        )
        self.assertEqual([8], [i.number for i in result])


class OptionalStrTest(unittest.TestCase):
    def test_truthy_value_returns_string(self) -> None:
        self.assertEqual("hello", _optional_str("hello"))
        self.assertEqual("42", _optional_str(42))
        self.assertEqual("True", _optional_str(True))

    def test_falsy_value_returns_none(self) -> None:
        self.assertIsNone(_optional_str(""))
        self.assertIsNone(_optional_str(0))
        self.assertIsNone(_optional_str(None))
        self.assertIsNone(_optional_str(False))


class AddQualityCheckTest(unittest.TestCase):
    """Verify _add_quality_check uses valid QualityGateCheck status values."""

    def test_passed_check_uses_pass_status(self) -> None:
        checks: list[QualityGateCheck] = []
        blockers: list[str] = []
        _add_quality_check(checks, blockers, "test_check", True, "detail text")
        self.assertEqual(1, len(checks))
        self.assertEqual("pass", checks[0].status)
        self.assertEqual("test_check", checks[0].name)
        self.assertEqual("detail text", checks[0].detail)
        self.assertEqual([], blockers)

    def test_failed_check_uses_block_status_not_fail(self) -> None:
        checks: list[QualityGateCheck] = []
        blockers: list[str] = []
        _add_quality_check(checks, blockers, "failed_check", False, "block detail")
        self.assertEqual(1, len(checks))
        self.assertEqual("block", checks[0].status)
        self.assertEqual(["block detail"], blockers)

    def test_multiple_checks_accumulate_blockers(self) -> None:
        checks: list[QualityGateCheck] = []
        blockers: list[str] = []
        _add_quality_check(checks, blockers, "a", True, "ok")
        _add_quality_check(checks, blockers, "b", False, "b-fail")
        _add_quality_check(checks, blockers, "c", False, "c-fail")
        self.assertEqual(3, len(checks))
        self.assertEqual("pass", checks[0].status)
        self.assertEqual("block", checks[1].status)
        self.assertEqual("block", checks[2].status)
        self.assertEqual(["b-fail", "c-fail"], blockers)
