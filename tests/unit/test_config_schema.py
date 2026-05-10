from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contribarena.config import load_run_config, write_starter_config


class ConfigSchemaTest(unittest.TestCase):
    def test_init_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run_config.yaml"
            write_starter_config(path)

            config = load_run_config(path)

            self.assertEqual("shadow", config.run.mode)
            self.assertEqual("docker", config.workspace.backend)
            self.assertEqual(
                "openai/openai-agents-python", config.discovery.candidates[0].full_name
            )


if __name__ == "__main__":
    unittest.main()
