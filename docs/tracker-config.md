# Trial-project setup

The harness operates on the **current directory's** `.scratch/`. To stand up a trial
project, initialize the tracker, drop in the tailored config below, and add a starter
design doc.

## 1. Initialize the tracker

```bash
tracker init
```

That's the whole step — the tracker's **default** config already ships the full agent
workflow this harness needs, so there is nothing to overwrite. The default includes the
first-class escalation state `needs-decision` in the **active** bucket, which keeps it out
of `tracker ready` (which returns only the `todo` bucket) so escalated tickets never
re-enter the dispatch frontier until a design session routes them back. Concretely, the
default provides:

- `needs-decision` in `statuses.active`.
- `ready-for-agent` and `in-progress` → `needs-decision` transitions (the two states a
  worker escalates from; the loop also escalates a ready ticket that hit the attempt cap).
- `needs-decision` → `[ready-for-agent, ready-for-human, wontfix]` — how a design session
  re-queues a resolved question.

(If you *do* hand-edit `.scratch/.tracker.yaml`, note that `config.load_config` does a
**shallow** `cfg.update(data)`: any `statuses`/`transitions` map you supply replaces the
default wholesale, so write the full map, not a delta.)

## 2. Starter design doc

Create `docs/design.md` (the `--design` default). It is **normative** — decisions and
constraints only, never descriptions of the code. Each decision is an addressable `D-N`
unit so tickets can cite it (`per D-1`) and staleness is detectable.

Record only genuinely *weighed* decisions: presence in canon means "we weighed this," so
there is no confidence/status field — a bare convention or arbitrary default belongs in
code or `CLAUDE.md`, not here (admission test: *what did we weigh?*). Each entry carries a
`**Why:**` that bounds its scope, so the terse constraint can't be repurposed into an
adjacent, unweighed claim; size it to the decision's weight. Never renumber entries
(tickets cite by number); when a decision changes, edit it in place.

```markdown
# <Project> — Design decisions

Canonical, normative. Decisions and constraints only — no code description. Each entry is
addressable as `D-N`; workers cite these, design sessions add to and edit them.

## D-1: <title>

<the constraint the implementation must satisfy>

**Why:** <the reason, which bounds what the constraint may be read to mean — plus the
rejected alternative if the choice was contested>
```

## 3. Create tickets

```bash
tracker new lexer "Tokenize integer literals" \
  --category enhancement \
  --criterion "tokenizes 0 and multi-digit integers" \
  --criterion "tests pass" \
  --status ready-for-agent
```

Give each ticket a **category** and at least one **acceptance criterion** before moving it
to `ready-for-agent` (the tracker enforces both). Machine-checkable criteria are what let
the loop run unattended — otherwise you become the review bottleneck.

## 4. Install the skills

The design phase runs in Claude Code via the `design-session` skill. Install the repo's
skills so Claude Code picks them up in the trial project:

```bash
# from the harness repo:
cp -r skills/* ~/.claude/skills/        # user-global, or:
cp -r skills/* /path/to/trial/.claude/skills/   # project-local
```

## 5. Run

The worker implements and stops — it does **not** judge its own done-ness. After it stops
the loop validates in two stages before accepting: a hard **test-command gate** (`--validate`)
then an independent **validator agent** that judges the acceptance criteria tests don't
cover. Only if both pass does the loop check off the criteria and `resolve` the ticket; a
failure bumps the `attempts:N` label (auto-escalating at `--max-attempts`) and feeds the
reason back into the next attempt via a ticket comment.

```bash
harness --validate "uv run pytest -q"   # full loop with the test gate wired up
harness --once --validate "make test"   # dispatch a single ticket
harness --dry-run                        # show what would be dispatched, mutate nothing
```

Point `--validate` at the project's golden-file / test command. If you omit it, the test
gate is skipped and only the validator agent judges the work — set it for unattended runs.

The loop keeps dispatching until no workable ticket remains: the `needs-decision` queue
hits `--queue-threshold`, nothing is `ready-for-agent`, or the `--max-dispatches` safety
cap trips.

Inspect `.scratch/.harness-log.jsonl` for one JSON line per
start / dispatch / validate / done / escalate / retry / halt.
