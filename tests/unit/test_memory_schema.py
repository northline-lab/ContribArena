from __future__ import annotations

import unittest
from typing import get_args

from pydantic import ValidationError

from contribarena.memory.schema import (
    GuidanceContext,
    MemoryCapabilities,
    MemoryContext,
    MemoryEvent,
    MemoryHint,
    MemorySearchItem,
    MemorySearchResult,
    MemoryWriteReport,
    MemoryWriteResult,
    TrackedPullRequestContext,
    WorkingMemory,
    WorkingMemoryFact,
    WorkingMemoryNote,
    WorkingMemoryPlanItem,
)


class WorkingMemoryFactTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        fact = WorkingMemoryFact(
            key="test_key",
            value="test_value",
            source="agent",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(fact.key, "test_key")
        self.assertEqual(fact.value, "test_value")
        self.assertEqual(fact.source, "agent")
        self.assertEqual(fact.derived_from, "")

    def test_explicit_values(self) -> None:
        fact = WorkingMemoryFact(
            key="k",
            value="v",
            source="harness",
            derived_from="parent_key",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T01:00:00+00:00",
        )
        self.assertEqual(fact.source, "harness")
        self.assertEqual(fact.derived_from, "parent_key")

    def test_invalid_source_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            WorkingMemoryFact(
                key="k",
                value="v",
                source="invalid",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )

    def test_missing_required_field(self) -> None:
        with self.assertRaises(ValidationError):
            WorkingMemoryFact(key="k", value="v", source="agent")  # type: ignore[call-arg]


class WorkingMemoryPlanItemTest(unittest.TestCase):
    def test_required_fields_and_defaults(self) -> None:
        item = WorkingMemoryPlanItem(
            id="plan-1",
            text="Do something",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(item.id, "plan-1")
        self.assertEqual(item.status, "open")

    def test_all_status_literals(self) -> None:
        for status in get_args(WorkingMemoryPlanItem.model_fields["status"].annotation):
            item = WorkingMemoryPlanItem(
                id="plan-1",
                text="text",
                status=status,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
            self.assertEqual(item.status, status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            WorkingMemoryPlanItem(
                id="plan-1",
                text="text",
                status="invalid",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )


class WorkingMemoryNoteTest(unittest.TestCase):
    def test_required_fields_and_defaults(self) -> None:
        note = WorkingMemoryNote(
            id="note-1",
            text="Important observation",
            created_at="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(note.id, "note-1")
        self.assertEqual(note.tags, [])

    def test_explicit_tags(self) -> None:
        note = WorkingMemoryNote(
            id="note-1",
            text="text",
            tags=["tag1", "tag2"],
            created_at="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(note.tags, ["tag1", "tag2"])

    def test_tags_factory_independence(self) -> None:
        note1 = WorkingMemoryNote(id="1", text="a", created_at="2026-01-01T00:00:00+00:00")
        note2 = WorkingMemoryNote(id="2", text="b", created_at="2026-01-01T00:00:00+00:00")
        note1.tags.append("shared")
        self.assertEqual(note1.tags, ["shared"])
        self.assertEqual(note2.tags, [])


class GuidanceContextTest(unittest.TestCase):
    def test_defaults(self) -> None:
        gc = GuidanceContext()
        self.assertTrue(gc.available)
        self.assertEqual(gc.entry_path, ".contribarena/guidance/guidance_entry.md")
        self.assertEqual(gc.path_relative_to, "workspace_root")
        self.assertEqual(gc.skipped_reason, "")
        self.assertEqual(gc.error, "")

    def test_explicit_values(self) -> None:
        gc = GuidanceContext(
            available=False,
            skipped_reason="not_applicable",
            error="some error",
        )
        self.assertFalse(gc.available)
        self.assertEqual(gc.skipped_reason, "not_applicable")


class MemoryHintTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        hint = MemoryHint(
            category="repo_context",
            summary_line="Test summary",
            suggested_query="test query",
            suggested_intent="repo_context",
        )
        self.assertEqual(hint.category, "repo_context")

    def test_all_category_literals(self) -> None:
        for cat in get_args(MemoryHint.model_fields["category"].annotation):
            hint = MemoryHint(
                category=cat,
                summary_line="summary",
                suggested_query="query",
                suggested_intent=cat,
            )
            self.assertEqual(hint.category, cat)

    def test_invalid_category_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MemoryHint(
                category="invalid",
                summary_line="summary",
                suggested_query="query",
                suggested_intent="repo_context",
            )

    def test_summary_line_max_length(self) -> None:
        with self.assertRaises(ValidationError):
            MemoryHint(
                category="repo_context",
                summary_line="x" * 201,
                suggested_query="query",
                suggested_intent="repo_context",
            )


class MemoryCapabilitiesTest(unittest.TestCase):
    def test_defaults(self) -> None:
        caps = MemoryCapabilities()
        self.assertTrue(caps.run_scope_notes)
        self.assertFalse(caps.repo_scope_persistent)
        self.assertFalse(caps.global_scope_persistent)
        self.assertIn("event-log-only", caps.note)

    def test_explicit_values(self) -> None:
        caps = MemoryCapabilities(
            run_scope_notes=False,
            repo_scope_persistent=True,
            global_scope_persistent=True,
        )
        self.assertFalse(caps.run_scope_notes)
        self.assertTrue(caps.repo_scope_persistent)


class MemoryEventTest(unittest.TestCase):
    def test_required_fields_and_defaults(self) -> None:
        event = MemoryEvent(
            event_id="evt-1",
            event_type="test",
            run_id="run-1",
            created_at="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(event.event_id, "evt-1")
        self.assertEqual(event.repo_full_name, "")
        self.assertIsNone(event.pr_number)
        self.assertEqual(event.payload, {})
        self.assertEqual(event.confidence, "medium")
        self.assertTrue(event.redacted)

    def test_all_confidence_literals(self) -> None:
        for conf in get_args(MemoryEvent.model_fields["confidence"].annotation):
            event = MemoryEvent(
                event_id="evt-1",
                event_type="test",
                run_id="run-1",
                confidence=conf,
                created_at="2026-01-01T00:00:00+00:00",
            )
            self.assertEqual(event.confidence, conf)

    def test_invalid_confidence_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MemoryEvent(
                event_id="evt-1",
                event_type="test",
                run_id="run-1",
                confidence="invalid",
                created_at="2026-01-01T00:00:00+00:00",
            )

    def test_payload_factory_independence(self) -> None:
        event1 = MemoryEvent(
            event_id="1", event_type="t", run_id="r", created_at="2026-01-01T00:00:00+00:00"
        )
        event2 = MemoryEvent(
            event_id="2", event_type="t", run_id="r", created_at="2026-01-01T00:00:00+00:00"
        )
        event1.payload["key"] = "value"
        self.assertNotIn("key", event2.payload)


class MemoryWriteResultTest(unittest.TestCase):
    def test_required_field_and_defaults(self) -> None:
        result = MemoryWriteResult(success=True)
        self.assertTrue(result.success)
        self.assertEqual(result.event_ids, [])
        self.assertEqual(result.graphiti_episode_ids, [])
        self.assertFalse(result.history_index_updated)
        self.assertFalse(result.degraded)
        self.assertEqual(result.skipped_reason, "")

    def test_explicit_values(self) -> None:
        result = MemoryWriteResult(
            success=False,
            event_ids=["evt-1", "evt-2"],
            degraded=True,
            error_kind="timeout",
            error_message="Operation timed out",
        )
        self.assertFalse(result.success)
        self.assertEqual(len(result.event_ids), 2)
        self.assertTrue(result.degraded)


class MemorySearchItemTest(unittest.TestCase):
    def test_required_fields_and_defaults(self) -> None:
        item = MemorySearchItem(
            text="found text",
            source="history_index",
        )
        self.assertEqual(item.text, "found text")
        self.assertEqual(item.source, "history_index")
        self.assertEqual(item.score, 0.0)
        self.assertEqual(item.confidence, "medium")

    def test_all_source_literals(self) -> None:
        for src in get_args(MemorySearchItem.model_fields["source"].annotation):
            item = MemorySearchItem(text="text", source=src)
            self.assertEqual(item.source, src)

    def test_invalid_source_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MemorySearchItem(text="text", source="invalid")


class MemorySearchResultTest(unittest.TestCase):
    def test_required_fields_and_defaults(self) -> None:
        result = MemorySearchResult(
            success=True,
            intent="repo_context",
            query="test query",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.results, [])
        self.assertFalse(result.degraded)

    def test_results_factory_independence(self) -> None:
        r1 = MemorySearchResult(success=True, intent="i", query="q")
        r2 = MemorySearchResult(success=True, intent="i", query="q")
        r1.results.append(MemorySearchItem(text="t", source="history_index"))
        self.assertEqual(len(r1.results), 1)
        self.assertEqual(len(r2.results), 0)


class TrackedPullRequestContextTest(unittest.TestCase):
    def test_required_fields_and_defaults(self) -> None:
        pr = TrackedPullRequestContext(
            repository="owner/repo",
            number=123,
        )
        self.assertEqual(pr.repository, "owner/repo")
        self.assertEqual(pr.number, 123)
        self.assertEqual(pr.state, "open")
        self.assertEqual(pr.lifecycle_status, "tracking")
        self.assertEqual(pr.ci_status, "not_run")

    def test_all_state_literals(self) -> None:
        for state in get_args(TrackedPullRequestContext.model_fields["state"].annotation):
            pr = TrackedPullRequestContext(repository="r", number=1, state=state)
            self.assertEqual(pr.state, state)

    def test_invalid_state_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TrackedPullRequestContext(repository="r", number=1, state="invalid")

    def test_detail_queries_factory_independence(self) -> None:
        pr1 = TrackedPullRequestContext(repository="r", number=1)
        pr2 = TrackedPullRequestContext(repository="r", number=2)
        hint = MemoryHint(
            category="external_write",
            summary_line="test",
            suggested_query="q",
            suggested_intent="external_write",
        )
        pr1.detail_queries.append(hint)
        self.assertEqual(len(pr1.detail_queries), 1)
        self.assertEqual(len(pr2.detail_queries), 0)


class MemoryWriteReportTest(unittest.TestCase):
    def test_required_fields_and_defaults(self) -> None:
        report = MemoryWriteReport(
            graphiti_enabled=True,
            graphiti_available=True,
            events_written=5,
        )
        self.assertTrue(report.graphiti_enabled)
        self.assertEqual(report.events_written, 5)
        self.assertEqual(report.graphiti_episodes_written, 0)
        self.assertFalse(report.degraded)
        self.assertEqual(report.failures, [])

    def test_failures_factory_independence(self) -> None:
        r1 = MemoryWriteReport(graphiti_enabled=False, graphiti_available=False, events_written=0)
        r2 = MemoryWriteReport(graphiti_enabled=False, graphiti_available=False, events_written=0)
        r1.failures.append({"error": "test"})
        self.assertEqual(len(r1.failures), 1)
        self.assertEqual(len(r2.failures), 0)


class WorkingMemoryTest(unittest.TestCase):
    def test_required_fields_and_defaults(self) -> None:
        wm = WorkingMemory(run_id="run-1")
        self.assertEqual(wm.run_id, "run-1")
        self.assertEqual(wm.repo_full_name, "")
        self.assertIsInstance(wm.guidance, GuidanceContext)
        self.assertEqual(wm.memory_hints, [])
        self.assertEqual(wm.facts, {})
        self.assertFalse(wm.truncated)

    def test_nested_composition(self) -> None:
        hint = MemoryHint(
            category="repo_context",
            summary_line="test",
            suggested_query="q",
            suggested_intent="repo_context",
        )
        wm = WorkingMemory(
            run_id="run-1",
            repo_full_name="owner/repo",
            memory_hints=[hint],
        )
        self.assertEqual(len(wm.memory_hints), 1)
        self.assertEqual(wm.memory_hints[0].category, "repo_context")

    def test_facts_factory_independence(self) -> None:
        wm1 = WorkingMemory(run_id="1")
        wm2 = WorkingMemory(run_id="2")
        wm1.facts["key"] = WorkingMemoryFact(
            key="key", value="v", source="agent", created_at="t", updated_at="t"
        )
        self.assertNotIn("key", wm2.facts)


class MemoryContextTest(unittest.TestCase):
    def test_required_fields_and_defaults(self) -> None:
        ctx = MemoryContext(run_id="run-1")
        self.assertEqual(ctx.run_id, "run-1")
        self.assertEqual(ctx.snapshot_phase, "run_start_context")
        self.assertTrue(ctx.enabled)
        self.assertFalse(ctx.degraded)
        self.assertEqual(ctx.backend, "noop")
        self.assertEqual(ctx.history_results, [])
        self.assertEqual(ctx.notes, [])

    def test_notes_factory_independence(self) -> None:
        c1 = MemoryContext(run_id="1")
        c2 = MemoryContext(run_id="2")
        c1.notes.append("note")
        self.assertEqual(len(c1.notes), 1)
        self.assertEqual(len(c2.notes), 0)


if __name__ == "__main__":
    unittest.main()
