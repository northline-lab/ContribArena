from __future__ import annotations

import unittest

from contribarena.errors import (
    AgentError,
    BudgetExhausted,
    ConfigError,
    ContribArenaError,
    InfrastructureError,
)


class ErrorHierarchyTest(unittest.TestCase):
    """Each ContribArena error subclass inherits from ContribArenaError."""

    def test_base_error_inherits_from_exception(self) -> None:
        self.assertTrue(issubclass(ContribArenaError, Exception))

    def test_config_error_inherits_from_base(self) -> None:
        self.assertTrue(issubclass(ConfigError, ContribArenaError))

    def test_infrastructure_error_inherits_from_base(self) -> None:
        self.assertTrue(issubclass(InfrastructureError, ContribArenaError))

    def test_agent_error_inherits_from_base(self) -> None:
        self.assertTrue(issubclass(AgentError, ContribArenaError))

    def test_budget_exhausted_inherits_from_base(self) -> None:
        self.assertTrue(issubclass(BudgetExhausted, ContribArenaError))


class ErrorExitCodeTest(unittest.TestCase):
    """Each error class exposes a stable exit_code used by the CLI."""

    def test_base_error_exit_code(self) -> None:
        self.assertEqual(1, ContribArenaError.exit_code)

    def test_config_error_exit_code(self) -> None:
        self.assertEqual(1, ConfigError.exit_code)

    def test_infrastructure_error_exit_code(self) -> None:
        self.assertEqual(2, InfrastructureError.exit_code)

    def test_agent_error_exit_code(self) -> None:
        self.assertEqual(3, AgentError.exit_code)

    def test_budget_exhausted_exit_code(self) -> None:
        self.assertEqual(4, BudgetExhausted.exit_code)

    def test_exit_codes_are_distinct_across_categories(self) -> None:
        # ConfigError shares exit_code 1 with the base; the operational
        # categories (infrastructure/agent/budget) each have a distinct code.
        codes = {
            InfrastructureError.exit_code,
            AgentError.exit_code,
            BudgetExhausted.exit_code,
        }
        self.assertEqual(3, len(codes))

    def test_exit_code_survives_instance_access(self) -> None:
        # exit_code is defined on the class; instances see the same value.
        self.assertEqual(2, InfrastructureError("boom").exit_code)
        self.assertEqual(4, BudgetExhausted("out of budget").exit_code)


class ErrorRaiseAndCatchTest(unittest.TestCase):
    """Subclasses can be caught via the common ContribArenaError base."""

    def test_config_error_caught_as_base(self) -> None:
        with self.assertRaises(ContribArenaError) as ctx:
            raise ConfigError("bad config")
        self.assertIsInstance(ctx.exception, ConfigError)
        self.assertEqual("bad config", str(ctx.exception))

    def test_infrastructure_error_caught_as_base(self) -> None:
        with self.assertRaises(ContribArenaError):
            raise InfrastructureError("docker down")

    def test_agent_error_caught_as_base(self) -> None:
        with self.assertRaises(ContribArenaError):
            raise AgentError("agent failed")

    def test_budget_exhausted_caught_as_base(self) -> None:
        with self.assertRaises(ContribArenaError):
            raise BudgetExhausted("budget exhausted")

    def test_base_error_preserves_message(self) -> None:
        err = ContribArenaError("general failure")
        self.assertEqual("general failure", str(err))


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
