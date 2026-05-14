from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import MemoryConfig
from contribarena.memory.history_index import HistoryIndex
from contribarena.memory.service import MemoryService


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
            self.assertEqual("l2_l3_not_implemented", result.error_kind)
            self.assertNotIn("repo", service.working.facts)
            self.assertIn("agent_lesson_proposed", service.events_text())


if __name__ == "__main__":
    unittest.main()
