# clockwork

An unattended **ticket→implementation loop**: a simple loop dispatches ready tickets
to a headless agent one at a time, validates the result, and escalates design
questions to a queue for a batched human design session. The intelligence lives in
**tracker state + prompt instructions**, not in the loop.

The workflow alternates two phases:
- **Execution** — `harness` (this repo's `orchestrator/`) drives a headless agent over `tracker` state.
- **Design** — the `design-session` skill (interactive) drains the escalation queue and patches canon.

## Packages

- `orchestrator/` — the worker loop. `cli.py` (`harness` entry point) → `loop.py`
  (dispatch algorithm + seam log) → `worker.py` (triage/worker/validator prompts +
  drive-to-stop) + `tracker.py` (subprocess wrapper over the `tracker` CLI) + `formatter.py`
  (event pretty-printer, shared with `main.py`).
- `issue_tracker/` — the `tracker` CLI: plain-text issues as markdown+YAML under `.scratch/`,
  config-driven statuses/transitions, dependency resolution, acceptance-criteria checklists.
  Behavior is entirely config-driven, and the tracker's **default** config already ships the
  full agent workflow this harness needs — the `needs-triage`/`ready-for-agent` triage states,
  the `needs-decision` escalation state (active bucket), and the `require_category` /
  `require_acceptance_criteria` invariants — so a project just runs `tracker init`; no
  per-project `.tracker.yaml` tailoring required.
- `harnesses/` — `PiRpcClient`, an async `pi --mode rpc` driver that streams events as
  harness-agnostic dataclasses. One client = one stateless agent run.
- `skills/` — installable, workflow-tailored skills: `design-session` (the batched design
  phase) plus repo-local copies of `issue-tracker`, `grilling`, `domain-modeling`.
- `main.py` — a smoke-test demo for `PiRpcClient` (not part of the loop).
- `trial-calc/` — a self-contained greenfield trial project (move it out to run it).
- `docs/tracker-config.md` — the tailored `.tracker.yaml` + trial setup.

## Core design

- **The loop is simple.** It shells out to the real `tracker` CLI and decides everything from
  the status observed after each run. Serial dispatch only.
- **The loop holds done-authority, not the worker.** The worker implements and STOPS — it
  does not `resolve` or check off criteria. After it stops the loop validates in two stages
  (hard `--validate` test-command gate, then an independent validator `pi` agent) and only
  then checks off criteria + resolves. This is what makes an attempt able to *fail*. The
  validator agent has three outcomes, not two: PASS, FAIL (→ worker retry), and ESCALATE (→
  `needs-decision`) — the last catches the worker *silently defaulting* a genuine design
  decision it should have raised, judged on the same routine-vs-genuine bar the worker uses.
  On a
  pass the loop also makes **one commit per ticket** (`git add -A && git commit`) so the tree
  is clean before the next dispatch — an uncommitted diff otherwise pollutes the next
  worker's and the read-only validator's `git diff`/`git status` view.
- **Triage is a loop phase, not just a status.** `needs-triage` bare tickets sit in the
  **todo** bucket; when no `ready-for-agent` work remains, the loop runs a triage `pi` agent
  (`build_triage_prompt`) that fills in description + acceptance criteria + category and
  promotes the ticket to `ready-for-agent`. The tracker's `require_category` /
  `require_acceptance_criteria` invariants *reject* that promotion until triage has really
  done its job, so the loop just observes the resulting status (promoted, routed to
  `needs-info`, or forced to `needs-info` if the agent stalled). Dispatch always wins over
  triage, so ready work never starves.
- **Escalation is a first-class tracker state.** `needs-decision` lives in the **active**
  bucket so `tracker ready` (todo bucket only) never re-dispatches it. Every escalation
  carries a `QUESTION:/PROPOSED DEFAULT:`. Escalations are deliberately **unstructured** for
  now — collect ~20 real ones before designing a schema.
- **The attempt counter is in-tracker**, as an `attempts:N` label. The loop bumps it via
  `tracker edit --remove-label/--add-label`; auto-escalates at `--max-attempts`.
- **The seam log is the instrument.** Every dispatch/triage/validate/commit/done/escalate/
  retry/halt is one JSON line in `.scratch/.harness-log.jsonl`. Breakdowns show up here
  before they show up in code — keep it fed.
- **Canon is normative and addressable.** The design doc (`--design`, default `docs/design.md`)
  holds decisions/constraints only (no code description), each a `D-N` unit. Design sessions
  patch it and inline decisions into tickets *as derived, with a `per D-N` citation*. How to
  write canon (the "what did we weigh?" admission test, the entry format) lives in the
  `domain-modeling` skill, not in `design.md`, so the file stays lean and the guidance can't drift.
- **The naming registry is convention, not canon.** `docs/vocabulary.md` (`--vocab`) holds one
  canonical name per concept — with a `Not:` synonym list — so independent stateless runs don't
  coin `zip`/`zap` for the same thing and build it twice. **Triage** is its sole writer (it reads
  the code to spec a ticket, so it reuses/registers names and inlines them into the ticket);
  workers and the validator only read it. It has the *opposite* admission test to canon (any
  collidable concept vs. only-what-was-weighed), which is why it's a separate file; a term
  graduates *up* into a `D-N` only if its definition was actually contested.

## Gotchas

- `config.load_config` does a **shallow** `cfg.update(data)`, so a project's `.tracker.yaml`
  must contain the **full** `statuses` and `transitions` maps, not a delta.
- `done` is terminal in the transition graph. The loop never self-resolves-then-reopens
  because the worker leaves the ticket `in-progress` and the loop resolves only on a pass.
- The harness operates on the **current directory's** `.scratch/` — no project-path plumbing.

## Commands

```bash
uv tool install .           # ships BOTH `harness` and `tracker`
uv run python -c "import orchestrator.cli"    # import-check without installing
harness --help              # loop flags
tracker help [<cmd>]        # discoverable help — command list, or one command's flags
```

Run the loop inside a project with a tailored `.scratch/`:

```bash
harness --validate "python -m unittest discover -s tests -t ."   # full loop with a test gate
harness --once --validate "..."                                   # dispatch one ticket
harness --dry-run                                                 # show the pick, mutate nothing
```

There is no unit-test suite yet; verification is by running the loop against `trial-calc/`
(see `docs/trial-calc.md`) and inspecting `.scratch/.harness-log.jsonl`.

## Conventions

- Python 3.13, stdlib + `pyyaml` only. Match the surrounding module's style (dataclasses,
  `from __future__ import annotations`, terse docstrings that explain *why*).
- `orchestrator.tracker` is the only thing that shells out to `tracker`; the loop never
  parses `.md`/YAML directly.

## Deferred (not built yet)

Scheduler/cron auto-dispatch, parallel dispatch, a structured escalation schema.
