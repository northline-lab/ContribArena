from __future__ import annotations

import unittest
from pathlib import Path

from contribarena.config.schema import (
    DEFAULT_MEMORY_RELATIVE,
    ArtifactConfig,
    DiscoveryConfig,
    MemoryConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.runtime_config import apply_output_dir


def _base_config(
    *,
    memory: MemoryConfig | None = None,
    artifacts: ArtifactConfig | None = None,
) -> RunConfig:
    return RunConfig(
        run=RunSection(mode="shadow", model="local-stub"),
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
        artifacts=artifacts if artifacts is not None else ArtifactConfig(),
        memory=memory if memory is not None else MemoryConfig(),
    )


class ApplyOutputDirTest(unittest.TestCase):
    """apply_output_dir wires output_dir into artifacts and (conditionally) memory."""

    def test_none_output_dir_returns_input_config_unchanged(self) -> None:
        config = _base_config()

        result = apply_output_dir(config, None)

        # When output_dir is None, the helper short-circuits and returns the
        # exact same RunConfig instance, leaving artifacts and memory untouched.
        self.assertIs(config, result)
        self.assertEqual(Path("runs"), result.artifacts.output_root)
        self.assertEqual(DEFAULT_MEMORY_RELATIVE, result.memory.root)

    def test_output_dir_updates_artifacts_output_root(self) -> None:
        config = _base_config()
        output_dir = Path("/tmp/contribarena/runs/run-1")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(output_dir, result.artifacts.output_root)

    def test_default_relative_memory_root_is_rebased_under_output_dir_parent(self) -> None:
        # MemoryConfig defaults memory.root to DEFAULT_MEMORY_RELATIVE
        # (".contribarena/memory"). In that case the helper rebases memory.root
        # to output_dir.parent / "memory" so per-run artifacts and shared
        # memory live as siblings under the same parent.
        config = _base_config()
        output_dir = Path("/tmp/contribarena/runs/run-1")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(Path("/tmp/contribarena/runs/memory"), result.memory.root)

    def test_absolute_memory_root_is_preserved(self) -> None:
        # An operator-provided absolute memory.root must not be silently moved.
        absolute_memory_root = Path("/var/contribarena/memory")
        config = _base_config(memory=MemoryConfig(root=absolute_memory_root))
        output_dir = Path("/tmp/contribarena/runs/run-1")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(absolute_memory_root, result.memory.root)

    def test_non_default_relative_memory_root_is_preserved(self) -> None:
        # A non-default relative memory.root is also preserved verbatim;
        # only the default sentinel triggers the rebasing behavior.
        custom_relative = Path("custom-memory")
        config = _base_config(memory=MemoryConfig(root=custom_relative))
        output_dir = Path("/tmp/contribarena/runs/run-1")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(custom_relative, result.memory.root)

    def test_returns_new_config_without_mutating_input(self) -> None:
        # The helper relies on Pydantic model_copy and must not mutate the
        # input config's nested artifacts/memory submodels.
        config = _base_config()
        original_output_root = config.artifacts.output_root
        original_memory_root = config.memory.root
        output_dir = Path("/tmp/contribarena/runs/run-1")

        result = apply_output_dir(config, output_dir)

        self.assertIsNot(config, result)
        self.assertEqual(original_output_root, config.artifacts.output_root)
        self.assertEqual(original_memory_root, config.memory.root)
        self.assertEqual(output_dir, result.artifacts.output_root)

    def test_other_config_sections_are_preserved(self) -> None:
        # Fields outside artifacts and memory must round-trip unchanged.
        config = _base_config()
        output_dir = Path("/tmp/contribarena/runs/run-1")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(config.run.mode, result.run.mode)
        self.assertEqual(config.run.model, result.run.model)
        self.assertEqual(
            config.discovery.candidates[0].full_name,
            result.discovery.candidates[0].full_name,
        )
        self.assertEqual(config.workspace.backend, result.workspace.backend)

    def test_memory_config_fields_other_than_root_are_preserved(self) -> None:
        # When memory.root is rebased, other MemoryConfig fields must survive
        # the model_copy untouched.
        config = _base_config(
            memory=MemoryConfig(
                enabled=False,
                backend="graphiti",
                graphiti_enabled=True,
            )
        )
        output_dir = Path("/tmp/contribarena/runs/run-1")

        result = apply_output_dir(config, output_dir)

        # backend/enabled/graphiti_enabled round-trip
        self.assertFalse(result.memory.enabled)
        self.assertEqual("graphiti", result.memory.backend)
        self.assertTrue(result.memory.graphiti_enabled)
        # memory.root rebased because the default sentinel was in use
        self.assertEqual(Path("/tmp/contribarena/runs/memory"), result.memory.root)

    def test_relative_output_dir_rebases_memory_under_relative_parent(self) -> None:
        # The helper inspects output_dir.parent without requiring it to be
        # absolute; a relative output_dir produces a relative memory.root.
        config = _base_config()
        output_dir = Path("runs/run-1")

        result = apply_output_dir(config, output_dir)

        self.assertEqual(Path("runs") / "memory", result.memory.root)
        self.assertEqual(output_dir, result.artifacts.output_root)
