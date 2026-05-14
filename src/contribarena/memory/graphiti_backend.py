from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from contribarena.config.schema import MemoryConfig
from contribarena.memory.redact import redact_payload, redact_text
from contribarena.memory.schema import MemorySearchItem


@dataclass
class GraphitiWriteResult:
    success: bool
    episode_ids: list[str] = field(default_factory=list)
    degraded: bool = False
    error_kind: str = ""
    error_message: str = ""


@dataclass
class GraphitiSearchResult:
    success: bool
    results: list[MemorySearchItem] = field(default_factory=list)
    degraded: bool = False
    error_kind: str = ""
    error_message: str = ""


class GraphitiBackend(Protocol):
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
        ...

    def search_repo(
        self,
        *,
        repo_full_name: str,
        query: str,
        limit: int,
    ) -> GraphitiSearchResult:
        ...

    def close(self) -> None:
        ...


class GraphitiMemoryBackend:
    def __init__(self, config: MemoryConfig) -> None:
        self.config = config
        self._episode_type_text: Any | None = None
        self._setup_failed: str = ""
        self._instances: dict[str, Any] = {}
        self._runner = _AsyncRunner()

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
        preflight_error = self._preflight_error()
        if preflight_error:
            return GraphitiWriteResult(
                success=False,
                degraded=True,
                error_kind="graphiti_unavailable",
                error_message=preflight_error,
            )
        clean_payload = redact_payload(
            {
                "event_type": event_type,
                "run_id": run_id,
                "repository": repo_full_name,
                "event_id": event_id,
                "source_ref": source_ref,
                "confidence": confidence,
                **payload,
            }
        )
        try:
            return self._runner.run(
                self._add_repo_episode_async(
                    repo_full_name=repo_full_name,
                    event_id=event_id,
                    event_type=event_type,
                    payload=clean_payload,
                ),
                timeout=self.config.graphiti_timeout_seconds + 5,
            )
        except Exception as exc:
            return GraphitiWriteResult(
                success=False,
                degraded=True,
                error_kind=_graphiti_error_kind(exc),
                error_message=redact_text(str(exc), max_chars=512),
            )

    def search_repo(
        self,
        *,
        repo_full_name: str,
        query: str,
        limit: int,
    ) -> GraphitiSearchResult:
        preflight_error = self._preflight_error()
        if preflight_error:
            return GraphitiSearchResult(
                success=False,
                degraded=True,
                error_kind="graphiti_unavailable",
                error_message=preflight_error,
            )
        try:
            return self._runner.run(
                self._search_repo_async(
                    repo_full_name=repo_full_name,
                    query=redact_text(query, max_chars=512),
                    limit=limit,
                ),
                timeout=self.config.graphiti_timeout_seconds + 5,
            )
        except Exception as exc:
            return GraphitiSearchResult(
                success=False,
                degraded=True,
                error_kind=_graphiti_error_kind(exc),
                error_message=redact_text(str(exc), max_chars=512),
            )

    async def _add_repo_episode_async(
        self,
        *,
        repo_full_name: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> GraphitiWriteResult:
        try:
            graphiti = await self._get_graphiti(repo_full_name)
            episode_id = event_id or uuid.uuid4().hex[:12]
            await asyncio.wait_for(
                graphiti.add_episode(
                    name=f"{event_type}_{episode_id}",
                    episode_body=_episode_body(payload),
                    source=self._episode_type_text,
                    source_description=f"ContribArena memory event: {event_type}",
                    reference_time=datetime.now(UTC),
                    group_id=_repo_group_id(self.config, repo_full_name),
                ),
                timeout=self.config.graphiti_timeout_seconds,
            )
            return GraphitiWriteResult(success=True, episode_ids=[episode_id])
        except Exception as exc:
            return GraphitiWriteResult(
                success=False,
                degraded=True,
                error_kind=_graphiti_error_kind(exc),
                error_message=redact_text(str(exc), max_chars=512),
            )

    async def _search_repo_async(
        self,
        *,
        repo_full_name: str,
        query: str,
        limit: int,
    ) -> GraphitiSearchResult:
        try:
            graphiti = await self._get_graphiti(repo_full_name)
            from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF

            search_config = COMBINED_HYBRID_SEARCH_RRF.model_copy(deep=True)
            search_config.limit = limit
            raw_results = await asyncio.wait_for(
                graphiti.search_(
                    query=query,
                    config=search_config,
                    group_ids=[_repo_group_id(self.config, repo_full_name)],
                ),
                timeout=self.config.graphiti_timeout_seconds,
            )
            items = _search_items(raw_results)
            return GraphitiSearchResult(
                success=True,
                results=items[:limit],
            )
        except Exception as exc:
            return GraphitiSearchResult(
                success=False,
                degraded=True,
                error_kind=_graphiti_error_kind(exc),
                error_message=redact_text(str(exc), max_chars=512),
            )

    async def _get_graphiti(self, repo_full_name: str) -> Any:
        if self._setup_failed:
            raise RuntimeError(self._setup_failed)
        group_id = _repo_group_id(self.config, repo_full_name)
        if group_id in self._instances:
            return self._instances[group_id]
        try:
            graphiti = await self._build_graphiti(group_id)
        except Exception as exc:
            self._setup_failed = redact_text(str(exc), max_chars=256)
            raise
        self._instances[group_id] = graphiti
        return graphiti

    async def _build_graphiti(self, group_id: str) -> Any:
        try:
            from graphiti_core import Graphiti
            from graphiti_core.cross_encoder.client import CrossEncoderClient
            from graphiti_core.driver.falkordb_driver import FalkorDriver
            from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
            from graphiti_core.llm_client.config import LLMConfig
            from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
            from graphiti_core.nodes import EpisodeType
        except ImportError as exc:
            self._setup_failed = "graphiti-core[falkordb] is not installed"
            raise RuntimeError(self._setup_failed) from exc
        try:
            llm_client = OpenAIGenericClient(
                LLMConfig(
                    api_key=_required_env(self.config.graphiti_llm_api_key_env, "Graphiti LLM"),
                    base_url=_openai_base_url(
                        _resolve_value(
                            self.config.graphiti_llm_base_url,
                            self.config.graphiti_llm_base_url_env,
                        )
                    ),
                    model=self.config.graphiti_llm_model,
                    small_model=self.config.graphiti_llm_small_model or self.config.graphiti_llm_model,
                    temperature=self.config.graphiti_llm_temperature,
                )
            )
            embedder = OpenAIEmbedder(
                OpenAIEmbedderConfig(
                    api_key=_required_env(
                        self.config.graphiti_embedding_api_key_env,
                        "Graphiti embedding",
                    ),
                    base_url=_openai_base_url(
                        _resolve_value(
                            self.config.graphiti_embedding_base_url,
                            self.config.graphiti_embedding_base_url_env,
                        )
                    ),
                    embedding_model=self.config.graphiti_embedding_model,
                    embedding_dim=self.config.graphiti_embedding_dim,
                )
            )
            driver = FalkorDriver(
                host=self.config.falkordb_host,
                port=self.config.falkordb_port,
                username=self.config.falkordb_username or None,
                password=os.environ.get(self.config.falkordb_password_env) or None,
                database=group_id,
            )
            graphiti = Graphiti(
                graph_driver=driver,
                llm_client=llm_client,
                embedder=embedder,
                cross_encoder=_lexical_cross_encoder(CrossEncoderClient),
            )
            await asyncio.wait_for(
                graphiti.build_indices_and_constraints(),
                timeout=self.config.graphiti_timeout_seconds,
            )
        except Exception:
            raise
        self._episode_type_text = EpisodeType.text
        return graphiti

    def _preflight_error(self) -> str:
        for env_name, label in [
            (self.config.graphiti_llm_api_key_env, "Graphiti LLM"),
            (self.config.graphiti_embedding_api_key_env, "Graphiti embedding"),
        ]:
            if not os.environ.get(env_name):
                return f"{env_name} is required for {label}"
        return ""

    def close(self) -> None:
        self._runner.close()


def make_graphiti_backend(config: MemoryConfig) -> GraphitiBackend | None:
    if not config.enabled or not config.graphiti_enabled or config.backend != "graphiti":
        return None
    return GraphitiMemoryBackend(config)


class _AsyncRunner:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def run(self, coro: Any, *, timeout: int) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
        self._ensure_loop()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("Graphiti operation timed out")

    def _ensure_loop(self) -> None:
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()

        def loop_worker() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=loop_worker, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._loop is None:
            raise RuntimeError("Graphiti async runner did not start")

    def close(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self._loop = None
        self._thread = None


def _repo_group_id(config: MemoryConfig, repo_full_name: str) -> str:
    raw = f"{config.graphiti_group_prefix}_{repo_full_name or 'unknown_repo'}"
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    return cleaned or "contribarena_unknown_repo"


def _episode_body(payload: dict[str, Any]) -> str:
    text = str(payload.get("text") or "").strip()
    metadata = {key: value for key, value in payload.items() if key != "text"}
    parts = ["ContribArena memory event."]
    if text:
        parts.append(f"Memory: {text}")
    if metadata:
        parts.append(f"Metadata: {json.dumps(metadata, sort_keys=True, ensure_ascii=True)}")
    return "\n".join(parts)


def _lexical_cross_encoder(base_cls: type[Any]) -> Any:
    class LexicalCrossEncoder(base_cls):  # type: ignore[misc, valid-type]
        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            query_terms = set(re.findall(r"[A-Za-z0-9_/-]+", query.lower()))
            ranked = []
            for passage in passages:
                passage_terms = set(re.findall(r"[A-Za-z0-9_/-]+", passage.lower()))
                ranked.append((passage, float(len(query_terms & passage_terms))))
            return sorted(ranked, key=lambda item: item[1], reverse=True)

    return LexicalCrossEncoder()


def _required_env(env_name: str, label: str) -> str:
    value = os.environ.get(env_name)
    if not value:
        raise RuntimeError(f"{env_name} is required for {label}")
    return value


def _resolve_value(value: str | None, env_name: str | None) -> str | None:
    if value:
        return value
    if env_name:
        return os.environ.get(env_name)
    return None


def _openai_base_url(url: str | None) -> str | None:
    if not url:
        return None
    stripped = url.rstrip("/")
    suffix = "/chat/completions"
    if stripped.endswith(suffix):
        return stripped[: -len(suffix)]
    return stripped


def _search_item(item: object) -> MemorySearchItem:
    text = _extract_text(item)
    source_ref = _extract_source_ref(item)
    return MemorySearchItem(
        text=redact_text(text, max_chars=800),
        source="graphiti",
        source_ref=source_ref,
        title="Graphiti repo memory",
        record_type="repo_memory",
        reason="matched Graphiti repo memory",
        confidence="medium",
    )


def _search_items(raw_results: object) -> list[MemorySearchItem]:
    items: list[MemorySearchItem] = []
    for attr in ("edges", "nodes", "episodes", "communities"):
        for item in getattr(raw_results, attr, []) or []:
            items.append(_search_item(item))
    if not items and isinstance(raw_results, list):
        items.extend(_search_item(item) for item in raw_results)
    return items


def _extract_text(item: object) -> str:
    for attr in ("fact", "text", "content", "summary", "episode_body", "name"):
        value = getattr(item, attr, None)
        if value:
            return str(value)
    if isinstance(item, dict):
        for key in ("fact", "text", "content", "summary", "episode_body"):
            if item.get(key):
                return str(item[key])
    return str(item)


def _extract_source_ref(item: object) -> str:
    for attr in ("source_node_name", "name", "uuid"):
        value = getattr(item, attr, None)
        if value:
            return str(value)
    if isinstance(item, dict):
        for key in ("source_ref", "source_node_name", "name", "uuid"):
            if item.get(key):
                return str(item[key])
    return "graphiti"


def _graphiti_error_kind(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "graphiti_timeout"
    message = str(exc).lower()
    if "not installed" in message or "no module named" in message:
        return "graphiti_unavailable"
    if "timeout" in message:
        return "graphiti_timeout"
    return "graphiti_unavailable"
