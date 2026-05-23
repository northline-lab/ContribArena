from __future__ import annotations

from pathlib import Path

from contribarena.config.schema import DEFAULT_MEMORY_RELATIVE, RunConfig


def apply_output_dir(config: RunConfig, output_dir: Path | None) -> RunConfig:
    if output_dir is None:
        return config
    memory_root = config.memory.root
    if not memory_root.is_absolute() and memory_root == DEFAULT_MEMORY_RELATIVE:
        memory_root = output_dir.parent / "memory"
    return config.model_copy(
        update={
            "artifacts": config.artifacts.model_copy(update={"output_root": output_dir}),
            "memory": config.memory.model_copy(update={"root": memory_root}),
        }
    )
