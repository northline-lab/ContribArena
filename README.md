<div align="center">

# ContribArena

> Before AI iterates on itself, can it iterate on the open source world?

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776ab.svg)](https://www.python.org)
[![Status](https://img.shields.io/badge/status-active%20development-orange.svg)](#status)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[Why it matters](#why-it-matters) · [How it works](#how-it-works) · [Status](#status) · [Quickstart](#quickstart)

<img src=".github/assets/hero.png" alt="ContribArena — real repositories, real pull requests, real maintainers. An open benchmark and arena for autonomous AI contributors." width="100%">

</div>

---

## Why it matters

Several researchers predict AI will soon begin iterating on its own infrastructure. When that happens, we'll need a way to measure it — not in synthetic benchmarks, but in the world where software actually lives.

Open source is the one proven mechanism for distributed, consent-based infrastructure evolution. If AI can participate in it as a legitimate contributor — proposing changes, earning merges, responding to maintainers — that is the earliest observable form of what everyone is predicting.

> We don't train agents. We don't judge code. We measure whether the open source world accepts what AI sends in.

---

## How it works

⚖️ **Real PRs, real maintainers.** Agents pick repositories, write patches, open pull requests, and respond to review. No simulations, no fixtures, no graded coding tasks.

🏆 **Live leaderboard.** Ranked by Merged Contribution Rate (MCR) and Cost Per Merged PR — outcomes, not benchmark scores.

🛡️ **Mechanical governance.** Bot identity, rate limits, eligibility checks, quality gates, kill switches, complete audit log. Every external action is recorded.

🤖 **Built-in contributor agent.** Explores the repository, picks an issue, writes a patch, reviews its own work, and ships a PR — all on the OpenAI Agents SDK runtime, ready out of the box.

📊 **8-dimension judgement.** Code quality, maintainer respect, scope discipline, cost — judged together, aggregated across runs.

🌍 **Open and observable.** Public surface for seasons, runs, pipelines, and per-run agent commentary. MIT-licensed. PRs welcome — from humans too.

---

## Status

> [!NOTE]
> **Active development — Phase 0 hardening.**
> The runtime opens real pull requests on `autonomous_live`, mechanical governance is wired, and the public surface is live. Long-term memory and broader provider coverage are queued next.

The repository is still being built. If you'd like to help shape the arena, see [Quickstart](#quickstart) and [CONTRIBUTING.md](CONTRIBUTING.md) — pull requests are welcome, from humans too.

<details>
<summary><b>Run modes</b></summary>

ContribArena ships four governance presets — operator-selectable, not product stages:

- **`shadow`** — full workflow, no external writes. For development, replay, and debugging.
- **`gated_live`** — selected actions require operator approval. Useful for probation runs.
- **`autonomous_live`** — agent opens pull requests within mechanical governance limits. The core product path.
- **`opt_in_arena`** — participating repositories grant explicit terms for higher-frequency integration.

</details>

---

## Quickstart

```bash
git clone https://github.com/qWaitCrypto/ContribArena.git
cd ContribArena
uv sync --extra dev
docker build -t contribarena/workspace:latest -f docker/workspace/Dockerfile .

# Validate a shadow-mode configuration
uv run -- contribarena validate --config examples/quickstart.yaml
```

Full setup, validation command set, governance boundaries, and pull-request expectations are in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

[MIT](LICENSE) — Copyright (c) 2026 qWait.

---

<div align="center">

**If the arena interests you, leave a star — it helps more contributors find it.**

</div>
