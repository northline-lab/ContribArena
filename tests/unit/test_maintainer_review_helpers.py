from __future__ import annotations

import json
import unittest

from contribarena.engine.maintainer_review import (
    MaintainerReviewResult,
    _fallback_review,
    _json_object_text,
    _parse_review,
    _string_list,
)


class JsonObjectTextTest(unittest.TestCase):
    def test_plain_json_object(self) -> None:
        result = _json_object_text('{"key": "value"}')
        self.assertEqual('{"key": "value"}', result)

    def test_whitespace_stripped(self) -> None:
        result = _json_object_text('  \n{"a":1}  \n')
        self.assertEqual('{"a":1}', result)

    def test_extracts_json_from_wrapped_text(self) -> None:
        result = _json_object_text('some prefix {"severity":"approve"} trailing')
        self.assertEqual('{"severity":"approve"}', result)

    def test_nested_json_selects_outermost(self) -> None:
        result = _json_object_text('{"nested": {"inner": 1}}')
        self.assertEqual('{"nested": {"inner": 1}}', result)

    def test_no_braces_returns_original(self) -> None:
        result = _json_object_text('no braces here')
        self.assertEqual('no braces here', result)

    def test_unmatched_opening_brace(self) -> None:
        result = _json_object_text('{open but no close')
        self.assertEqual('{open but no close', result)

    def test_empty_string(self) -> None:
        result = _json_object_text('')
        self.assertEqual('', result)

    def test_only_braces(self) -> None:
        result = _json_object_text('{}')
        self.assertEqual('{}', result)


class StringListTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        result = _string_list([])
        self.assertEqual([], result)

    def test_list_of_strings(self) -> None:
        result = _string_list(['a', 'b', 'c'])
        self.assertEqual(['a', 'b', 'c'], result)

    def test_non_list_returns_empty(self) -> None:
        result = _string_list('not a list')
        self.assertEqual([], result)

    def test_none_returns_empty(self) -> None:
        result = _string_list(None)
        self.assertEqual([], result)

    def test_dict_returns_empty(self) -> None:
        result = _string_list({'key': 'value'})
        self.assertEqual([], result)

    def test_truncates_at_ten_items(self) -> None:
        result = _string_list([str(i) for i in range(15)])
        self.assertEqual(10, len(result))
        self.assertEqual(['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'], result)

    def test_filters_empty_strings(self) -> None:
        result = _string_list(['a', '', '  ', 'b'])
        self.assertEqual(['a', 'b'], result)

    def test_coerces_non_strings_to_strings(self) -> None:
        result = _string_list([1, 2.5, True])
        self.assertEqual(['1', '2.5', 'True'], result)

    def test_redacts_text(self) -> None:
        result = _string_list(['Secret: ghp_abcdefghijklmnopqrstuvwxyz1234567890'])
        self.assertNotIn('ghp_abcdefghijklmnopqrstuvwxyz1234567890', result[0])


class FallbackReviewTest(unittest.TestCase):
    def test_unavailable_flag(self) -> None:
        result = _fallback_review('review_simulator_unavailable', 1)
        self.assertTrue(result.unavailable)

    def test_row_contains_status(self) -> None:
        result = _fallback_review('custom_status', 3)
        self.assertEqual('custom_status', result.row['status'])

    def test_row_contains_round(self) -> None:
        result = _fallback_review('test', 5)
        self.assertEqual(5, result.row['round'])

    def test_no_error_by_default(self) -> None:
        result = _fallback_review('test', 1)
        self.assertEqual('', result.row['error'])

    def test_error_passed_through(self) -> None:
        result = _fallback_review('test', 1, error='something went wrong')
        self.assertEqual('something went wrong', result.row['error'])

    def test_schema_version_present(self) -> None:
        result = _fallback_review('test', 1)
        self.assertEqual('1', result.row['schema_version'])

    def test_review_mode_is_self(self) -> None:
        result = _fallback_review('test', 1)
        self.assertEqual('self', result.row['review_mode'])

    def test_reviewer_model_is_unavailable(self) -> None:
        result = _fallback_review('test', 1)
        self.assertEqual('unavailable', result.row['reviewer_model'])

    def test_severity_is_unavailable(self) -> None:
        result = _fallback_review('test', 1)
        self.assertEqual('unavailable', result.row['severity'])

    def test_concerns_and_suggested_changes_are_empty(self) -> None:
        result = _fallback_review('test', 1)
        self.assertEqual([], result.row['concerns'])
        self.assertEqual([], result.row['suggested_changes'])


class ParseReviewTest(unittest.TestCase):
    def test_parses_valid_json(self) -> None:
        result = _parse_review(
            json.dumps({
                'severity': 'approve',
                'concerns': ['minor issue'],
                'suggested_changes': ['fix typo'],
                'summary': 'looks good',
            }),
            1,
            reviewer_model='test-model',
        )
        self.assertFalse(result.unavailable)
        self.assertEqual('approve', result.row['severity'])
        self.assertEqual(['minor issue'], result.row['concerns'])
        self.assertEqual(['fix typo'], result.row['suggested_changes'])
        self.assertEqual('looks good', result.row['summary'])

    def test_parses_json_with_wrapping_text(self) -> None:
        result = _parse_review(
            'Prefix text here\n{"severity":"comment","concerns":[],"suggested_changes":[],"summary":"ok"}\nSuffix',
            1,
            reviewer_model='test',
        )
        self.assertFalse(result.unavailable)
        self.assertEqual('comment', result.row['severity'])

    def test_invalid_json_falls_back(self) -> None:
        result = _parse_review('not json at all', 2, reviewer_model='test')
        self.assertTrue(result.unavailable)
        self.assertEqual('review_simulator_unavailable', result.row['status'])
        self.assertIn('non-json', result.row['error'])

    def test_non_dict_json_falls_back(self) -> None:
        result = _parse_review('["array", "not", "object"]', 3, reviewer_model='test')
        self.assertTrue(result.unavailable)
        self.assertIn('non-object', result.row['error'])

    def test_unknown_severity_defaults_to_comment(self) -> None:
        result = _parse_review(
            json.dumps({'severity': 'unknown_value', 'concerns': [], 'suggested_changes': [], 'summary': ''}),
            1,
            reviewer_model='test',
        )
        self.assertFalse(result.unavailable)
        self.assertEqual('comment', result.row['severity'])

    def test_missing_severity_defaults_to_comment(self) -> None:
        result = _parse_review(
            json.dumps({'concerns': [], 'suggested_changes': [], 'summary': ''}),
            1,
            reviewer_model='test',
        )
        self.assertFalse(result.unavailable)
        self.assertEqual('comment', result.row['severity'])

    def test_request_changes_severity_accepted(self) -> None:
        result = _parse_review(
            json.dumps({'severity': 'request_changes', 'concerns': ['big issue'], 'suggested_changes': ['rewrite'], 'summary': 'needs work'}),
            1,
            reviewer_model='test',
        )
        self.assertFalse(result.unavailable)
        self.assertEqual('request_changes', result.row['severity'])

    def test_reviewer_model_passed_to_row(self) -> None:
        result = _parse_review(
            json.dumps({'severity': 'approve', 'concerns': [], 'suggested_changes': [], 'summary': ''}),
            1,
            reviewer_model='claude-4',
        )
        self.assertEqual('claude-4', result.row['reviewer_model'])

    def test_round_number_in_row(self) -> None:
        result = _parse_review(
            json.dumps({'severity': 'approve', 'concerns': [], 'suggested_changes': [], 'summary': ''}),
            7,
            reviewer_model='test',
        )
        self.assertEqual(7, result.row['round'])

    def test_concerns_coerced_via_string_list(self) -> None:
        result = _parse_review(
            json.dumps({'severity': 'approve', 'concerns': ['a', '', 'b'], 'suggested_changes': [], 'summary': ''}),
            1,
            reviewer_model='test',
        )
        self.assertEqual(['a', 'b'], result.row['concerns'])

    def test_summary_redacted(self) -> None:
        result = _parse_review(
            json.dumps({'severity': 'approve', 'concerns': [], 'suggested_changes': [], 'summary': 'Safe text'}),
            1,
            reviewer_model='test',
        )
        self.assertEqual('Safe text', result.row['summary'])


class MaintainerReviewResultTest(unittest.TestCase):
    def test_frozen_dataclass(self) -> None:
        result = _fallback_review('test', 1)
        self.assertIsInstance(result, MaintainerReviewResult)

    def test_default_unavailable_is_false(self) -> None:
        result = _parse_review(
            json.dumps({'severity': 'approve', 'concerns': [], 'suggested_changes': [], 'summary': ''}),
            1,
            reviewer_model='test',
        )
        self.assertFalse(result.unavailable)
