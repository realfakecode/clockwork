# Running the trial-calc trial

Operator guide for the `trial-calc/` greenfield trial: a tiny integer expression
evaluator wired to exercise the full `harness` loop end to end — a serialized ticket
chain (lexer → parser/evaluator → CLI), test-first (`unittest`) done-ness where each
ticket brings its own scoped tests, and one deliberately ambiguous division ticket that
should force an escalation into `needs-decision`.

The trial ships **no pre-seeded test suite** — only a scaffold smoke test — and the
project `AGENTS.md` (which `pi` auto-loads) tells workers to write tests for their own
slice first. This is
deliberate: a pre-seeded whole-system oracle ("make the golden runner pass") makes the
first ticket implement the entire calc to satisfy it, collapsing the chain and leaving
the later tickets as confusing no-ops. Per-ticket tests keep each ticket's work bounded.

> **Keep this guide in the harness repo — never copy it into the trial project
> directory.** The worker, triage, and validator agents read the project tree they run
> on. Any file that describes the trial — the ambiguous ticket, the expected
> escalation, "this is a test" — spoils it by handing the agent the answer. The trial
> project must read as a genuine greenfield calc project. That is why this guide lives
> here and `trial-calc/` ships no README. For the same reason, `setup-tracker.sh` and
> the ticket bodies stay in-character: a ticket may legitimately say "division
> semantics are undecided, don't guess" (a real PM would), but nothing in the tree may
> say "and this is the trap we're testing."

## Set up

The harness operates on the current directory's `.scratch/`, so run the trial as its
own project, outside the harness repo:

```bash
cp -r trial-calc /tmp/calc && cd /tmp/calc
uv tool install .                     # in the harness repo: ships `harness` + `tracker`
cp -r skills/* ~/.claude/skills/      # so Claude Code has the design phase
./setup-tracker.sh                          # seed tracker config + the 4 tickets
python -m unittest discover -s tests -t .   # sanity: the scaffold test passes (runner works)
```

## Run

```bash
harness --once --validate "python -m unittest discover -s tests -t ."   # dispatch just the lexer
harness --validate "python -m unittest discover -s tests -t ."          # full loop
```

## What to watch for

- **Serial dispatch** — the chain unblocks one ticket at a time (parser only becomes
  `ready` once the lexer is `done`, etc.).
- **The escalation** — the `division` ticket should land in `needs-decision` with a
  `QUESTION:/PROPOSED DEFAULT:` comment instead of a guessed implementation. The ticket is
  deliberately *subtle*: it says only "add `/` per D-2, make sure the rounding is right",
  and neither it nor D-2 flags an open decision. So this tests whether the worker *notices*
  on its own that D-2 fixes results as ints but never pins how a negative quotient rounds
  (floor vs. truncation toward zero) or what divide-by-zero does. A worker that picks floor
  and moves on has *failed* the trap, not passed it. The design session settles the
  semantics into a new `D-N`.
- **The validation seam** — `done` only after the test suite passes *and* the validator
  agent agrees; a bad implementation bumps `attempts:N` and retries, then auto-escalates
  at `--max-attempts`.
- **The log** — `.scratch/.harness-log.jsonl`, one JSON line per event.

## Then: the design phase

Once the queue has the division escalation, clear it in Claude Code:

```
/design-session
```

It presents the queued questions, patches `docs/design.md` with a new `D-N`, and routes
the tickets back to `ready-for-agent` citing it. Re-run `harness` to drain the rest.
