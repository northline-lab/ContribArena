from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from contribarena.config.schema import MemoryConfig
from contribarena.memory.graphiti_backend import GraphitiSearchResult, GraphitiWriteResult
from contribarena.memory.history_index import HistoryIndex
from contribarena.memory.schema import MemorySearchItem
from contribarena.memory.service import MemoryService
from contribarena.models import PrLifecycleRecord


class FakeGraphitiBackend:
    def __init__(
        self,
        *,
        write_result: GraphitiWriteResult | None = None,
        search_result: GraphitiSearchResult | None = None,
    ) -> None:
        self.write_result = write_result or GraphitiWriteResult(success=True, episode_ids=["episode-1"])
        self.search_result = search_result or GraphitiSearchResult(
            success=True,
            results=[
                MemorySearchItem(
                    text="Repo convention: use compileall before submitting.",
                    source="graphiti",
                    source_ref="episode-1",
                    record_type="repo_memory",
                )
            ],
        )
        self.episodes: list[dict[str, Any]] = []
        self.searches: list[dict[str, Any]] = []

    def add_repo_episode(
        self,
        *,
        repo_full_name: str,
        run_id: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        source_ref: str,
        confidence: str,
    ) -> GraphitiWriteResult:
        self.episodes.append(
            {
                "repo_full_name": repo_full_name,
                "run_id": run_id,
                "event_id": event_id,
                "event_type": event_type,
                "payload": payload,
                "source_ref": source_ref,
                "confidence": confidence,
            }
        )
        return self.write_result

    def search_repo(
        self,
        *,
        repo_full_name: str,
        query: str,
        limit: int,
    ) -> GraphitiSearchResult:
        self.searches.append({"repo_full_name": repo_full_name, "query": query, "limit": limit})
        return self.search_result


class MemoryBackedFakeGraphitiBackend(FakeGraphitiBackend):
    def __init__(self) -> None:
        super().__init__(search_result=GraphitiSearchResult(success=True, results=[]))

    def search_repo(
        self,
        *,
        repo_full_name: str,
        query: str,
        limit: int,
    ) -> GraphitiSearchResult:
        self.searches.append({"repo_full_name": repo_full_name, "query": query, "limit": limit})
        query_terms = {term.lower() for term in query.split()}
        results: list[MemorySearchItem] = []
        for episode in self.episodes:
            payload = episode["payload"]
            text = str(payload.get("text") or payload)
            if not query_terms or any(term in text.lower() for term in query_terms):
                results.append(
                    MemorySearchItem(
                        text=text,
                        source="graphiti",
                        source_ref=episode["event_id"],
                        record_type="repo_memory",
                    )
                )
        return GraphitiSearchResult(success=True, results=results[:limit])


class MemoryServiceTest(unittest.TestCase):
    def test_derives_contributing_fact_and_agent_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = MemoryService(
                MemoryConfig(root=Path(tmp) / "memory"),
                run_id="run-1",
                repo_full_name="example/repo",
            )

            service.record_tool_observation(
                "1",
                "aci_view",
                {"path": "repo/CONTRIBUTING.md"},
                {"success": True, "output": "guidance"},
            )
            service.note_agent_memory(
                "run",
                "Project uses pytest with a token=ghs_secretsecretsecretsecret.",
                ["testing"],
                "medium",
                "unit-test",
            )

            self.assertEqual("repo/CONTRIBUTING.md", service.working.facts["contributing_checked"].value)
            self.assertEqual("harness", service.working.facts["contributing_checked"].source)
            self.assertIn("testing", service.working.facts)
            self.assertNotIn("ghs_secret", service.working.facts["testing"].value)

    def test_run_context_exposes_guidance_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = MemoryService(
                MemoryConfig(root=Path(tmp) / "memory"),
                run_id="run-1",
                repo_full_name="example/repo",
            )

            context = service.start_run_context("example/repo")

            self.assertTrue(context.guidance.available)
            self.assertEqual(
                ".contribarena/guidance/guidance_entry.md",
                context.guidance.entry_path,
            )
            self.assertEqual("workspace_root", context.guidance.path_relative_to)
            self.assertEqual(
                ".contribarena/guidance/guidance_entry.md",
                service.working.guidance.entry_path,
            )

    def test_guidance_status_marks_context_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = MemoryService(
                MemoryConfig(root=Path(tmp) / "memory"),
                run_id="run-1",
                repo_full_name="example/repo",
            )

            service.set_guidance_status(
                available=False,
                skipped_reason="guidance_disabled",
                error="",
            )

            self.assertFalse(service.context.guidance.available)
            self.assertEqual("guidance_disabled", service.context.guidance.skipped_reason)
            self.assertFalse(service.working.guidance.available)

    def test_run_context_exposes_compact_memory_hints_not_raw_history_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prior_run = root / "prior"
            prior_run.mkdir()
            (prior_run / "repo_guidance.json").write_text(
                '{"summary": "SECRET RAW CONTRIBUTING guidance verification details"}\n',
                encoding="utf-8",
            )
            (prior_run / "postmortem.md").write_text(
                "SECRET RAW failure recovery guidance verification details\n",
                encoding="utf-8",
            )
            HistoryIndex(root / "memory").index_run_dir(
                prior_run,
                run_id="prior-run",
                repo_full_name="example/repo",
            )
            service = MemoryService(
                MemoryConfig(root=root / "memory"),
                run_id="run-1",
                repo_full_name="example/repo",
            )

            context = service.start_run_context("example/repo")

            self.assertEqual(["repo_context", "failure"], [hint.category for hint in context.memory_hints])
            self.assertEqual(context.memory_hints, service.working.memory_hints)
            self.assertLessEqual(len(context.memory_hints), 5)
            for hint in context.memory_hints:
                self.assertLessEqual(len(hint.summary_line), 200)
                self.assertLessEqual(len(hint.suggested_query), 200)
                self.assertNotIn("SECRET RAW", hint.summary_line)
                self.assertNotIn("SECRET RAW", hint.suggested_query)
            self.assertFalse(service.working.memory_capabilities.repo_scope_persistent)
            self.assertFalse(service.working.memory_capabilities.global_scope_persistent)
            self.assertIn("event-log-only", service.working.memory_capabilities.note)

    def test_history_index_indexes_text_artifacts_with_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "postmortem.md").write_text(
                "Provider failure with token=ghs_secretsecretsecretsecret.\n",
                encoding="utf-8",
            )
            (run_dir / "binary.bin").write_bytes(b"\x00\x01")
            index = HistoryIndex(root / "memory")

            result = index.index_run_dir(run_dir, run_id="run-1", repo_full_name="example/repo")
            hits = index.search("Provider failure", repo_full_name="example/repo")

            self.assertEqual(1, result.entries_written)
            self.assertEqual(["postmortem.md"], result.indexed_sources)
            self.assertEqual(1, len(hits))
            self.assertEqual("history_index", hits[0].source)
            self.assertNotIn("ghs_secret", hits[0].text)
            self.assertIn("token=***", hits[0].text)

    def test_history_index_prioritizes_high_signal_verification_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "quality_report.md").write_text(
                "compileall verification passed for app.py\n",
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text(
                '{"event": "step", "message": "compileall noisy trace event"}\n',
                encoding="utf-8",
            )
            index = HistoryIndex(root / "memory")
            index.index_run_dir(run_dir, run_id="run-1", repo_full_name="example/repo")

            hits = index.search(
                "compileall verification",
                intent="verification",
                repo_full_name="example/repo",
            )

            self.assertEqual(1, len(hits))
            self.assertEqual("quality_report", hits[0].record_type)
            self.assertEqual("Quality report: quality_report.md", hits[0].title)
            self.assertIn("verification query", hits[0].reason)

    def test_history_index_routes_guidance_queries_to_guidance_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "repo_guidance.json").write_text(
                '{"summary": "CONTRIBUTING guidance requires small patches"}\n',
                encoding="utf-8",
            )
            (run_dir / "working_memory.json").write_text(
                '{"facts": {"guidance": "CONTRIBUTING guidance was read"}}\n',
                encoding="utf-8",
            )
            (run_dir / "quality_report.md").write_text(
                "CONTRIBUTING guidance mentioned in final quality report\n",
                encoding="utf-8",
            )
            index = HistoryIndex(root / "memory")
            index.index_run_dir(run_dir, run_id="run-1", repo_full_name="example/repo")

            hits = index.search(
                "CONTRIBUTING guidance",
                intent="repo_context",
                repo_full_name="example/repo",
                limit=2,
            )

            self.assertEqual(["repo_guidance", "working_memory"], [hit.record_type for hit in hits])
            self.assertIn("repository-context query", hits[0].reason)

    def test_history_index_routes_failure_queries_to_postmortem_before_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "postmortem.md").write_text(
                "provider failure happened after retry exhausted\n",
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text(
                '{"event": "provider failure", "message": "retry exhausted"}\n',
                encoding="utf-8",
            )
            index = HistoryIndex(root / "memory")
            index.index_run_dir(run_dir, run_id="run-1", repo_full_name="example/repo")

            hits = index.search(
                "provider failure retry",
                intent="failure",
                repo_full_name="example/repo",
                limit=2,
            )

            self.assertEqual(["postmortem", "trace_event"], [hit.record_type for hit in hits])

    def test_history_index_returns_empty_for_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "quality_report.md").write_text("compileall passed\n", encoding="utf-8")
            index = HistoryIndex(root / "memory")
            index.index_run_dir(run_dir, run_id="run-1", repo_full_name="example/repo")

            hits = index.search("nonexistent-term", intent="verification", repo_full_name="example/repo")

            self.assertEqual([], hits)

    def test_repo_scope_note_reports_event_log_only_degraded_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = MemoryService(
                MemoryConfig(root=Path(tmp) / "memory"),
                run_id="run-1",
                repo_full_name="example/repo",
            )

            result = service.note_agent_memory(
                "repo",
                "Future runs should remember this once L2 exists.",
                ["repo"],
                "medium",
                "unit-test",
            )

            self.assertTrue(result.success)
            self.assertTrue(result.degraded)
            self.assertEqual("graphiti_disabled", result.error_kind)
            self.assertNotIn("repo", service.working.facts)
            self.assertIn("agent_lesson_proposed", service.events_text())

    def test_repo_scope_note_writes_graphiti_episode_and_local_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            fake = FakeGraphitiBackend()
            service = MemoryService(
                MemoryConfig(
                    root=root / "memory",
                    backend="graphiti",
                    graphiti_enabled=True,
                ),
                run_id="run-1",
                repo_full_name="example/repo",
                graphiti_backend=fake,
            )

            result = service.note_agent_memory(
                "repo",
                "Remember repo convention token=ghs_secretsecretsecretsecret.",
                ["repo", "verification"],
                "high",
                "unit-test",
            )
            report = service.finalize_run({"terminal": "completed"}, run_dir)

            self.assertTrue(result.success)
            self.assertFalse(result.degraded)
            self.assertEqual(["episode-1"], result.graphiti_episode_ids)
            self.assertEqual(1, len(fake.episodes))
            self.assertEqual("example/repo", fake.episodes[0]["repo_full_name"])
            self.assertEqual("run-1", fake.episodes[0]["run_id"])
            self.assertEqual("agent_lesson_proposed", fake.episodes[0]["event_type"])
            self.assertNotIn("ghs_secret", fake.episodes[0]["payload"]["text"])
            self.assertIn("agent_lesson_proposed", service.events_text())
            self.assertTrue(report.graphiti_enabled)
            self.assertTrue(report.graphiti_available)
            self.assertEqual(1, report.graphiti_episodes_written)
            self.assertTrue(service.working.memory_capabilities.repo_scope_persistent)

    def test_harness_repo_fact_writes_event_log_and_graphiti_episode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            fake = FakeGraphitiBackend()
            service = MemoryService(
                MemoryConfig(
                    root=root / "memory",
                    backend="graphiti",
                    graphiti_enabled=True,
                ),
                run_id="run-1",
                repo_full_name="example/repo",
                graphiti_backend=fake,
            )

            result = service.record_tool_observation(
                "1",
                "aci_view",
                {"path": "repo/CONTRIBUTING.md"},
                {"success": True, "output": "follow contribution rules"},
            )
            report = service.finalize_run({"terminal": "completed"}, run_dir)

            self.assertTrue(result.success)
            self.assertEqual("repo/CONTRIBUTING.md", service.working.facts["contributing_checked"].value)
            self.assertIn("harness_repo_fact_observed", service.events_text())
            self.assertEqual(1, len(fake.episodes))
            self.assertEqual("harness_repo_fact_observed", fake.episodes[0]["event_type"])
            self.assertEqual(
                {"contributing_checked": "repo/CONTRIBUTING.md"},
                fake.episodes[0]["payload"]["facts"],
            )
            self.assertIn("Harness observed repository facts", fake.episodes[0]["payload"]["text"])
            self.assertEqual(1, report.graphiti_episodes_written)

    def test_repo_context_search_merges_graphiti_and_history_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prior_run = root / "prior"
            prior_run.mkdir()
            (prior_run / "repo_guidance.json").write_text(
                '{"summary": "History says CONTRIBUTING requires focused tests"}\n',
                encoding="utf-8",
            )
            HistoryIndex(root / "memory").index_run_dir(
                prior_run,
                run_id="prior-run",
                repo_full_name="example/repo",
            )
            fake = FakeGraphitiBackend()
            service = MemoryService(
                MemoryConfig(
                    root=root / "memory",
                    backend="graphiti",
                    graphiti_enabled=True,
                ),
                run_id="run-1",
                repo_full_name="example/repo",
                graphiti_backend=fake,
            )

            result = service.search_for_agent(
                "CONTRIBUTING compileall tests",
                intent="repo_context",
                max_results=5,
            )

            self.assertTrue(result.success)
            self.assertFalse(result.degraded)
            self.assertIn("graphiti", [item.source for item in result.results])
            self.assertIn("history_index", [item.source for item in result.results])
            self.assertEqual("example/repo", fake.searches[0]["repo_full_name"])

    def test_prior_repo_memory_surfaces_in_next_run_context_hint_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_root = root / "memory"
            prior_run_dir = root / "prior-run"
            prior_run_dir.mkdir()
            (prior_run_dir / "repo_guidance.json").write_text(
                '{"summary": "History says CONTRIBUTING requires focused tests"}\n',
                encoding="utf-8",
            )
            fake = MemoryBackedFakeGraphitiBackend()
            prior = MemoryService(
                MemoryConfig(
                    root=memory_root,
                    backend="graphiti",
                    graphiti_enabled=True,
                ),
                run_id="run-1",
                repo_full_name="example/repo",
                graphiti_backend=fake,
            )
            prior.note_agent_memory(
                "repo",
                "Repo convention: inspect CONTRIBUTING and run compileall before submit.",
                ["repo_context", "verification"],
                "high",
                "simulation-prior",
            )
            prior.finalize_run({"terminal": "completed"}, prior_run_dir)

            current = MemoryService(
                MemoryConfig(
                    root=memory_root,
                    backend="graphiti",
                    graphiti_enabled=True,
                ),
                run_id="run-2",
                repo_full_name="example/repo",
                graphiti_backend=fake,
            )
            context = current.start_run_context("example/repo")
            search = current.search_for_agent(
                "CONTRIBUTING compileall",
                intent="repo_context",
                max_results=5,
            )
            current_run_dir = root / "current-run"
            current_run_dir.mkdir()
            report = current.finalize_run({"terminal": "completed"}, current_run_dir)

            self.assertIn("repo_context", [hint.category for hint in context.memory_hints])
            self.assertIn("graphiti", [item.source for item in context.history_results])
            self.assertIn("graphiti", [item.source for item in search.results])
            self.assertIn("history_index", [item.source for item in search.results])
            self.assertGreaterEqual(report.memory_searches, 1)
            self.assertGreaterEqual(report.graphiti_searches, 2)
            self.assertGreaterEqual(report.history_index_searches, 2)

    def test_graphiti_search_failure_falls_back_to_history_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prior_run = root / "prior"
            prior_run.mkdir()
            (prior_run / "repo_guidance.json").write_text(
                '{"summary": "History fallback says pytest is unavailable"}\n',
                encoding="utf-8",
            )
            HistoryIndex(root / "memory").index_run_dir(
                prior_run,
                run_id="prior-run",
                repo_full_name="example/repo",
            )
            fake = FakeGraphitiBackend(
                search_result=GraphitiSearchResult(
                    success=False,
                    degraded=True,
                    error_kind="graphiti_unavailable",
                    error_message="FalkorDB unavailable",
                )
            )
            service = MemoryService(
                MemoryConfig(
                    root=root / "memory",
                    backend="graphiti",
                    graphiti_enabled=True,
                ),
                run_id="run-1",
                repo_full_name="example/repo",
                graphiti_backend=fake,
            )

            result = service.search_for_agent(
                "pytest unavailable",
                intent="repo_context",
                max_results=5,
            )

            self.assertTrue(result.success)
            self.assertTrue(result.degraded)
            self.assertEqual("graphiti_unavailable", result.error_kind)
            self.assertEqual(["history_index"], [item.source for item in result.results])

    def test_graphiti_write_failure_keeps_repo_note_fail_soft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            fake = FakeGraphitiBackend(
                write_result=GraphitiWriteResult(
                    success=False,
                    degraded=True,
                    error_kind="graphiti_unavailable",
                    error_message="FalkorDB unavailable",
                )
            )
            service = MemoryService(
                MemoryConfig(
                    root=root / "memory",
                    backend="graphiti",
                    graphiti_enabled=True,
                ),
                run_id="run-1",
                repo_full_name="example/repo",
                graphiti_backend=fake,
            )

            result = service.note_agent_memory(
                "repo",
                "Remember this even if Graphiti is unavailable.",
                ["repo"],
                "medium",
                "unit-test",
            )
            report = service.finalize_run({"terminal": "completed"}, run_dir)

            self.assertTrue(result.success)
            self.assertTrue(result.degraded)
            self.assertEqual("graphiti_unavailable", result.error_kind)
            self.assertIn("agent_lesson_proposed", service.events_text())
            self.assertTrue(report.degraded)
            self.assertFalse(report.graphiti_available)
            self.assertEqual("graphiti_unavailable", report.failures[0]["error_kind"])

    def test_global_scope_note_remains_event_log_only_degraded_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = MemoryService(
                MemoryConfig(
                    root=Path(tmp) / "memory",
                    backend="graphiti",
                    graphiti_enabled=True,
                ),
                run_id="run-1",
                repo_full_name="example/repo",
                graphiti_backend=FakeGraphitiBackend(),
            )

            result = service.note_agent_memory(
                "global",
                "Global experience is still deferred.",
                ["global"],
                "medium",
                "unit-test",
            )

            self.assertTrue(result.success)
            self.assertTrue(result.degraded)
            self.assertEqual("l2_l3_not_implemented", result.error_kind)
            self.assertIn("agent_lesson_proposed", service.events_text())

    def test_lifecycle_observation_writes_repo_episode_and_tracked_pr_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = FakeGraphitiBackend()
            service = MemoryService(
                MemoryConfig(
                    root=root / "memory",
                    backend="graphiti",
                    graphiti_enabled=True,
                ),
                run_id="lifecycle-run",
                repo_full_name="example/repo",
                graphiti_backend=fake,
            )
            record = PrLifecycleRecord(
                repository="example/repo",
                number=12,
                url="https://github.com/example/repo/pull/12",
                originating_run_dir=str(root / "run"),
                lifecycle_status="needs_response",
                ci_status="failure",
                summary="external PR needs agent follow-up",
            )

            write = service.record_lifecycle_observation(record, review_count=2)
            context = service.start_run_context("example/repo", tracked_prs=[record])

            self.assertTrue(write.success)
            self.assertEqual("lifecycle_observed", fake.episodes[0]["event_type"])
            self.assertEqual(1, len(context.tracked_prs))
            tracked = context.tracked_prs[0]
            self.assertEqual("example/repo", tracked.repository)
            self.assertEqual(12, tracked.number)
            self.assertEqual("failure", tracked.ci_status)
            self.assertEqual("needs_response", tracked.lifecycle_status)
            self.assertIn("agent follow-up", tracked.summary)
            self.assertEqual("external_write", tracked.detail_queries[0].category)
            self.assertIn("pull request 12", tracked.detail_queries[0].suggested_query)
            self.assertEqual(context.tracked_prs, service.working.tracked_prs)


if __name__ == "__main__":
    unittest.main()
