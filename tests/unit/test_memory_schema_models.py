from __future__ import annotations

import unittest
from typing import Any

from pydantic import BaseModel, ValidationError

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


def assert_rejects_literal(test: unittest.TestCase, model: type[BaseModel], **kwargs: Any) -> None:
    with test.assertRaises(ValidationError):
        model(**kwargs)


def memory_hint(**overrides: Any) -> MemoryHint:
    values: dict[str, Any] = {
        "category": "verification",
        "summary_line": "pytest passed",
        "suggested_query": "verification pytest",
        "suggested_intent": "verification",
    }
    values.update(overrides)
    return MemoryHint(**values)


def memory_hint_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "category": "verification",
        "summary_line": "pytest passed",
        "suggested_query": "verification pytest",
        "suggested_intent": "verification",
    }
    values.update(overrides)
    return values


class WorkingMemoryPrimitiveModelsTest(unittest.TestCase):
    def test_fact_requires_supported_source_and_defaults_derived_from(self) -> None:
        fact = WorkingMemoryFact(
            key="repo",
            value="qWaitCrypto/ContribArena",
            source="agent",
            created_at="created",
            updated_at="updated",
        )

        self.assertEqual("repo", fact.key)
        self.assertEqual("qWaitCrypto/ContribArena", fact.value)
        self.assertEqual("agent", fact.source)
        self.assertEqual("", fact.derived_from)

        harness_fact = WorkingMemoryFact(
            key="phase",
            value="work",
            source="harness",
            created_at="created",
            updated_at="updated",
        )
        self.assertEqual("harness", harness_fact.source)
        assert_rejects_literal(
            self,
            WorkingMemoryFact,
            key="repo",
            value="value",
            source="operator",
            created_at="created",
            updated_at="updated",
        )

    def test_plan_item_status_literal_and_default(self) -> None:
        self.assertEqual(
            "open",
            WorkingMemoryPlanItem(
                id="plan-1",
                text="Inspect guidance",
                created_at="created",
                updated_at="updated",
            ).status,
        )

        for status in ("open", "doing", "done", "dropped"):
            with self.subTest(status=status):
                item = WorkingMemoryPlanItem(
                    id="plan-1",
                    text="task",
                    status=status,
                    created_at="created",
                    updated_at="updated",
                )
                self.assertEqual(status, item.status)

        assert_rejects_literal(
            self,
            WorkingMemoryPlanItem,
            id="plan-1",
            text="task",
            status="blocked",
            created_at="created",
            updated_at="updated",
        )

    def test_note_tags_default_factory_is_independent(self) -> None:
        first = WorkingMemoryNote(id="note-1", text="first", created_at="created")
        second = WorkingMemoryNote(id="note-2", text="second", created_at="created")

        first.tags.append("repo_context")

        self.assertEqual(["repo_context"], first.tags)
        self.assertEqual([], second.tags)


class MemoryContextSupportModelsTest(unittest.TestCase):
    def test_guidance_defaults_are_workspace_root_sidecar_paths(self) -> None:
        guidance = GuidanceContext()

        self.assertTrue(guidance.available)
        self.assertEqual(".contribarena/guidance/guidance_entry.md", guidance.entry_path)
        self.assertEqual(".contribarena/guidance/guidance_manifest.json", guidance.manifest_path)
        self.assertEqual("workspace_root", guidance.path_relative_to)
        self.assertEqual("", guidance.skipped_reason)
        self.assertEqual("", guidance.error)
        assert_rejects_literal(self, GuidanceContext, path_relative_to="repo")

    def test_memory_hint_limits_category_intent_and_summary_lengths(self) -> None:
        for value in ("repo_context", "verification", "failure", "external_write"):
            with self.subTest(value=value):
                hint = memory_hint(
                    category=value,
                    summary_line=f"{value} summary",
                    suggested_query=f"{value} query",
                    suggested_intent=value,
                )
                self.assertEqual(value, hint.category)
                self.assertEqual(value, hint.suggested_intent)

        text = "x" * 200
        self.assertEqual(text, memory_hint(summary_line=text, suggested_query=text).summary_line)
        assert_rejects_literal(self, MemoryHint, **memory_hint_values(summary_line="x" * 201))
        assert_rejects_literal(self, MemoryHint, **memory_hint_values(suggested_query="x" * 201))
        assert_rejects_literal(self, MemoryHint, **memory_hint_values(category="planning"))
        assert_rejects_literal(self, MemoryHint, **memory_hint_values(suggested_intent="planning"))

    def test_capability_defaults_describe_run_local_memory(self) -> None:
        capabilities = MemoryCapabilities()

        self.assertTrue(capabilities.run_scope_notes)
        self.assertFalse(capabilities.repo_scope_persistent)
        self.assertFalse(capabilities.global_scope_persistent)
        self.assertIn("scope='repo'", capabilities.note)


class WorkingMemoryContainerModelsTest(unittest.TestCase):
    def test_working_memory_minimal_defaults_and_independent_collections(self) -> None:
        first = WorkingMemory(run_id="run-1")
        second = WorkingMemory(run_id="run-2")

        self.assertEqual("1", first.schema_version)
        self.assertEqual("run-1", first.run_id)
        self.assertEqual("", first.repo_full_name)
        self.assertIsInstance(first.guidance, GuidanceContext)
        self.assertIsInstance(first.memory_capabilities, MemoryCapabilities)
        self.assertEqual("scout", first.goals.current_phase)
        self.assertFalse(first.truncated)

        first.memory_hints.append(memory_hint())
        first.facts["repo"] = WorkingMemoryFact(
            key="repo",
            value="qWaitCrypto/ContribArena",
            source="agent",
            created_at="created",
            updated_at="updated",
        )
        first.plan.append(
            WorkingMemoryPlanItem(
                id="plan-1", text="Run tests", created_at="created", updated_at="updated"
            )
        )
        first.notes.append(WorkingMemoryNote(id="note-1", text="note", created_at="created"))

        self.assertEqual(1, len(first.memory_hints))
        self.assertEqual(1, len(first.facts))
        self.assertEqual(1, len(first.plan))
        self.assertEqual(1, len(first.notes))
        self.assertEqual([], second.memory_hints)
        self.assertEqual({}, second.facts)
        self.assertEqual([], second.plan)
        self.assertEqual([], second.notes)

    def test_memory_context_snapshot_defaults_and_independent_collections(self) -> None:
        first = MemoryContext(run_id="run-1")
        second = MemoryContext(run_id="run-2")

        self.assertEqual("1", first.schema_version)
        self.assertEqual("run_start_context", first.snapshot_phase)
        self.assertIn("run context is prepared", first.snapshot_note)
        self.assertEqual("noop", first.backend)
        self.assertTrue(first.enabled)
        self.assertFalse(first.degraded)

        first.history_results.append(MemorySearchItem(text="prior verification", source="history_index"))
        first.notes.append("remember this")

        self.assertEqual(1, len(first.history_results))
        self.assertEqual(["remember this"], first.notes)
        self.assertEqual([], second.history_results)
        self.assertEqual([], second.notes)


class MemoryEventAndToolResultModelsTest(unittest.TestCase):
    def test_memory_event_defaults_confidence_literal_and_payload_factory(self) -> None:
        first = MemoryEvent(
            event_id="event-1", event_type="memory_note", run_id="run-1", created_at="created"
        )
        second = MemoryEvent(
            event_id="event-2", event_type="memory_note", run_id="run-1", created_at="created"
        )

        self.assertEqual("1", first.schema_version)
        self.assertEqual("", first.repo_full_name)
        self.assertIsNone(first.pr_number)
        self.assertEqual("medium", first.confidence)
        self.assertTrue(first.redacted)

        for confidence in ("low", "medium", "high"):
            with self.subTest(confidence=confidence):
                event = MemoryEvent(
                    event_id="event-3",
                    event_type="memory_note",
                    run_id="run-1",
                    confidence=confidence,
                    created_at="created",
                )
                self.assertEqual(confidence, event.confidence)

        assert_rejects_literal(
            self,
            MemoryEvent,
            event_id="event-4",
            event_type="memory_note",
            run_id="run-1",
            confidence="certain",
            created_at="created",
        )
        first.payload["note"] = "keep"
        self.assertEqual({"note": "keep"}, first.payload)
        self.assertEqual({}, second.payload)

    def test_memory_write_result_list_defaults_are_independent(self) -> None:
        first = MemoryWriteResult(success=True)
        second = MemoryWriteResult(success=True)

        self.assertTrue(first.success)
        self.assertFalse(first.history_index_updated)
        self.assertFalse(first.degraded)
        first.event_ids.append("event-1")
        first.graphiti_episode_ids.append("episode-1")

        self.assertEqual(["event-1"], first.event_ids)
        self.assertEqual(["episode-1"], first.graphiti_episode_ids)
        self.assertEqual([], second.event_ids)
        self.assertEqual([], second.graphiti_episode_ids)

    def test_memory_search_result_defaults_and_item_source_literal(self) -> None:
        result = MemorySearchResult(success=True, intent="verification", query="pytest")

        self.assertEqual("verification", result.intent)
        self.assertEqual("pytest", result.query)
        self.assertEqual("", result.repo_full_name)
        self.assertFalse(result.degraded)

        for source in ("graphiti", "history_index", "working_memory"):
            with self.subTest(source=source):
                item = MemorySearchItem(text="result text", source=source)
                self.assertEqual(source, item.source)
                self.assertEqual("medium", item.confidence)

        assert_rejects_literal(self, MemorySearchItem, text="result text", source="filesystem")
        result.results.append(MemorySearchItem(text="result", source="working_memory"))
        self.assertEqual(1, len(result.results))
        self.assertEqual([], MemorySearchResult(success=True, intent="verification", query="pytest").results)


class TrackedPullRequestAndReportModelsTest(unittest.TestCase):
    def test_tracked_pull_request_defaults_state_literal_and_detail_query_factory(self) -> None:
        first = TrackedPullRequestContext(repository="qWaitCrypto/ContribArena", number=93)
        second = TrackedPullRequestContext(repository="qWaitCrypto/ContribArena", number=94)

        self.assertEqual("1", first.schema_version)
        self.assertEqual("open", first.state)
        self.assertEqual("tracking", first.lifecycle_status)
        self.assertEqual("not_run", first.ci_status)

        for state in ("open", "closed", "merged"):
            with self.subTest(state=state):
                pr = TrackedPullRequestContext(
                    repository="qWaitCrypto/ContribArena", number=93, state=state
                )
                self.assertEqual(state, pr.state)

        assert_rejects_literal(
            self, TrackedPullRequestContext, repository="qWaitCrypto/ContribArena", number=93, state="draft"
        )
        first.detail_queries.append(memory_hint(category="external_write", suggested_intent="external_write"))
        self.assertEqual(1, len(first.detail_queries))
        self.assertEqual([], second.detail_queries)

    def test_memory_write_report_defaults_and_list_factories(self) -> None:
        first = MemoryWriteReport(graphiti_enabled=True, graphiti_available=False, events_written=2)
        second = MemoryWriteReport(graphiti_enabled=True, graphiti_available=False, events_written=2)

        self.assertTrue(first.graphiti_enabled)
        self.assertFalse(first.graphiti_available)
        self.assertEqual(2, first.events_written)
        self.assertEqual(0, first.graphiti_episodes_written)
        self.assertEqual(0, first.memory_searches)
        self.assertEqual(0, first.history_index_entries_written)
        self.assertFalse(first.degraded)

        first.failures.append({"kind": "graphiti_unavailable"})
        first.indexed_sources.append("memory_events.jsonl")

        self.assertEqual([{"kind": "graphiti_unavailable"}], first.failures)
        self.assertEqual(["memory_events.jsonl"], first.indexed_sources)
        self.assertEqual([], second.failures)
        self.assertEqual([], second.indexed_sources)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
