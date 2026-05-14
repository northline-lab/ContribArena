from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import yaml

from contribarena.errors import ConfigError

from .schema import RunConfig


STARTER_CONFIG = """run:
  mode: shadow
  model: local-stub
  budget:
    max_steps: 25
    max_tokens:
    max_wall_time_seconds: 900

discovery:
  candidates:
    - owner: openai
      repo: openai-agents-python
      url: https://github.com/openai/openai-agents-python
      branch: main
      notes: Example M0.0 candidate. Replace with the repo you want to inspect.

workspace:
  backend: docker
  image: contribarena/workspace:latest
  workdir: /workspace
  command_timeout_seconds: 300
  resources:
    cpus: "2"
    memory: 4g
    disk: 10g

artifacts:
  output_root: runs

memory:
  enabled: true
  root: .contribarena/memory
  backend: noop
  graphiti_enabled: false
  graphiti_graph_backend: falkordb
  falkordb_host: localhost
  falkordb_port: 6379
  falkordb_password_env: FALKORDB_PASSWORD
  graphiti_group_prefix: contribarena
  graphiti_llm_api_key_env: CONTRIBARENA_GRAPHITI_LLM_API_KEY
  graphiti_llm_base_url_env: CONTRIBARENA_GRAPHITI_LLM_BASE_URL
  graphiti_llm_model: gpt-4.1
  graphiti_embedding_api_key_env: CONTRIBARENA_GRAPHITI_EMBEDDING_API_KEY
  graphiti_embedding_base_url_env: CONTRIBARENA_GRAPHITI_EMBEDDING_BASE_URL
  graphiti_embedding_model: text-embedding-3-small
  graphiti_embedding_dim: 1024
  history_index_enabled: true

guidance:
  enabled: true

governance:
  live_enabled: false
  owned_repositories: []
  bot_identity:
    kind: pat
    actor: ""
    token_env: GITHUB_TOKEN
  rate_limits:
    max_open_prs_per_repo: 1
    max_prs_per_repo_per_day: 3
    min_minutes_between_prs_per_repo: 30
    max_open_prs_per_org: 3
    max_prs_per_org_per_day: 5
    min_minutes_between_prs_per_org: 60
    max_open_prs_global: 10
    max_prs_global_per_day: 10
    min_minutes_between_prs_global: 15
  contribution_classes:
    allowed:
      - docs
      - tests
      - low_risk_code
  kill_switches:
    global: false
    repositories: []
    organizations: []
    agents: []
  external_live:
    poll_interval_seconds: 21600
    require_maintainer_fit: true
    require_spam_risk_review: true
    allow_public_comments: true

controller:
  enabled: false
  interval_seconds: 300
  max_ticks: 1

models:
  providers:
    compatible: {}
"""


def load_run_config(path: Path) -> RunConfig:
    if not path.exists():
        raise ConfigError(f"config file does not exist: {path}")
    try:
        _load_dotenv(path.parent / ".env")
        _load_dotenv(Path.cwd() / ".env")
        raw = _load_yaml_like(path.read_text(encoding="utf-8"))
        return RunConfig.model_validate(raw)
    except (OSError, ValueError, ValidationError) as exc:
        raise ConfigError(f"invalid config {path}: {exc}") from exc


def write_starter_config(path: Path) -> None:
    if path.exists():
        raise ConfigError(f"refusing to overwrite existing config: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER_CONFIG, encoding="utf-8")


def _load_yaml_like(text: str) -> dict[str, Any]:
    stripped = text.lstrip()
    loaded = json.loads(text) if stripped.startswith("{") else yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("config root must be a mapping")
    return loaded


def _load_dotenv(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if not key:
            continue
        os.environ.setdefault(key, _clean_env_value(value.strip()))


def _clean_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
