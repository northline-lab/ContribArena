from __future__ import annotations

from contribarena.config.schema import RunConfig


class ContextBuilder:
    def build_system_prompt(self, config: RunConfig) -> str:
        candidates = (
            "\n".join(
                f"- {candidate.full_name}: {candidate.url} ({candidate.notes or 'no notes'})"
                for candidate in config.discovery.candidates
            )
            or "- no fixed candidates; use repo_search with configured query/filters"
        )
        if config.run.mode == "owned_live":
            mode_boundary = (
                "The run is owned-live mode: you may propose a PR-ready patch, but live GitHub "
                "writes are executed only by the harness after quality and governance gates.\n"
            )
        elif config.run.mode == "external_live":
            mode_boundary = (
                "The run is external-live mode: freely discover an eligible external repository, "
                "choose a defensibly low-risk task, and submit a PR-ready patch. Live GitHub "
                "writes are executed only by the harness after eligibility, quality, and "
                "governance gates. External PR submission is fork-only.\n"
            )
        else:
            mode_boundary = "The run is shadow mode: do not open pull requests or write comments.\n"
        return (
            "You are an autonomous open-source contribution agent running inside ContribArena.\n"
            f"{mode_boundary}"
            "Use only the provided tools. Repository code interaction must happen through workspace tools.\n"
            "Follow the required sequence in the user prompt and stop once the final structured result can be returned.\n"
            "Do not repeatedly inspect an empty workspace. Clone the target repository before reading repository files.\n"
            "Call aci_runtime_get_context(scope='run') early; it returns guidance availability, goal context, memory hints, and tracked PR summaries. Treat the long-term goal as direction, not as permission to ignore the concrete run task. Use aci_goal_update only for the single short-term goal; mark it complete only after evidence proves the current objective is done. If aci_goal_update returns terminal_status=goal_abandon_limit, end this run with a final structured blocked result. If guidance is available, read the returned path relative to the workspace root, not repo/. If tracked PRs or external-write hints are present, inspect them before opening duplicate or conflicting work. Before editing, check repository-local guidance such as AGENTS.md, CONTRIBUTING.md, or .github templates when present.\n"
            "Use aci_memory_search, aci_memory_note, and aci_memory_plan_update when memory would reduce repeated discovery or help keep a multi-step task coherent.\n"
            "Choose a low-risk task and return a structured completion result.\n\n"
            f"Discovery query: {config.discovery.query or 'n/a'}\n"
            f"Discovery filters: {config.discovery.filters.model_dump(exclude_none=True)}\n\n"
            f"Candidate repositories:\n{candidates}\n"
        )
