from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from contribarena.memory.history_index import (
    _is_text_artifact,
    _query_terms,
    _include_record_type,
    _reason_for_record,
    _record_id,
    _record_priority,
    _record_type,
    _resolve_intent,
    _snippet,
    _split_text,
    _title_for_record,
)


class IsTextArtifactTest(unittest.TestCase):
    def test_json_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("run_summary.json")))

    def test_jsonl_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("trace.jsonl")))

    def test_md_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("postmortem.md")))

    def test_txt_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("test_log.txt")))

    def test_log_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("output.log")))

    def test_diff_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("patch.diff")))

    def test_patch_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("fix.patch")))

    def test_no_extension(self) -> None:
        self.assertTrue(_is_text_artifact(Path("Makefile")))

    def test_binary_suffix(self) -> None:
        self.assertFalse(_is_text_artifact(Path("image.png")))

    def test_bin_suffix(self) -> None:
        self.assertFalse(_is_text_artifact(Path("binary.bin")))

    def test_py_suffix_not_in_set(self) -> None:
        self.assertFalse(_is_text_artifact(Path("module.py")))

    def test_yaml_suffix_not_in_set(self) -> None:
        self.assertFalse(_is_text_artifact(Path("config.yaml")))

    def test_uppercase_suffix_matches(self) -> None:
        self.assertTrue(_is_text_artifact(Path("report.JSON")))


class SplitTextTest(unittest.TestCase):
    def test_short_text_returns_single_chunk(self) -> None:
        text = "short text"
        result = _split_text(text, max_chars=100)
        self.assertEqual(["short text"], result)

    def test_empty_text_returns_single_chunk(self) -> None:
        result = _split_text("", max_chars=100)
        self.assertEqual([""], result)

    def test_exact_max_chars_returns_single_chunk(self) -> None:
        text = "a" * 100
        result = _split_text(text, max_chars=100)
        self.assertEqual(1, len(result))
        self.assertEqual(100, len(result[0]))

    def test_long_text_splits_into_chunks(self) -> None:
        lines = ["line one\n", "line two\n", "line three\n"]
        text = "".join(lines)
        # Each line is 10 chars; with max_chars=10, splits into 3 chunks
        result = _split_text(text, max_chars=10)
        self.assertEqual(3, len(result))
        # Content is preserved (truncated lines may differ)
        joined = "".join(result)
        for original_line in lines:
            self.assertTrue(original_line.strip() in joined)

    def test_very_long_line_is_truncated_per_chunk(self) -> None:
        # _split_text truncates long lines to max_chars per line, not splitting into multiple chunks
        text = "a" * 500 + "\n"
        result = _split_text(text, max_chars=100)
        # The 500-char line is truncated to 100 chars + newline, fitting in a single chunk
        self.assertEqual(1, len(result))
        self.assertLessEqual(len(result[0]), 101)  # truncated line + newline

    def test_preserves_newlines_in_single_chunk(self) -> None:
        text = "line1\nline2\nline3\n"
        result = _split_text(text, max_chars=1000)
        self.assertEqual(text, result[0])


class RecordTypeTest(unittest.TestCase):
    def test_trace_jsonl(self) -> None:
        self.assertEqual("trace_event", _record_type(Path("trace.jsonl")))

    def test_workspace_command(self) -> None:
        self.assertEqual("tool_result", _record_type(Path("workspace_command.json")))

    def test_quality_gate(self) -> None:
        self.assertEqual("quality_gate", _record_type(Path("quality_gate.json")))

    def test_quality_report(self) -> None:
        self.assertEqual("quality_report", _record_type(Path("quality_report.md")))

    def test_test_log(self) -> None:
        self.assertEqual("verification", _record_type(Path("test_log.txt")))

    def test_ci_status(self) -> None:
        self.assertEqual("ci_status", _record_type(Path("ci_status.json")))

    def test_live_action_log(self) -> None:
        self.assertEqual("live_action", _record_type(Path("live_action_log.jsonl")))

    def test_pr_review_log(self) -> None:
        self.assertEqual("review_event", _record_type(Path("pr_review_log.jsonl")))

    def test_postmortem(self) -> None:
        self.assertEqual("postmortem", _record_type(Path("postmortem.md")))

    def test_memory_events(self) -> None:
        self.assertEqual("memory_event", _record_type(Path("memory_events.jsonl")))

    def test_working_memory(self) -> None:
        self.assertEqual("working_memory", _record_type(Path("working_memory.json")))

    def test_memory_context(self) -> None:
        self.assertEqual("memory_context", _record_type(Path("memory_context.json")))

    def test_repo_guidance(self) -> None:
        self.assertEqual("repo_guidance", _record_type(Path("repo_guidance.json")))

    def test_governance_decision(self) -> None:
        self.assertEqual("governance_decision", _record_type(Path("governance_decision.json")))

    def test_unknown_file_returns_artifact(self) -> None:
        self.assertEqual("artifact", _record_type(Path("random_file.txt")))

    def test_run_summary_returns_artifact(self) -> None:
        self.assertEqual("artifact", _record_type(Path("run_summary.json")))


class RecordIdTest(unittest.TestCase):
    def test_deterministic_hash(self) -> None:
        id1 = _record_id("run-1", "trace.jsonl", 1)
        id2 = _record_id("run-1", "trace.jsonl", 1)
        self.assertEqual(id1, id2)

    def test_different_inputs_produce_different_ids(self) -> None:
        id1 = _record_id("run-1", "trace.jsonl", 1)
        id2 = _record_id("run-2", "trace.jsonl", 1)
        self.assertNotEqual(id1, id2)

    def test_length_is_24_chars(self) -> None:
        result = _record_id("run-1", "source", 1)
        self.assertEqual(24, len(result))

    def test_matches_sha256_truncation(self) -> None:
        raw = "run-1:trace.jsonl:1".encode("utf-8")
        expected = hashlib.sha256(raw).hexdigest()[:24]
        self.assertEqual(expected, _record_id("run-1", "trace.jsonl", 1))


class QueryTermsTest(unittest.TestCase):
    def test_simple_query(self) -> None:
        result = _query_terms("pytest verification")
        self.assertEqual(['"pytest"', '"verification"'], result)

    def test_removes_quotes(self) -> None:
        result = _query_terms('hello "world" test')
        self.assertEqual(['"hello"', '"world"', '"test"'], result)

    def test_filters_special_chars(self) -> None:
        result = _query_terms("test@#$ verify!*&")
        self.assertEqual(['"test"', '"verify"'], result)

    def test_preserves_underscore_and_dash(self) -> None:
        result = _query_terms("test_log run-1")
        self.assertEqual(['"test_log"', '"run-1"'], result)

    def test_limits_to_8_terms(self) -> None:
        result = _query_terms("a b c d e f g h i j")
        self.assertEqual(8, len(result))

    def test_empty_query_returns_empty(self) -> None:
        result = _query_terms("")
        self.assertEqual([], result)

    def test_whitespace_only_returns_empty(self) -> None:
        result = _query_terms("   ")
        self.assertEqual([], result)


class ResolveIntentTest(unittest.TestCase):
    def test_explicit_verification_alias(self) -> None:
        self.assertEqual("verification", _resolve_intent("test", "any query"))

    def test_explicit_ci_alias(self) -> None:
        self.assertEqual("verification", _resolve_intent("ci", "any query"))

    def test_explicit_repo_context_alias(self) -> None:
        self.assertEqual("repo_context", _resolve_intent("guidance", "any query"))

    def test_explicit_failure_alias(self) -> None:
        self.assertEqual("failure", _resolve_intent("error", "any query"))

    def test_explicit_external_write_alias(self) -> None:
        self.assertEqual("external_write", _resolve_intent("pr", "any query"))

    def test_unknown_intent_with_test_query(self) -> None:
        self.assertEqual("verification", _resolve_intent("unknown", "pytest run"))

    def test_unknown_intent_with_guidance_query(self) -> None:
        self.assertEqual("repo_context", _resolve_intent("unknown", "contributing guide"))

    def test_unknown_intent_with_failure_query(self) -> None:
        self.assertEqual("failure", _resolve_intent("unknown", "error blocked"))

    def test_unknown_intent_with_pr_query(self) -> None:
        self.assertEqual("external_write", _resolve_intent("unknown", "push fork label"))

    def test_unknown_intent_with_unmatched_query(self) -> None:
        self.assertEqual("unknown", _resolve_intent("unknown", "random word"))

    def test_intent_case_insensitive(self) -> None:
        self.assertEqual("verification", _resolve_intent("TEST", "any"))


class IncludeRecordTypeTest(unittest.TestCase):
    def test_trace_excluded_by_default(self) -> None:
        self.assertFalse(_include_record_type("trace_event", "verification"))

    def test_trace_excluded_for_repo_context(self) -> None:
        self.assertFalse(_include_record_type("trace_event", "repo_context"))

    def test_trace_included_for_failure_intent(self) -> None:
        self.assertTrue(_include_record_type("trace_event", "failure"))

    def test_trace_excluded_for_unknown_intent(self) -> None:
        self.assertFalse(_include_record_type("trace_event", "unknown"))

    def test_non_trace_type_always_included(self) -> None:
        self.assertTrue(_include_record_type("quality_report", "verification"))

    def test_non_trace_type_included_for_failure(self) -> None:
        self.assertTrue(_include_record_type("postmortem", "failure"))


class RecordPriorityTest(unittest.TestCase):
    def test_verification_intent_quality_report(self) -> None:
        self.assertEqual(95, _record_priority("quality_report", "verification"))

    def test_verification_intent_ci_status(self) -> None:
        self.assertEqual(90, _record_priority("ci_status", "verification"))

    def test_repo_context_intent_repo_guidance(self) -> None:
        self.assertEqual(100, _record_priority("repo_guidance", "repo_context"))

    def test_failure_intent_postmortem(self) -> None:
        self.assertEqual(100, _record_priority("postmortem", "failure"))

    def test_external_write_intent_live_action(self) -> None:
        self.assertEqual(100, _record_priority("live_action", "external_write"))

    def test_unknown_intent_uses_default(self) -> None:
        self.assertEqual(85, _record_priority("memory_event", "unknown"))

    def test_unknown_intent_unknown_type_uses_artifact_default(self) -> None:
        self.assertEqual(25, _record_priority("weird_type", "unknown"))


class TitleForRecordTest(unittest.TestCase):
    def test_quality_report(self) -> None:
        self.assertEqual("Quality report: quality_report.md", _title_for_record("quality_report.md", "quality_report"))

    def test_postmortem(self) -> None:
        self.assertEqual("Postmortem: postmortem.md", _title_for_record("postmortem.md", "postmortem"))

    def test_trace_event(self) -> None:
        self.assertEqual("Trace event: trace.jsonl", _title_for_record("trace.jsonl", "trace_event"))

    def test_unknown_type_falls_back_to_run_artifact(self) -> None:
        self.assertEqual("Run artifact: something.txt", _title_for_record("something.txt", "weird_type"))


class ReasonForRecordTest(unittest.TestCase):
    def test_verification_intent(self) -> None:
        self.assertIn("verification query", _reason_for_record("quality_report", "verification"))

    def test_repo_context_intent(self) -> None:
        self.assertIn("repository-context query", _reason_for_record("repo_guidance", "repo_context"))

    def test_failure_intent(self) -> None:
        self.assertIn("failure query", _reason_for_record("postmortem", "failure"))

    def test_external_write_intent(self) -> None:
        self.assertIn("external-write query", _reason_for_record("live_action", "external_write"))

    def test_unknown_intent(self) -> None:
        self.assertIn("matched query", _reason_for_record("artifact", "unknown"))


class SnippetTest(unittest.TestCase):
    def test_short_text_preserved(self) -> None:
        text = "short text"
        self.assertEqual("short text", _snippet(text))

    def test_empty_text_returns_empty(self) -> None:
        self.assertEqual("", _snippet(""))

    def test_whitespace_only_returns_empty(self) -> None:
        self.assertEqual("", _snippet("   "))

    def test_long_text_truncated(self) -> None:
        text = "a" * 900
        result = _snippet(text, limit=800)
        self.assertTrue(result.endswith("[history result truncated]"))
        # _snippet uses text[:limit-24] + "\n[history result truncated]\" which is limit+3 chars
        self.assertLessEqual(len(result), 803)

    def test_exact_limit_preserved(self) -> None:
        text = "a" * 800
        self.assertEqual(text, _snippet(text, limit=800))

    def test_just_over_limit_truncated(self) -> None:
        text = "a" * 801
        result = _snippet(text, limit=800)
        self.assertTrue(result.endswith("[history result truncated]"))

    def test_leading_trailing_whitespace_stripped(self) -> None:
        text = "  content  "
        self.assertEqual("content", _snippet(text))


if __name__ == "__main__":
    unittest.main()
