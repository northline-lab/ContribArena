from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from contribarena.memory.history_index import (
    DEFAULT_PRIORITIES,
    INTENT_ALIASES,
    INTENT_PRIORITIES,
    TEXT_SUFFIXES,
    TRACE_RECORD_TYPES,
    HistoryIndexResult,
    _resolve_intent,
    _is_text_artifact,
    _include_record_type,
    _query_terms,
    _reason_for_record,
    _record_id,
    _record_priority,
    _record_type,
    _snippet,
    _split_text,
    _title_for_record,
)


class TextSuffixesTest(unittest.TestCase):
    def test_contains_expected_suffixes(self) -> None:
        for suffix in (".json", ".jsonl", ".md", ".txt", ".log", ".diff", ".patch"):
            self.assertIn(suffix, TEXT_SUFFIXES)

    def test_count_matches_expected(self) -> None:
        self.assertEqual(7, len(TEXT_SUFFIXES))


class TraceRecordTypesTest(unittest.TestCase):
    def test_only_trace_event(self) -> None:
        self.assertEqual({"trace_event"}, TRACE_RECORD_TYPES)


class IntentAliasesTest(unittest.TestCase):
    def test_verification_aliases(self) -> None:
        for alias in ("ci", "test", "tests", "verify", "verification"):
            self.assertEqual("verification", INTENT_ALIASES[alias])

    def test_repo_context_aliases(self) -> None:
        for alias in ("guidance", "repo", "repo_context"):
            self.assertEqual("repo_context", INTENT_ALIASES[alias])

    def test_failure_aliases(self) -> None:
        for alias in ("failure", "error", "tool_error", "provider_error"):
            self.assertEqual("failure", INTENT_ALIASES[alias])

    def test_external_write_aliases(self) -> None:
        for alias in ("external", "external_write", "lifecycle", "pr"):
            self.assertEqual("external_write", INTENT_ALIASES[alias])

    def test_unknown_alias(self) -> None:
        self.assertEqual("unknown", INTENT_ALIASES["unknown"])

    def test_unregistered_intent_returns_unknown(self) -> None:
        self.assertNotIn("nonexistent", INTENT_ALIASES)


class DefaultPrioritiesTest(unittest.TestCase):
    def test_all_record_types_present(self) -> None:
        expected = {
            "quality_report",
            "postmortem",
            "memory_event",
            "working_memory",
            "repo_guidance",
            "memory_context",
            "verification",
            "quality_gate",
            "governance_decision",
            "ci_status",
            "live_action",
            "review_event",
            "tool_result",
            "artifact",
            "trace_event",
        }
        self.assertEqual(expected, set(DEFAULT_PRIORITIES.keys()))

    def test_trace_event_has_zero_priority(self) -> None:
        self.assertEqual(0, DEFAULT_PRIORITIES["trace_event"])

    def test_quality_report_is_highest(self) -> None:
        self.assertEqual(95, DEFAULT_PRIORITIES["quality_report"])


class IntentPrioritiesTest(unittest.TestCase):
    def test_all_intents_present(self) -> None:
        self.assertEqual(
            {"verification", "repo_context", "failure", "external_write"},
            set(INTENT_PRIORITIES.keys()),
        )

    def test_verification_verification_is_top(self) -> None:
        self.assertEqual(100, INTENT_PRIORITIES["verification"]["verification"])

    def test_repo_context_repo_guidance_is_top(self) -> None:
        self.assertEqual(100, INTENT_PRIORITIES["repo_context"]["repo_guidance"])

    def test_failure_postmortem_is_top(self) -> None:
        self.assertEqual(100, INTENT_PRIORITIES["failure"]["postmortem"])

    def test_external_write_live_action_is_top(self) -> None:
        self.assertEqual(100, INTENT_PRIORITIES["external_write"]["live_action"])


class HistoryIndexResultTest(unittest.TestCase):
    def test_defaults(self) -> None:
        result = HistoryIndexResult()
        self.assertEqual(0, result.entries_written)
        self.assertEqual([], result.indexed_sources)

    def test_explicit_values(self) -> None:
        result = HistoryIndexResult(entries_written=5, indexed_sources=["a", "b"])
        self.assertEqual(5, result.entries_written)
        self.assertEqual(["a", "b"], result.indexed_sources)

    def test_factory_independence(self) -> None:
        r1 = HistoryIndexResult()
        r2 = HistoryIndexResult()
        r1.indexed_sources.append("x")
        self.assertEqual([], r2.indexed_sources)


class IsTextArtifactTest(unittest.TestCase):
    def test_json_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("config.json")))

    def test_jsonl_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("trace.jsonl")))

    def test_md_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("README.md")))

    def test_txt_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("output.txt")))

    def test_log_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("test.log")))

    def test_diff_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("patch.diff")))

    def test_patch_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("fix.patch")))

    def test_no_extension(self) -> None:
        self.assertTrue(_is_text_artifact(Path("Makefile")))

    def test_binary_suffix(self) -> None:
        self.assertFalse(_is_text_artifact(Path("image.png")))

    def test_binary_suffix_pdf(self) -> None:
        self.assertFalse(_is_text_artifact(Path("doc.pdf")))

    def test_binary_suffix_tar(self) -> None:
        self.assertFalse(_is_text_artifact(Path("archive.tar.gz")))


class SplitTextTest(unittest.TestCase):
    def test_short_text_no_split(self) -> None:
        text = "hello world"
        self.assertEqual([text], _split_text(text, 100))

    def test_exact_max_chars_no_split(self) -> None:
        text = "x" * 100
        self.assertEqual([text], _split_text(text, 100))

    def test_long_text_splits(self) -> None:
        text = "line1\nline2\nline3\nline4\nline5\n"
        chunks = _split_text(text, 12)
        self.assertTrue(len(chunks) >= 2)
        self.assertEqual(text, "".join(chunks))

    def test_empty_text(self) -> None:
        self.assertEqual([""], _split_text("", 100))

    def test_single_line_exceeds_max(self) -> None:
        text = "a" * 200
        chunks = _split_text(text, 100)
        # Long single line is truncated per char limit
        self.assertTrue(len(chunks) >= 1)

    def test_preserves_line_endings(self) -> None:
        text = "line1\nline2\nline3\n"
        result = _split_text(text, 1000)
        self.assertEqual([text], result)


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

    def test_unknown_file(self) -> None:
        self.assertEqual("artifact", _record_type(Path("random_file.md")))

    def test_run_summary(self) -> None:
        self.assertEqual("artifact", _record_type(Path("run_summary.json")))


class RecordIdTest(unittest.TestCase):
    def test_known_hash(self) -> None:
        raw = "run1:trace.jsonl:1".encode("utf-8")
        expected = hashlib.sha256(raw).hexdigest()[:24]
        self.assertEqual(expected, _record_id("run1", "trace.jsonl", 1))

    def test_deterministic(self) -> None:
        self.assertEqual(
            _record_id("run1", "trace.jsonl", 1),
            _record_id("run1", "trace.jsonl", 1),
        )

    def test_different_inputs_produce_different_ids(self) -> None:
        self.assertNotEqual(
            _record_id("run1", "trace.jsonl", 1),
            _record_id("run2", "trace.jsonl", 1),
        )

    def test_length_is_24(self) -> None:
        self.assertEqual(24, len(_record_id("x", "y", 0)))


class QueryTermsTest(unittest.TestCase):
    def test_simple_query(self) -> None:
        terms = _query_terms("pytest fails")
        self.assertEqual(['"pytest"', '"fails"'], terms)

    def test_removes_quotes(self) -> None:
        terms = _query_terms('"quoted term" other')
        self.assertEqual(['"quoted"', '"term"', '"other"'], terms)

    def test_special_chars_removed(self) -> None:
        terms = _query_terms("test@#$% verification")
        self.assertEqual(['"test"', '"verification"'], terms)

    def test_max_8_terms(self) -> None:
        words = "a b c d e f g h i j"
        self.assertEqual(8, len(_query_terms(words)))

    def test_empty_query(self) -> None:
        self.assertEqual([], _query_terms(""))

    def test_preserves_underscores_and_dashes(self) -> None:
        terms = _query_terms("test_module run-config")
        self.assertEqual(['"test_module"', '"run-config"'], terms)


class ResolveIntentTest(unittest.TestCase):
    def test_verification_alias(self) -> None:
        self.assertEqual("verification", _resolve_intent("test", ""))

    def test_repo_context_alias(self) -> None:
        self.assertEqual("repo_context", _resolve_intent("guidance", ""))

    def test_failure_alias(self) -> None:
        self.assertEqual("failure", _resolve_intent("error", ""))

    def test_external_write_alias(self) -> None:
        self.assertEqual("external_write", _resolve_intent("pr", ""))

    def test_unknown_alias(self) -> None:
        self.assertEqual("unknown", _resolve_intent("unknown", ""))

    def test_query_heuristic_verification(self) -> None:
        self.assertEqual("verification", _resolve_intent("unknown", "pytest run fails"))

    def test_query_heuristic_repo_context(self) -> None:
        self.assertEqual("repo_context", _resolve_intent("unknown", "CONTRIBUTING.md template"))

    def test_query_heuristic_failure(self) -> None:
        self.assertEqual("failure", _resolve_intent("unknown", "error in workspace blocked"))

    def test_query_heuristic_external_write(self) -> None:
        self.assertEqual("external_write", _resolve_intent("unknown", "PR push fork review"))

    def test_no_match_returns_unknown(self) -> None:
        self.assertEqual("unknown", _resolve_intent("unknown", "random words here"))

    def test_alias_takes_priority_over_query(self) -> None:
        # Even if query contains "test", a repo_context alias wins
        self.assertEqual("repo_context", _resolve_intent("guidance", "test verification"))

    def test_case_insensitive(self) -> None:
        self.assertEqual("verification", _resolve_intent("TEST", ""))


class IncludeRecordTypeTest(unittest.TestCase):
    def test_non_trace_always_included(self) -> None:
        self.assertTrue(_include_record_type("quality_report", "verification"))
        self.assertTrue(_include_record_type("artifact", "unknown"))

    def test_trace_not_included_for_non_failure(self) -> None:
        self.assertFalse(_include_record_type("trace_event", "verification"))
        self.assertFalse(_include_record_type("trace_event", "repo_context"))
        self.assertFalse(_include_record_type("trace_event", "unknown"))

    def test_trace_included_for_failure(self) -> None:
        self.assertTrue(_include_record_type("trace_event", "failure"))


class RecordPriorityTest(unittest.TestCase):
    def test_verification_verification(self) -> None:
        self.assertEqual(100, _record_priority("verification", "verification"))

    def test_trace_event_default_priority(self) -> None:
        self.assertEqual(0, _record_priority("trace_event", "verification"))

    def test_trace_event_failure_priority(self) -> None:
        self.assertEqual(20, _record_priority("trace_event", "failure"))

    def test_unknown_intent_falls_to_default(self) -> None:
        self.assertEqual(95, _record_priority("quality_report", "unknown"))

    def test_unknown_record_type_falls_to_artifact_default(self) -> None:
        self.assertEqual(25, _record_priority("custom_type", "verification"))

    def test_repo_context_repo_guidance(self) -> None:
        self.assertEqual(100, _record_priority("repo_guidance", "repo_context"))


class TitleForRecordTest(unittest.TestCase):
    def test_quality_report(self) -> None:
        self.assertEqual("Quality report: quality_report.md", _title_for_record("quality_report.md", "quality_report"))

    def test_postmortem(self) -> None:
        self.assertEqual("Postmortem: postmortem.md", _title_for_record("postmortem.md", "postmortem"))

    def test_trace_event(self) -> None:
        self.assertEqual("Trace event: trace.jsonl", _title_for_record("trace.jsonl", "trace_event"))

    def test_unknown_type_uses_generic_label(self) -> None:
        self.assertEqual("Run artifact: unknown_file.json", _title_for_record("unknown_file.json", "custom_type"))


class ReasonForRecordTest(unittest.TestCase):
    def test_verification_intent(self) -> None:
        self.assertIn("verification query", _reason_for_record("verification", "verification"))

    def test_repo_context_intent(self) -> None:
        self.assertIn("repository-context query", _reason_for_record("repo_guidance", "repo_context"))

    def test_failure_intent(self) -> None:
        self.assertIn("failure query", _reason_for_record("postmortem", "failure"))

    def test_external_write_intent(self) -> None:
        self.assertIn("external-write query", _reason_for_record("live_action", "external_write"))

    def test_unknown_intent(self) -> None:
        self.assertIn("matched query", _reason_for_record("artifact", "unknown"))

    def test_includes_record_type(self) -> None:
        self.assertIn("quality_report", _reason_for_record("quality_report", "verification"))


class SnippetTest(unittest.TestCase):
    def test_short_text_unchanged(self) -> None:
        text = "short text"
        self.assertEqual("short text", _snippet(text))

    def test_exact_limit_unchanged(self) -> None:
        text = "x" * 800
        self.assertEqual(text, _snippet(text, 800))

    def test_long_text_truncated(self) -> None:
        text = "x" * 900
        result = _snippet(text, 800)
        self.assertTrue(len(result) < len(text))
        self.assertIn("[history result truncated]", result)

    def test_strips_whitespace(self) -> None:
        text = "  hello  "
        self.assertEqual("hello", _snippet(text))

    def test_empty_after_strip(self) -> None:
        self.assertEqual("", _snippet("   "))

    def test_custom_limit(self) -> None:
        text = "x" * 100
        result = _snippet(text, 50)
        # _snippet truncates at limit-24 then adds truncation marker,
        # so result can slightly exceed limit
        self.assertTrue(len(result) < 100)
        self.assertIn("[history result truncated]", result)


if __name__ == "__main__":
    unittest.main()
