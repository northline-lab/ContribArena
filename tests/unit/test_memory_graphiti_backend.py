from __future__ import annotations

import unittest
from unittest.mock import patch

from contribarena.config.schema import MemoryConfig
from contribarena.memory.graphiti_backend import (
    GraphitiMemoryBackend,
    _episode_body,
    _openai_base_url,
    _repo_group_id,
)


class GraphitiBackendTest(unittest.TestCase):
    def test_repo_group_id_is_graphiti_safe_slug(self) -> None:
        config = MemoryConfig(graphiti_group_prefix="contribarena")

        group_id = _repo_group_id(config, "qWaitCrypto/test.repo-with-dash")

        self.assertEqual("contribarena_qWaitCrypto_test_repo_with_dash", group_id)
        self.assertRegex(group_id, r"^[A-Za-z0-9_]+$")

    def test_openai_base_url_accepts_chat_completions_endpoint(self) -> None:
        self.assertEqual(
            "https://example.test/v1",
            _openai_base_url("https://example.test/v1/chat/completions"),
        )
        self.assertEqual("https://example.test/v1", _openai_base_url("https://example.test/v1/"))

    def test_episode_body_prioritizes_memory_text(self) -> None:
        body = _episode_body(
            {
                "text": "Run compileall before submit.",
                "repository": "example/repo",
                "tags": ["verification"],
            }
        )

        self.assertIn("Memory: Run compileall before submit.", body)
        self.assertIn('"repository": "example/repo"', body)

    def test_missing_graphiti_llm_env_fails_soft(self) -> None:
        config = MemoryConfig(
            backend="graphiti",
            graphiti_enabled=True,
            graphiti_llm_api_key_env="MISSING_GRAPHITI_LLM_KEY",
            graphiti_embedding_api_key_env="MISSING_GRAPHITI_EMBEDDING_KEY",
        )
        backend = GraphitiMemoryBackend(config)

        with patch.dict("os.environ", {}, clear=True):
            result = backend.add_repo_episode(
                repo_full_name="example/repo",
                run_id="run-1",
                event_id="event-1",
                event_type="agent_lesson_proposed",
                payload={"text": "remember compileall"},
                source_ref="unit-test",
                confidence="medium",
            )

        self.assertFalse(result.success)
        self.assertTrue(result.degraded)
        self.assertEqual("graphiti_unavailable", result.error_kind)
        self.assertIn("MISSING_GRAPHITI_LLM_KEY", result.error_message)

    def test_get_graphiti_caches_instance_per_repo_group(self) -> None:
        state = {"builds": 0}

        config = MemoryConfig(
            backend="graphiti",
            graphiti_enabled=True,
            graphiti_llm_api_key_env="GRAPHITI_LLM_KEY",
            graphiti_embedding_api_key_env="GRAPHITI_EMBEDDING_KEY",
        )
        backend = GraphitiMemoryBackend(config)

        async def fake_build(group_id: str) -> object:
            state["builds"] += 1
            return {"group_id": group_id}

        backend._build_graphiti = fake_build  # type: ignore[method-assign]
        first = backend._runner.run(backend._get_graphiti("example/repo"), timeout=5)
        second = backend._runner.run(backend._get_graphiti("example/repo"), timeout=5)
        other = backend._runner.run(backend._get_graphiti("example/other"), timeout=5)
        backend.close()

        self.assertIs(first, second)
        self.assertIsNot(first, other)
        self.assertEqual(2, state["builds"])

    def test_setup_failure_is_redacted_before_reuse(self) -> None:
        config = MemoryConfig(
            backend="graphiti",
            graphiti_enabled=True,
            graphiti_llm_api_key_env="GRAPHITI_LLM_KEY",
            graphiti_embedding_api_key_env="GRAPHITI_EMBEDDING_KEY",
        )
        backend = GraphitiMemoryBackend(config)

        async def fail_build(group_id: str) -> object:
            raise RuntimeError("bad token=ghs_secretsecretsecretsecret")

        backend._build_graphiti = fail_build  # type: ignore[method-assign]
        with patch.dict("os.environ", {"GRAPHITI_LLM_KEY": "llm-key", "GRAPHITI_EMBEDDING_KEY": "embedding-key"}):
            result = backend.search_repo(repo_full_name="example/repo", query="guidance", limit=1)
            reused = backend.search_repo(repo_full_name="example/repo", query="guidance", limit=1)
            backend.close()

        self.assertTrue(result.degraded)
        self.assertNotIn("ghs_secret", result.error_message)
        self.assertIn("token=***", result.error_message)
        self.assertNotIn("ghs_secret", reused.error_message)


if __name__ == "__main__":
    unittest.main()
