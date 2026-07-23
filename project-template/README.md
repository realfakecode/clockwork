# Setting up a target project

Scaffolding to drop into a project you want `clockwork` to drive. Clockwork
operates on the current directory's `.scratch/`, so run it from the target project's
root. These files belong to *that* project, not to clockwork — copy them in and adapt
them.

## 1. Initialize the issue tracker

```bash
issues init
```

The issue tracker's default config already ships the full state machine clockwork needs,
so there is nothing to overwrite. In particular it puts the escalation state
`needs-decision` in the `active` bucket, which keeps it out of `issues ready` (todo
bucket only) so escalated tickets don't re-enter the frontier until a design session
routes them back.

## 2. Design doc

Copy [`docs/design.md`](docs/design.md) to the target's `docs/design.md` (the
`--design` default). It is **normative** — decisions and constraints only, never a
description of the code. Each decision is an addressable `D-N` unit so tickets can cite
it (`per D-1`).

Record decisions you actually weighed; a `**Why:**` line bounds each one's scope so a
terse constraint isn't later stretched to cover an adjacent case. How to write canon in
detail lives in the `domain-modeling` skill.

## 3. Naming registry

Copy [`docs/vocabulary.md`](docs/vocabulary.md) to the target's `docs/vocabulary.md`
(the `--vocab` default). It holds one canonical name per concept so independent agent
runs don't coin two names for the same thing. Triage maintains it; workers and the
validator only read it. It starts empty — triage fills it in as it specifies tickets.

## 4. AGENTS.md

Copy [`AGENTS.md`](AGENTS.md) to the target's root and adapt it. The headless agent
auto-loads it, so use it for project-wide conventions the workers need — how to run the
tests, the build layout, the testing discipline.

## 5. Create tickets

Give each ticket a **category** and at least one concrete **acceptance criterion**
before moving it to `ready-for-agent` (the issue tracker enforces both):

```bash
issues new lexer "Tokenize integer literals" \
  --category enhancement \
  --criterion "tokenizes 0 and multi-digit integers" \
  --criterion "tests pass" \
  --status ready-for-agent
```

Machine-checkable criteria are what let the loop run unattended — otherwise you become
the review bottleneck. Use `--blocked-by <id>` to serialize a chain; a blocked ticket
only becomes ready once its blockers are `done`. File thin tickets as `needs-triage`
and let the triage agent specify them.

## 6. Run

```bash
clockwork --validate "uv run pytest -q"   # full loop, with the test command as the hard gate
clockwork --once --validate "..."         # dispatch a single ticket
clockwork --dry-run                        # show what would be dispatched, change nothing
```

Point `--validate` at the project's test command. The worker implements and stops; the
loop then validates (test gate + an independent validator agent) before it checks off
criteria and resolves. A failure bumps the `attempts:N` label and retries, escalating
at `--max-attempts`. The loop keeps dispatching until the `needs-decision` queue hits
`--queue-threshold`, nothing is ready, or the `--max-dispatches` cap trips. Inspect
`.scratch/.clockwork-log.jsonl` for one JSON line per event.

