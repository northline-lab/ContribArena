from __future__ import annotations

import unittest
from pathlib import Path

from contribarena.config.schema import (
    DEFAULT_MEMORY_RELATIVE,
    DiscoveryConfig,
    MemoryConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.runtime_config import apply_output_dir


def _make_config(memory: MemoryConfig | None = None) -> RunConfig:
    kwargs = dict(
        run=RunSection(),
        discovery=DiscoveryConfig(
            candidates=[
                RepoCandidate(
                    owner="example",
                    repo="repo",
                    url="https://github.com/example/repo",
                )
            ]
        ),
        workspace=WorkspaceConfig(),
    )
    if memory is not None:
        kwargs["memory"] = memory
    return RunConfig(**kwargs)


class ApplyOutputDirTest(unittest.TestCase):
    def test_returns_config_unchanged_when_output_dir_is_none(self) -> None:
        config = _make_config()

        result = apply_output_dir(config, None)

        self.assertIs(result, config)

    def test_rewrites_artifacts_and_memory_when_memory_is_default_relative(self) -> None:
        config = _make_config()
        self.assertEqual(DEFAULT_MEMORY_RELATIVE, config.memory.root)
        output_dir = Path("/tmp/contribarena/runs/run-0001")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(output_dir, result.artifacts.output_root)
        # memory root should be relocated next to the output directory's parent
        self.assertEqual(output_dir.parent / "memory", result.memory.root)
        # original config is not mutated
        self.assertEqual(Path("runs"), config.artifacts.output_root)
        self.assertEqual(DEFAULT_MEMORY_RELATIVE, config.memory.root)

    def test_preserves_customized_relative_memory_root(self) -> None:
        custom_memory = MemoryConfig(root=Path("custom/memory"))
        config = _make_config(memory=custom_memory)
        output_dir = Path("/tmp/contribarena/runs/run-0002")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(output_dir, result.artifacts.output_root)
        # Customized relative root must not be rewritten.
        self.assertEqual(Path("custom/memory"), result.memory.root)

    def test_preserves_absolute_memory_root(self) -> None:
        absolute_memory = MemoryConfig(root=Path("/var/lib/contribarena/memory"))
        config = _make_config(memory=absolute_memory)
        output_dir = Path("/tmp/contribarena/runs/run-0003")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(output_dir, result.artifacts.output_root)
        # Absolute root must be preserved as-is.
        self.assertEqual(Path("/var/lib/contribarena/memory"), result.memory.root)


if __name__ == "__main__":
    unittest.main()
