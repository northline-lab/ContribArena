from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from contribarena.memory._helpers import (
    DEFAULT_PRIORITIES,
    INTENT_ALIASES,
    INTENT_PRIORITIES,
    TEXT_SUFFIXES,
    TRACE_RECORD_TYPES,
    is_text_artifact,
    include_record_type,
    query_terms,
    record_id,
    record_priority,
    record_type,
    reason_for_record,
    snippet,
    split_text,
    title_for_record,
    resolve_intent,
)


class IsTextArtifactTest(unittest.TestCase):
    def test_known_suffixes_are_text(self) -> None:
        for suffix in TEXT_SUFFIXES:
            path = Path(f"example{suffix}")
            self.assertTrue(is_text_artifact(path), f"{suffix} should be a text artifact")

    def test_unknown_suffix_is_not_text(self) -> None:
        self.assertFalse(is_text_artifact(Path("image.png")))
        self.assertFalse(is_text_artifact(Path("data.csv")))
        self.assertFalse(is_text_artifact(Path("archive.tar.gz")))

    def test_no_extension_is_text(self) -> None:
        self.assertTrue(is_text_artifact(Path("Makefile")))
        self.assertTrue(is_text_artifact(Path("Dockerfile")))

    def test_suffix_case_insensitive(self) -> None:
        self.assertTrue(is_text_artifact(Path("report.JSON")))
        self.assertTrue(is_text_artifact(Path("log.TXT")))


class SplitTextTest(unittest.TestCase):
    def test_short_text_returns_single_chunk(self) -> None:
        text = "short text"
        result = split_text(text, 100)
        self.assertEqual(result, [text])

    def test_long_text_splits_into_chunks(self) -> None:
        lines = ["line\n" for _ in range(20)]
        text = "".join(lines)
        result = split_text(text, 30)
        self.assertTrue(len(result) > 1)
        for chunk in result:
            self.assertTrue(len(chunk) <= 30 or len(chunk.splitlines()) == 1)

    def test_preserves_line_content(self) -> None:
        text = "hello world\nsecond line\n"
        result = split_text(text, 1000)
        self.assertEqual(result, [text])

    def test_empty_string_returns_single_empty_chunk(self) -> None:
        result = split_text("", 100)
        self.assertEqual(result, [""])


class RecordTypeTest(unittest.TestCase):
    def test_trace_jsonl(self) -> None:
        self.assertEqual(record_type(Path("trace.jsonl")), "trace_event")

    def test_workspace_command(self) -> None:
        self.assertEqual(record_type(Path("workspace_command.json")), "tool_result")

    def test_quality_gate(self) -> None:
        self.assertEqual(record_type(Path("quality_gate.json")), "quality_gate")

    def test_quality_report(self) -> None:
        self.assertEqual(record_type(Path("quality_report.md")), "quality_report")

    def test_test_log(self) -> None:
        self.assertEqual(record_type(Path("test_log.txt")), "verification")

    def test_ci_status(self) -> None:
        self.assertEqual(record_type(Path("ci_status.json")), "ci_status")

    def test_live_action_log(self) -> None:
        self.assertEqual(record_type(Path("live_action_log.jsonl")), "live_action")

    def test_pr_review_log(self) -> None:
        self.assertEqual(record_type(Path("pr_review_log.jsonl")), "review_event")

    def test_postmortem(self) -> None:
        self.assertEqual(record_type(Path("postmortem.md")), "postmortem")

    def test_memory_events(self) -> None:
        self.assertEqual(record_type(Path("memory_events.jsonl")), "memory_event")

    def test_working_memory(self) -> None:
        self.assertEqual(record_type(Path("working_memory.json")), "working_memory")

    def test_memory_context(self) -> None:
        self.assertEqual(record_type(Path("memory_context.json")), "memory_context")

    def test_repo_guidance(self) -> None:
        self.assertEqual(record_type(Path("repo_guidance.json")), "repo_guidance")

    def test_governance_decision(self) -> None:
        self.assertEqual(record_type(Path("governance_decision.json")), "governance_decision")

    def test_unknown_file_returns_artifact(self) -> None:
        self.assertEqual(record_type(Path("random_file.txt")), "artifact")
        self.assertEqual(record_type(Path("custom.json")), "artifact")


class RecordIdTest(unittest.TestCase):
    def test_deterministic_hash(self) -> None:
        id1 = record_id("run-1", "trace.jsonl", 1)
        id2 = record_id("run-1", "trace.jsonl", 1)
        self.assertEqual(id1, id2)

    def test_different_inputs_produce_different_ids(self) -> None:
        id1 = record_id("run-1", "trace.jsonl", 1)
        id2 = record_id("run-2", "trace.jsonl", 1)
        self.assertNotEqual(id1, id2)

    def test_length_is_24(self) -> None:
        rid = record_id("run-1", "source", 1)
        self.assertEqual(len(rid), 24)

    def test_matches_sha256_prefix(self) -> None:
        raw = "run-1:trace.jsonl:1".encode("utf-8")
        expected = hashlib.sha256(raw).hexdigest()[:24]
        self.assertEqual(record_id("run-1", "trace.jsonl", 1), expected)


class QueryTermsTest(unittest.TestCase):
    def test_simple_query(self) -> None:
        terms = query_terms("test verification")
        self.assertEqual(terms, ['"test"', '"verification"'])

    def test_removes_special_chars(self) -> None:
        terms = query_terms("hello! world? @symbol")
        self.assertEqual(terms, ['"hello"', '"world"', '"symbol"'])

    def test_removes_quotes(self) -> None:
        terms = query_terms('"quoted term"')
        self.assertEqual(terms, ['"quoted"', '"term"'])

    def test_limits_to_eight_terms(self) -> None:
        self.assertEqual(len(query_terms("one two three four five six seven eight nine ten")), 8)

    def test_empty_query_returns_empty(self) -> None:
        self.assertEqual(query_terms(""), [])

    def test_preserves_alphanumeric_and_hyphens(self) -> None:
        terms = query_terms("my-key_test")
        self.assertEqual(terms, ['"my-key_test"'])


class ResolveIntentTest(unittest.TestCase):
    def test_known_aliases(self) -> None:
        self.assertEqual(resolve_intent("ci", ""), "verification")
        self.assertEqual(resolve_intent("test", ""), "verification")
        self.assertEqual(resolve_intent("guidance", ""), "repo_context")
        self.assertEqual(resolve_intent("failure", ""), "failure")
        self.assertEqual(resolve_intent("external", ""), "external_write")

    def test_unknown_intent_with_verification_keywords(self) -> None:
        self.assertEqual(resolve_intent("unknown", "pytest results"), "verification")
        self.assertEqual(resolve_intent("unknown", "ci run status"), "verification")

    def test_unknown_intent_with_repo_context_keywords(self) -> None:
        self.assertEqual(resolve_intent("unknown", "AGENTS.md guidance"), "repo_context")
        self.assertEqual(resolve_intent("unknown", "read contributing guide"), "repo_context")

    def test_unknown_intent_with_failure_keywords(self) -> None:
        self.assertEqual(resolve_intent("unknown", "error occurred"), "failure")
        self.assertEqual(resolve_intent("unknown", "blocked command"), "failure")

    def test_unknown_intent_with_external_write_keywords(self) -> None:
        self.assertEqual(resolve_intent("unknown", "push branch"), "external_write")
        self.assertEqual(resolve_intent("unknown", "pr lifecycle review"), "external_write")

    def test_unknown_intent_no_keywords(self) -> None:
        self.assertEqual(resolve_intent("unknown", "random query"), "unknown")

    def test_alias_overrides_query_heuristics(self) -> None:
        self.assertEqual(resolve_intent("verification", "pr push"), "verification")


class IncludeRecordTypeTest(unittest.TestCase):
    def test_non_trace_always_included(self) -> None:
        for rt in ("quality_report", "verification", "artifact", "tool_result"):
            self.assertTrue(include_record_type(rt, "unknown"))

    def test_trace_excluded_for_non_failure_intent(self) -> None:
        for intent in ("verification", "repo_context", "unknown", "external_write"):
            self.assertFalse(include_record_type("trace_event", intent))

    def test_trace_included_for_failure_intent(self) -> None:
        self.assertTrue(include_record_type("trace_event", "failure"))


class RecordPriorityTest(unittest.TestCase):
    def test_intent_specific_priority(self) -> None:
        self.assertEqual(record_priority("verification", "verification"), 100)

    def test_default_priority_fallback(self) -> None:
        self.assertEqual(record_priority("artifact", "repo_context"), 35)

    def test_fallback_to_artifact_default(self) -> None:
        artifact_default = DEFAULT_PRIORITIES["artifact"]
        self.assertEqual(record_priority("nonexistent_type", "unknown"), artifact_default)

    def test_intent_not_in_priorities_uses_default(self) -> None:
        self.assertEqual(
            record_priority("quality_report", "unknown"),
            DEFAULT_PRIORITIES["quality_report"],
        )


class TitleForRecordTest(unittest.TestCase):
    def test_known_type(self) -> None:
        self.assertEqual(
            title_for_record("quality_report.md", "quality_report"),
            "Quality report: quality_report.md",
        )

    def test_unknown_type(self) -> None:
        self.assertEqual(
            title_for_record("custom.json", "unknown_type"),
            "Run artifact: custom.json",
        )

    def test_all_known_types_not_use_generic_label(self) -> None:
        known_types = set(DEFAULT_PRIORITIES.keys()) | TRACE_RECORD_TYPES
        for rt in known_types:
            title = title_for_record("file", rt)
            if rt == "artifact":
                self.assertEqual(title, "Run artifact: file")
            else:
                self.assertNotEqual(title, "Run artifact: file")


class ReasonForRecordTest(unittest.TestCase):
    def test_verification_intent(self) -> None:
        reason = reason_for_record("quality_report", "verification")
        self.assertIn("verification", reason)
        self.assertIn("quality_report", reason)

    def test_repo_context_intent(self) -> None:
        reason = reason_for_record("repo_guidance", "repo_context")
        self.assertIn("repository-context", reason)
        self.assertIn("repo_guidance", reason)

    def test_failure_intent(self) -> None:
        reason = reason_for_record("postmortem", "failure")
        self.assertIn("failure", reason)
        self.assertIn("postmortem", reason)

    def test_external_write_intent(self) -> None:
        reason = reason_for_record("live_action", "external_write")
        self.assertIn("external-write", reason)
        self.assertIn("live_action", reason)

    def test_unknown_intent(self) -> None:
        reason = reason_for_record("artifact", "unknown")
        self.assertIn("matched query", reason)
        self.assertIn("artifact", reason)


class SnippetTest(unittest.TestCase):
    def test_short_text_not_truncated(self) -> None:
        self.assertEqual(snippet("short", 800), "short")

    def test_long_text_truncated_with_marker(self) -> None:
        long_text = "x" * 1000
        result = snippet(long_text, 800)
        self.assertTrue(result.endswith("\n[history result truncated]"))
        self.assertEqual(len(result), 800 - 24 + len("\n[history result truncated]"))

    def test_strip_whitespace(self) -> None:
        self.assertEqual(snippet("  hello  ", 800), "hello")

    def test_custom_limit(self) -> None:
        text = "a" * 200
        result = snippet(text, 100)
        self.assertTrue(result.endswith("\n[history result truncated]"))
        self.assertEqual(len(result), 100 - 24 + len("\n[history result truncated]"))


if __name__ == "__main__":
    unittest.main()
