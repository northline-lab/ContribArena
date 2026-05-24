from __future__ import annotations

import unittest
from types import SimpleNamespace

from contribarena.agent.prompts import (
    RECOVERY_TEMPLATES,
    _clone_command,
    _clone_url,
    _live_prompt_completion_rule,
    _phase_runtime_contract,
    _recovery_template_text,
)


class CloneUrlTest(unittest.TestCase):
    def test_adds_dot_git_suffix_when_missing(self) -> None:
        url = "https://github.com/owner/repo"
        self.assertEqual("https://github.com/owner/repo.git", _clone_url(url))

    def test_preserves_existing_dot_git_suffix(self) -> None:
        url = "https://github.com/owner/repo.git"
        self.assertEqual(url, _clone_url(url))

    def test_preserves_non_github_urls(self) -> None:
        url = "https://gitlab.com/namespace/project"
        self.assertEqual(url, _clone_url(url))

    def test_empty_string_preserved(self) -> None:
        self.assertEqual("", _clone_url(""))


class CloneCommandTest(unittest.TestCase):
    def test_returns_command_with_clone_url(self) -> None:
        url = "https://github.com/owner/repo.git"
        cmd = _clone_command(url)
        self.assertIn("rm -rf repo", cmd)
        self.assertIn(f"git -c http.version=HTTP/1.1 clone --depth 1 {url} repo", cmd)
        self.assertIn("test -d repo/.git", cmd)

    def test_triple_retry_loop(self) -> None:
        cmd = _clone_command("https://example.com/repo.git")
        self.assertIn("for attempt in 1 2 3; do", cmd)
        self.assertIn("rm -rf repo; sleep 2;", cmd)


class RecoveryTemplateTextTest(unittest.TestCase):
    def test_includes_all_six_recovery_kinds(self) -> None:
        text = _recovery_template_text()
        self.assertIn("- format_error:", text)
        self.assertIn("- blocked_command:", text)
        self.assertIn("- command_timeout:", text)
        self.assertIn("- too_large_output:", text)
        self.assertIn("- patch_failure:", text)
        self.assertIn("- submit_review_failed:", text)

    def test_recovery_text_matches_known_keys(self) -> None:
        text = _recovery_template_text()
        for name in RECOVERY_TEMPLATES:
            self.assertIn(f"- {name}:", text)

    def test_format_error_template_includes_malformed(self) -> None:
        text = _recovery_template_text()
        self.assertIn("malformed", text)


class LivePromptCompletionRuleTest(unittest.TestCase):
    def test_owned_live_returns_live_pr_recipe(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="owned_live"))
        result = _live_prompt_completion_rule(config)
        self.assertIn("github_open_pr", result)
        self.assertIn("returns opened or existing", result)

    def test_external_live_returns_live_pr_recipe(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="external_live"))
        result = _live_prompt_completion_rule(config)
        self.assertIn("github_open_pr", result)
        self.assertIn("returns opened or existing", result)

    def test_shadow_mode_returns_no_live_pr_text(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="shadow"))
        result = _live_prompt_completion_rule(config)
        self.assertIn("Shadow mode stops at a reviewed patch", result)
        self.assertNotIn("github_open_pr", result)

    def test_dry_run_returns_shadow_text(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="dry_run"))
        result = _live_prompt_completion_rule(config)
        self.assertIn("Shadow mode stops", result)


class PhaseRuntimeContractTest(unittest.TestCase):
    def test_live_mode_includes_github_tools_in_review_contract(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="owned_live"))
        result = _phase_runtime_contract(config)
        self.assertIn("github_prepare_fork", result)
        self.assertIn("github_prepare_branch", result)
        self.assertIn("github_commit", result)
        self.assertIn("github_push_branch", result)
        self.assertIn("github_open_pr", result)
        self.assertIn("completes the live contribution", result)

    def test_external_live_includes_live_github_tools(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="external_live"))
        result = _phase_runtime_contract(config)
        self.assertIn("github_open_pr", result)

    def test_shadow_mode_excludes_live_github_writes(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="shadow"))
        result = _phase_runtime_contract(config)
        self.assertIn("Shadow mode stops at a reviewed patch", result)
        self.assertNotIn("github_prepare_fork", result)

    def test_all_phases_and_tool_names_present(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="owned_live"))
        result = _phase_runtime_contract(config)
        self.assertIn("Scout/project", result)
        self.assertIn("Scout/opportunity", result)
        self.assertIn("Work", result)
        self.assertIn("Review", result)
        self.assertIn("aci_dispute_review", result)
        self.assertIn("aci_submit_patch", result)
        self.assertIn("aci_submit_patch_finalize", result)
        self.assertIn("evidence_refs_json", result)

    def test_includes_repo_search_and_eligibility_tools(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="owned_live"))
        result = _phase_runtime_contract(config)
        self.assertIn("repo_search", result)
        self.assertIn("repo_check_eligibility", result)
        self.assertIn("repo_get_metadata", result)

    def test_includes_aci_tool_mentions(self) -> None:
        config = SimpleNamespace(run=SimpleNamespace(mode="owned_live"))
        result = _phase_runtime_contract(config)
        self.assertIn("aci_view", result)
        self.assertIn("aci_search", result)
        self.assertIn("aci_find_files", result)


if __name__ == "__main__":
    unittest.main()
