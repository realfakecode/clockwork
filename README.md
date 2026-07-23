# clockwork

Clockwork runs a headless coding agent through an issue tracker, one ticket at a
time, until only blocked work remains. The **issue tracker is a state machine**: a
ticket's status determines which agent action the loop dispatches next — specify a
thin ticket, implement a ready one, or hand a design question to a human. The
intelligence lives in that tracker state plus the agent prompts, so the loop itself
stays small.

Two phases alternate:

- **Execution** — the `harness` runs unattended, driving a headless agent over ready
  tickets and parking design questions in a queue.
- **Design** — a human runs the `design-session` skill to drain that queue, then
  re-runs the harness.

## The tracker as a state machine

Each ticket has a status; statuses sit in three buckets (`todo`, `active`, `done`)
and a transition map says which moves are legal. The loop reads the current state and
picks the next action from it:

| Ticket state | Bucket | Next action the loop dispatches |
|---|---|---|
| `needs-triage` | todo | **triage agent** specifies it (description, criteria, category) → `ready-for-agent`, or routes to `needs-info` |
| `ready-for-agent` | todo | **worker agent** implements it → `in-progress`, then validation |
| `in-progress` | active | worker has stopped; the loop validates and moves it to `done`, back to `ready-for-agent` (retry), or `needs-decision` |
| `needs-decision` | active | nothing automated — waits for a **human design session**, which routes it back to `ready-for-agent` |
| `done` / `wontfix` | done | terminal |

Ready work always wins over triage, so the frontier never starves. When the
`needs-decision` queue fills up, no ready work remains, or a safety cap trips, the
loop stops and tells you what to do next. See [docs/architecture.md](docs/architecture.md)
for how validation, escalation, and the run log work.

## This repository is meta — three kinds of thing live here

Clockwork drives *other* projects, so agents working in this repo must not confuse
files meant for the **target** project with files that belong to clockwork itself:

- **Clockwork's own code and docs** — the two CLIs and their documentation:
  - `orchestrator/` — the `harness` CLI: the dispatch loop, the agent prompts, and a
    thin wrapper over the `tracker` CLI.
  - `issue_tracker/` — the `tracker` CLI: plain-text issues (markdown + YAML under
    `.scratch/`) with the config-driven state machine described above.
  - `harnesses/` — `PiRpcClient`, an async driver for `pi --mode rpc` that streams a
    run's events as plain dataclasses. One client drives one stateless agent run.
  - `README.md` (this file) and `docs/` — how clockwork works.

  `harness` and `tracker` are **one project**, not external dependencies: a single
  `uv tool install .` builds both from the packages above.

- **`project-template/`** — scaffolding you copy **into a target project** so the
  harness can drive it: the normative design doc, the naming registry, an example
  `AGENTS.md`, and setup instructions. These are templates for *another* repo, not
  clockwork's own config. Start here to point the harness at a new project.

- **`trials/`** — throwaway projects used to exercise the harness end to end. The
  fixtures (e.g. `trials/calc/`) are meant to read as genuine greenfield projects; the
  operator guide that explains what to watch for lives at `trials/README.md`, *outside*
  the fixture, so it can't leak into the agent's view.

- **`skills/`** — Claude Code skills the workflow uses, installed into the project the
  harness runs against (`design-session` for the human phase, plus `issue-tracker`,
  `grilling`, `domain-modeling`). See [skills/README.md](skills/README.md).

## Install and run

```bash
uv tool install .        # builds both `harness` and `tracker`
harness --help           # loop flags
tracker help             # tracker command list; `tracker help <cmd>` for one command
```

Run the harness inside a target project that has a tracker set up (see
[project-template/README.md](project-template/README.md)):

```bash
harness --validate "uv run pytest -q"   # full loop, with a test command as the hard gate
harness --once --validate "..."         # dispatch a single ticket
harness --dry-run                        # show the pick, change nothing
```

To try it against a bundled fixture instead, follow [trials/README.md](trials/README.md).

## Conventions

- Python 3.13, standard library plus `pyyaml`. Match the surrounding module's style
  (dataclasses, `from __future__ import annotations`, docstrings that explain *why*).
- `orchestrator/tracker.py` is the only place that shells out to `tracker`; the loop
  never parses tracker `.md`/YAML files directly.
