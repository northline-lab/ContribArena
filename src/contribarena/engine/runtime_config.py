from __future__ import annotations

from pathlib import Path

from contribarena.config.schema import RunConfig


def apply_output_dir(config: RunConfig, output_dir: Path | None) -> RunConfig:
    if output_dir is None:
        return config
    return config.model_copy(
        update={
            "artifacts": config.artifacts.model_copy(update={"output_root": output_dir})
        }
    )
