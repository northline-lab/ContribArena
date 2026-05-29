from __future__ import annotations

import unittest
from pathlib import Path

from contribarena.config.schema import (
    DEFAULT_MEMORY_RELATIVE,
    DiscoveryConfig,
    MemoryConfig,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.runtime_config import apply_output_dir


def make_config(memory_root: Path = DEFAULT_MEMORY_RELATIVE) -> RunConfig:
    return RunConfig(
        run=RunSection(),
        discovery=DiscoveryConfig(query="agent framework"),
        workspace=WorkspaceConfig(),
        memory=MemoryConfig(root=memory_root),
    )


class ApplyOutputDirTest(unittest.TestCase):
    def test_none_output_dir_returns_original_config(self) -> None:
        config = make_config()

        result = apply_output_dir(config, None)

        self.assertIs(config, result)
        self.assertEqual(Path("runs"), result.artifacts.output_root)
        self.assertEqual(DEFAULT_MEMORY_RELATIVE, result.memory.root)

    def test_sets_artifact_output_root(self) -> None:
        config = make_config()
        output_dir = Path("/tmp/contribarena-runs/run-123")

        result = apply_output_dir(config, output_dir)

        self.assertIsNot(config, result)
        self.assertEqual(output_dir, result.artifacts.output_root)

    def test_moves_default_relative_memory_root_next_to_output_parent(self) -> None:
        config = make_config()
        output_dir = Path("/tmp/contribarena-runs/run-123")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(Path("/tmp/contribarena-runs/memory"), result.memory.root)

    def test_preserves_custom_relative_memory_root(self) -> None:
        config = make_config(Path("custom-memory"))
        output_dir = Path("/tmp/contribarena-runs/run-123")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(Path("custom-memory"), result.memory.root)

    def test_preserves_absolute_memory_root(self) -> None:
        config = make_config(Path("/var/lib/contribarena-memory"))
        output_dir = Path("/tmp/contribarena-runs/run-123")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(Path("/var/lib/contribarena-memory"), result.memory.root)

    def test_original_config_is_not_mutated_when_output_dir_is_applied(self) -> None:
        config = make_config()
        output_dir = Path("/tmp/contribarena-runs/run-123")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(Path("runs"), config.artifacts.output_root)
        self.assertEqual(DEFAULT_MEMORY_RELATIVE, config.memory.root)
        self.assertEqual(output_dir, result.artifacts.output_root)
        self.assertEqual(Path("/tmp/contribarena-runs/memory"), result.memory.root)


if __name__ == "__main__":
    unittest.main()
