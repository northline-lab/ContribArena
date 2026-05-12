# ContribArena Architecture

ContribArena is a harness for evaluating autonomous open-source contribution agents. The project is intentionally small and auditable. This document summarizes the core boundaries referenced in CONTRIBUTING.md.

## Harness Boundaries

- Agents decide: agent logic selects targets, proposes patches, and writes trace notes.
- Infrastructure executes: workspace and Docker utilities run commands, apply patches, and perform read-only repository discovery.
- Benchmark artifacts observe: runs produce reproducible artifacts (trace, manifests, diffs, quality gates) for review and comparison.
- Governance controls live writes: owned-live workflows gate any GitHub writes behind governance checks; examples never enable real writes by default.

## High-level Components

- CLI and entrypoint: `src/contribarena/cli.py`, `src/contribarena/__main__.py` provide `contribarena` commands for init, validate, run, and controller.
- Engine and lifecycle: `src/contribarena/engine/` orchestrates runs, context loading, middleware (trace, budget, governance, artifacts), and the local workspace.
- Agent runtime: `src/contribarena/agent/` contains the contributor agent and prompts; the agent uses tools and produces a final result.
- Tools: `src/contribarena/tools/` expose safe, bounded capabilities (e.g., repo metadata, issues, workspace patch/run) used by the agent.
- Models and schemas: `src/contribarena/models/` define Pydantic models for config, tool results, artifacts, governance, and run state.
- Configuration: `src/contribarena/config/` provides schema/loader utilities and starter config generation.
- Tests and examples: `tests/` provide unit coverage; `examples/` contain validated templates for quickstart, search, issue-solving, and owned-live.

## Reproducibility

- Deterministic examples and unit tests should validate behavior without real provider credentials.
- Public examples use placeholders and environment variables only; do not commit secrets or real gateway URLs.

If you change a boundary above, update this file and CONTRIBUTING.md accordingly.
