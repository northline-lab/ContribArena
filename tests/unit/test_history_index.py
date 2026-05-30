from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from contribarena.memory.history_index import (
    INTENT_ALIASES,
    INTENT_PRIORITIES,
    DEFAULT_PRIORITIES,
    TEXT_SUFFIXES,
    TRACE_RECORD_TYPES,
    _is_text_artifact,
    _record_id,
    _record_type,
    _resolve_intent,
    _split_text,
    _query_terms,
    _include_record_type,
    _record_priority,
    _title_for_record,
    _reason_for_record,
    _snippet,
)


# ── TEXT_SUFFIXES ──


class TextSuffixesTest(unittest.TestCase):
    EXPECTED_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".log", ".diff", ".patch"}

    def test_all_suffixes_present(self) -> None:
        self.assertEqual(TEXT_SUFFIXES, self.EXPECTED_SUFFIXES)

    def test_suffixes_are_strings(self) -> None:
        for suffix in TEXT_SUFFIXES:
            self.assertIsInstance(suffix, str)
            self.assertTrue(suffix.startswith("."))


# ── TRACE_RECORD_TYPES ──


class TraceRecordTypesTest(unittest.TestCase):
    def test_only_trace_event(self) -> None:
        self.assertEqual(TRACE_RECORD_TYPES, {"trace_event"})


# ── INTENT_ALIASES ──


class IntentAliasesTest(unittest.TestCase):
    EXPECTED_ALIASES = {
        "ci": "verification",
        "test": "verification",
        "tests": "verification",
        "verify": "verification",
        "verification": "verification",
        "guidance": "repo_context",
        "repo": "repo_context",
        "repo_context": "repo_context",
        "failure": "failure",
        "error": "failure",
        "tool_error": "failure",
        "provider_error": "failure",
        "external": "external_write",
        "external_write": "external_write",
        "lifecycle": "external_write",
        "pr": "external_write",
        "unknown": "unknown",
    }

    def test_all_aliases_match(self) -> None:
        self.assertEqual(INTENT_ALIASES, self.EXPECTED_ALIASES)

    def test_verification_aliases(self) -> None:
        for alias in ("ci", "test", "tests", "verify", "verification"):
            self.assertEqual(INTENT_ALIASES[alias], "verification")

    def test_repo_context_aliases(self) -> None:
        for alias in ("guidance", "repo", "repo_context"):
            self.assertEqual(INTENT_ALIASES[alias], "repo_context")

    def test_failure_aliases(self) -> None:
        for alias in ("failure", "error", "tool_error", "provider_error"):
            self.assertEqual(INTENT_ALIASES[alias], "failure")

    def test_external_write_aliases(self) -> None:
        for alias in ("external", "external_write", "lifecycle", "pr"):
            self.assertEqual(INTENT_ALIASES[alias], "external_write")

    def test_unknown_alias(self) -> None:
        self.assertEqual(INTENT_ALIASES["unknown"], "unknown")


# ── DEFAULT_PRIORITIES ──


class DefaultPrioritiesTest(unittest.TestCase):
    EXPECTED_PRIORITIES = {
        "quality_report": 95,
        "postmortem": 90,
        "memory_event": 85,
        "working_memory": 80,
        "repo_guidance": 75,
        "memory_context": 70,
        "verification": 65,
        "quality_gate": 60,
        "governance_decision": 55,
        "ci_status": 50,
        "live_action": 45,
        "review_event": 45,
        "tool_result": 35,
        "artifact": 25,
        "trace_event": 0,
    }

    def test_all_priorities_match(self) -> None:
        self.assertEqual(DEFAULT_PRIORITIES, self.EXPECTED_PRIORITIES)

    def test_priorities_are_integers(self) -> None:
        for key, value in DEFAULT_PRIORITIES.items():
            self.assertIsInstance(value, int)

    def test_priorities_ordering(self) -> None:
        # Quality items should have higher priority than trace items
        self.assertGreater(DEFAULT_PRIORITIES["quality_report"], DEFAULT_PRIORITIES["trace_event"])
        self.assertGreater(DEFAULT_PRIORITIES["postmortem"], DEFAULT_PRIORITIES["artifact"])


# ── INTENT_PRIORITIES ──


class IntentPrioritiesTest(unittest.TestCase):
    def test_all_intents_present(self) -> None:
        for intent in ("verification", "repo_context", "failure", "external_write"):
            self.assertIn(intent, INTENT_PRIORITIES)

    def test_verification_priorities(self) -> None:
        expected = {
            "verification": 100,
            "quality_report": 95,
            "ci_status": 90,
            "quality_gate": 80,
            "postmortem": 60,
            "artifact": 35,
        }
        self.assertEqual(INTENT_PRIORITIES["verification"], expected)

    def test_repo_context_priorities(self) -> None:
        expected = {
            "repo_guidance": 100,
            "working_memory": 95,
            "memory_event": 90,
            "memory_context": 80,
            "quality_report": 55,
            "artifact": 35,
        }
        self.assertEqual(INTENT_PRIORITIES["repo_context"], expected)

    def test_failure_priorities(self) -> None:
        expected = {
            "postmortem": 100,
            "quality_gate": 95,
            "quality_report": 85,
            "tool_result": 75,
            "verification": 70,
            "trace_event": 20,
            "artifact": 30,
        }
        self.assertEqual(INTENT_PRIORITIES["failure"], expected)

    def test_external_write_priorities(self) -> None:
        expected = {
            "live_action": 100,
            "review_event": 95,
            "governance_decision": 90,
            "ci_status": 80,
            "repo_guidance": 45,
            "quality_report": 40,
            "artifact": 25,
        }
        self.assertEqual(INTENT_PRIORITIES["external_write"], expected)


# ── _is_text_artifact ──


class IsTextArtifactTest(unittest.TestCase):
    def test_json_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("result.json")))

    def test_jsonl_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("trace.jsonl")))

    def test_md_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("report.md")))

    def test_txt_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("output.txt")))

    def test_log_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("debug.log")))

    def test_diff_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("changes.diff")))

    def test_patch_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("fix.patch")))

    def test_no_extension(self) -> None:
        self.assertTrue(_is_text_artifact(Path("Makefile")))

    def test_binary_suffix_rejected(self) -> None:
        self.assertFalse(_is_text_artifact(Path("image.png")))

    def test_py_suffix_rejected(self) -> None:
        self.assertFalse(_is_text_artifact(Path("module.py")))

    def test_yaml_suffix_rejected(self) -> None:
        self.assertFalse(_is_text_artifact(Path("config.yaml")))

    def test_case_insensitive_suffix(self) -> None:
        self.assertTrue(_is_text_artifact(Path("data.JSON")))


# ── _record_type ──


class RecordTypeTest(unittest.TestCase):
    KNOWN_FILES = {
        "trace.jsonl": "trace_event",
        "workspace_command.json": "tool_result",
        "quality_gate.json": "quality_gate",
        "quality_report.md": "quality_report",
        "test_log.txt": "verification",
        "ci_status.json": "ci_status",
        "live_action_log.jsonl": "live_action",
        "pr_review_log.jsonl": "review_event",
        "postmortem.md": "postmortem",
        "memory_events.jsonl": "memory_event",
        "working_memory.json": "working_memory",
        "memory_context.json": "memory_context",
        "repo_guidance.json": "repo_guidance",
        "governance_decision.json": "governance_decision",
    }

    def test_all_known_files(self) -> None:
        for filename, expected_type in self.KNOWN_FILES.items():
            self.assertEqual(_record_type(Path(filename)), expected_type)

    def test_unknown_file_returns_artifact(self) -> None:
        self.assertEqual(_record_type(Path("unknown_output.txt")), "artifact")

    def test_generic_json_returns_artifact(self) -> None:
        self.assertEqual(_record_type(Path("results.json")), "artifact")

    def test_generic_md_returns_artifact(self) -> None:
        self.assertEqual(_record_type(Path("notes.md")), "artifact")


# ── _record_id ──


class RecordIdTest(unittest.TestCase):
    def test_returns_hex_string(self) -> None:
        result = _record_id("run1", "trace.jsonl", 1)
        self.assertTrue(all(c in "0123456789abcdef" for c in result))

    def test_length_is_24(self) -> None:
        result = _record_id("run1", "trace.jsonl", 1)
        self.assertEqual(len(result), 24)

    def test_deterministic(self) -> None:
        result1 = _record_id("run1", "trace.jsonl", 1)
        result2 = _record_id("run1", "trace.jsonl", 1)
        self.assertEqual(result1, result2)

    def test_different_inputs_different_ids(self) -> None:
        result1 = _record_id("run1", "trace.jsonl", 1)
        result2 = _record_id("run1", "trace.jsonl", 2)
        self.assertNotEqual(result1, result2)

    def test_matches_sha256_prefix(self) -> None:
        raw = "run1:trace.jsonl:1".encode("utf-8")
        expected = hashlib.sha256(raw).hexdigest()[:24]
        self.assertEqual(_record_id("run1", "trace.jsonl", 1), expected)


# ── _split_text ──


class SplitTextTest(unittest.TestCase):
    def test_short_text_single_chunk(self) -> None:
        text = "short text"
        result = _split_text(text, 1000)
        self.assertEqual(result, ["short text"])

    def test_empty_text(self) -> None:
        result = _split_text("", 1000)
        self.assertEqual(result, [""])

    def test_exact_boundary(self) -> None:
        text = "a" * 10
        result = _split_text(text, 10)
        self.assertEqual(result, ["a" * 10])

    def test_text_longer_than_max(self) -> None:
        lines = ["line1\n", "line2\n", "line3\n"]
        text = "".join(lines)
        # Each line is 6 chars, max 12 means line1+line2=12, line3 alone
        result = _split_text(text, 12)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "line1\nline2\n")
        self.assertEqual(result[1], "line3\n")

    def test_single_long_line_truncated(self) -> None:
        text = "abcdefghij\n"  # 11 chars with newline
        result = _split_text(text, 5)
        # Line is 11 chars, truncated to 5: "abcde"
        self.assertEqual(result, ["abcde"])

    def test_preserves_newlines(self) -> None:
        text = "line1\nline2\n"
        result = _split_text(text, 1000)
        self.assertEqual(result, ["line1\nline2\n"])

    def test_no_newlines(self) -> None:
        text = "abcdef"
        result = _split_text(text, 1000)
        self.assertEqual(result, ["abcdef"])

    def test_multi_split(self) -> None:
        # 5 lines of 6 chars each = 30 chars total
        lines = [f"line{i}\n" for i in range(1, 6)]
        text = "".join(lines)
        result = _split_text(text, 12)
        # line1\n(6) + line2\n(6) = 12 -> chunk 1
        # line3\n(6) + line4\n(6) = 12 -> chunk 2
        # line5\n(6) -> chunk 3
        self.assertEqual(len(result), 3)


# ── _query_terms ──


class QueryTermsTest(unittest.TestCase):
    def test_simple_words(self) -> None:
        result = _query_terms("pytest runner")
        self.assertEqual(result, ['"pytest"', '"runner"'])

    def test_removes_double_quotes(self) -> None:
        result = _query_terms('"exact phrase" search')
        self.assertEqual(result, ['"exact"', '"phrase"', '"search"'])

    def test_removes_special_chars(self) -> None:
        result = _query_terms("test.py:42")
        self.assertEqual(result, ['"testpy42"'])

    def test_preserves_alphanumeric_underscore_dash(self) -> None:
        result = _query_terms("my_key my-val")
        self.assertEqual(result, ['"my_key"', '"my-val"'])

    def test_max_8_terms(self) -> None:
        words = "a b c d e f g h i j"
        result = _query_terms(words)
        self.assertEqual(len(result), 8)

    def test_empty_query(self) -> None:
        result = _query_terms("")
        self.assertEqual(result, [])

    def test_whitespace_only(self) -> None:
        result = _query_terms("   ")
        self.assertEqual(result, [])

    def test_each_term_quoted(self) -> None:
        result = _query_terms("hello world")
        for term in result:
            self.assertTrue(term.startswith('"'))
            self.assertTrue(term.endswith('"'))


# ── _resolve_intent ──


class ResolveIntentTest(unittest.TestCase):
    # -- Alias resolution --

    def test_ci_alias(self) -> None:
        self.assertEqual(_resolve_intent("ci", ""), "verification")

    def test_test_alias(self) -> None:
        self.assertEqual(_resolve_intent("test", ""), "verification")

    def test_guidance_alias(self) -> None:
        self.assertEqual(_resolve_intent("guidance", ""), "repo_context")

    def test_error_alias(self) -> None:
        self.assertEqual(_resolve_intent("error", ""), "failure")

    def test_pr_alias(self) -> None:
        self.assertEqual(_resolve_intent("pr", ""), "external_write")

    def test_unknown_alias(self) -> None:
        self.assertEqual(_resolve_intent("unknown", ""), "unknown")

    def test_unrecognized_alias(self) -> None:
        # An intent not in INTENT_ALIASES falls through to query inspection
        self.assertEqual(_resolve_intent("mystery", "pytest results"), "verification")

    # -- Case-insensitive alias lookup --

    def test_case_insensitive_alias(self) -> None:
        self.assertEqual(_resolve_intent("CI", ""), "verification")

    def test_whitespace_trimmed(self) -> None:
        self.assertEqual(_resolve_intent("  test  ", ""), "verification")

    # -- Query-keyword fallback --

    def test_query_contains_test_keyword(self) -> None:
        self.assertEqual(_resolve_intent("mystery", "pytest results"), "verification")

    def test_query_contains_ci_keyword(self) -> None:
        self.assertEqual(_resolve_intent("mystery", "ci pipeline"), "verification")

    def test_query_contains_verify_keyword(self) -> None:
        self.assertEqual(_resolve_intent("mystery", "verify output"), "verification")

    def test_query_contains_guidance_keyword(self) -> None:
        self.assertEqual(_resolve_intent("mystery", "guidance docs"), "repo_context")

    def test_query_contains_contributing_keyword(self) -> None:
        self.assertEqual(_resolve_intent("mystery", "contributing guide"), "repo_context")

    def test_query_contains_fail_keyword(self) -> None:
        # Use a query that only triggers failure keywords, not verification
        self.assertEqual(_resolve_intent("mystery", "execution failed"), "failure")

    def test_query_contains_error_keyword(self) -> None:
        self.assertEqual(_resolve_intent("mystery", "runtime error"), "failure")

    def test_query_contains_pr_keyword(self) -> None:
        self.assertEqual(_resolve_intent("mystery", "pr review"), "external_write")

    def test_query_contains_fork_keyword(self) -> None:
        self.assertEqual(_resolve_intent("mystery", "fork repo"), "external_write")

    def test_unknown_intent_unknown_query(self) -> None:
        self.assertEqual(_resolve_intent("mystery", "random stuff"), "unknown")

    # -- Alias takes priority over query --

    def test_alias_overrides_query(self) -> None:
        # "test" alias resolves to "verification" even if query contains "pr"
        self.assertEqual(_resolve_intent("test", "pr review"), "verification")


# ── _include_record_type ──


class IncludeRecordTypeTest(unittest.TestCase):
    def test_trace_event_excluded_by_default(self) -> None:
        self.assertFalse(_include_record_type("trace_event", "verification"))

    def test_trace_event_excluded_from_repo_context(self) -> None:
        self.assertFalse(_include_record_type("trace_event", "repo_context"))

    def test_trace_event_excluded_from_external_write(self) -> None:
        self.assertFalse(_include_record_type("trace_event", "external_write"))

    def test_trace_event_included_for_failure(self) -> None:
        self.assertTrue(_include_record_type("trace_event", "failure"))

    def test_non_trace_type_always_included(self) -> None:
        self.assertTrue(_include_record_type("quality_report", "verification"))
        self.assertTrue(_include_record_type("artifact", "repo_context"))
        self.assertTrue(_include_record_type("tool_result", "external_write"))

    def test_unknown_type_included(self) -> None:
        self.assertTrue(_include_record_type("custom_type", "unknown"))


# ── _record_priority ──


class RecordPriorityTest(unittest.TestCase):
    def test_intent_specific_priority(self) -> None:
        # verification intent + verification record type = 100
        self.assertEqual(_record_priority("verification", "verification"), 100)

    def test_intent_specific_over_default(self) -> None:
        # verification intent + artifact = 35 (intent-specific, not 25 default)
        self.assertEqual(_record_priority("artifact", "verification"), 35)

    def test_default_priority_fallback(self) -> None:
        # unknown intent + quality_report = 95 (DEFAULT_PRIORITIES fallback)
        self.assertEqual(_record_priority("quality_report", "unknown"), 95)

    def test_unknown_type_unknown_intent(self) -> None:
        # unknown intent + unknown type = artifact default (25)
        self.assertEqual(_record_priority("totally_unknown", "unknown"), 25)

    def test_default_artifact_is_25(self) -> None:
        # The default for unknown types is the artifact priority (25)
        self.assertEqual(DEFAULT_PRIORITIES["artifact"], 25)


# ── _title_for_record ──


class TitleForRecordTest(unittest.TestCase):
    KNOWN_LABELS = {
        "quality_report": "Quality report",
        "postmortem": "Postmortem",
        "memory_event": "Memory event",
        "working_memory": "Working memory",
        "repo_guidance": "Repository guidance artifact",
        "memory_context": "Memory context",
        "verification": "Verification log",
        "quality_gate": "Quality gate",
        "governance_decision": "Governance decision",
        "ci_status": "CI status",
        "live_action": "Live action log",
        "review_event": "PR review log",
        "tool_result": "Tool result",
        "trace_event": "Trace event",
        "artifact": "Run artifact",
    }

    def test_all_known_types(self) -> None:
        for record_type, label in self.KNOWN_LABELS.items():
            result = _title_for_record("some_file", record_type)
            self.assertEqual(result, f"{label}: some_file")

    def test_unknown_type_fallback(self) -> None:
        result = _title_for_record("output.txt", "unknown_type")
        self.assertEqual(result, "Run artifact: output.txt")


# ── _reason_for_record ──


class ReasonForRecordTest(unittest.TestCase):
    def test_verification_intent(self) -> None:
        result = _reason_for_record("verification", "verification")
        self.assertIn("matched verification query", result)
        self.assertIn("verification", result)

    def test_repo_context_intent(self) -> None:
        result = _reason_for_record("repo_guidance", "repo_context")
        self.assertIn("matched repository-context query", result)
        self.assertIn("repo_guidance", result)

    def test_failure_intent(self) -> None:
        result = _reason_for_record("postmortem", "failure")
        self.assertIn("matched failure query", result)
        self.assertIn("postmortem", result)

    def test_external_write_intent(self) -> None:
        result = _reason_for_record("live_action", "external_write")
        self.assertIn("matched external-write query", result)
        self.assertIn("live_action", result)

    def test_unknown_intent(self) -> None:
        result = _reason_for_record("artifact", "unknown")
        self.assertIn("matched query", result)
        self.assertIn("artifact", result)


# ── _snippet ──


class SnippetTest(unittest.TestCase):
    def test_short_text_preserved(self) -> None:
        text = "short"
        self.assertEqual(_snippet(text), "short")

    def test_exact_limit_preserved(self) -> None:
        text = "a" * 800
        self.assertEqual(_snippet(text), "a" * 800)

    def test_truncation_marker(self) -> None:
        text = "a" * 801
        result = _snippet(text)
        self.assertTrue(result.endswith("\n[history result truncated]"))
        self.assertEqual(len(result), 800 - 24 + len("\n[history result truncated]"))

    def test_leading_trailing_whitespace_stripped(self) -> None:
        text = "  hello  "
        self.assertEqual(_snippet(text), "hello")

    def test_empty_string(self) -> None:
        self.assertEqual(_snippet(""), "")

    def test_custom_limit(self) -> None:
        text = "a" * 200
        result = _snippet(text, limit=100)
        self.assertTrue(result.endswith("\n[history result truncated]"))


if __name__ == "__main__":
    unittest.main()
