from __future__ import annotations

import json
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

models:
  providers:
    compatible: {}
"""


def load_run_config(path: Path) -> RunConfig:
    if not path.exists():
        raise ConfigError(f"config file does not exist: {path}")
    try:
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
