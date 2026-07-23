# Trials

Throwaway projects for exercising the `harness` end to end. Each fixture lives in its
own subdirectory and is meant to read as a genuine greenfield project. **This guide
stays here, outside the fixtures** — the worker, triage, and validator agents read the
project tree they run on, so any file that describes the trial (the ambiguous ticket,
the expected escalation, "this is a test") would hand the agent the answer. That is why
the fixtures ship no README and `cp -r trials/calc ...` below deliberately excludes this
file.

## `calc/` — integer expression evaluator

A tiny integer expression evaluator (lexer → parser/evaluator → CLI) wired to exercise
the whole loop:

- a serialized ticket chain, so you see dispatch unblock one ticket at a time;
- test-first done-ness (`unittest`), each ticket bringing its own scoped tests;
- one deliberately ambiguous division ticket that should force an escalation.

The fixture ships **no pre-seeded whole-system test suite** — only a scaffold smoke
test — and its `AGENTS.md` tells workers to write tests for their own slice first. A
single whole-system oracle would make the first ticket implement the entire calc to
satisfy it, collapsing the chain; per-ticket tests keep each ticket bounded. The ticket
bodies stay in character: a ticket may say "division semantics are undecided, don't
guess" (a real PM would), but nothing in the tree says "this is the trap we're testing."

### Set up

Run the fixture as its own project, outside the clockwork repo:

```bash
cp -r trials/calc /tmp/calc && cd /tmp/calc
uv tool install .                     # in the clockwork repo: builds `harness` + `tracker`
cp -r skills/* ~/.claude/skills/      # so Claude Code has the design phase
./setup-tracker.sh                          # seed tracker config + the 4 tickets
python -m unittest discover -s tests -t .   # sanity: the scaffold test passes
```

### Run

```bash
harness --once --validate "python -m unittest discover -s tests -t ."   # just the lexer
harness --validate "python -m unittest discover -s tests -t ."          # full loop
```

### What to watch for

- **Serial dispatch** — the chain unblocks one ticket at a time (the parser only becomes
  ready once the lexer is `done`, and so on).
- **The escalation** — the `division` ticket should land in `needs-decision` with a
  `QUESTION:`/`PROPOSED DEFAULT:` comment instead of a guessed implementation. It is
  deliberately subtle: it says only "add `/` per D-2, make sure the rounding is right,"
  and neither it nor D-2 flags an open decision. So it tests whether the worker *notices*
  on its own that D-2 fixes results as ints but never pins how a negative quotient rounds
  (floor vs. truncation) or what divide-by-zero does. A worker that picks floor and moves
  on has failed the trap, not passed it.
- **The validation seam** — `done` only after the tests pass *and* the validator agent
  agrees; a bad implementation bumps `attempts:N` and retries, then auto-escalates at
  `--max-attempts`.
- **The log** — `.scratch/.harness-log.jsonl`, one JSON line per event.

### Then: the design phase

Once the queue has the division escalation, clear it in Claude Code:

```
/design-session
```

It presents the queued questions, patches `docs/design.md` with a new `D-N`, and routes
the tickets back to `ready-for-agent` citing it. Re-run `harness` to drain the rest.
