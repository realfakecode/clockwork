---
name: design-session
description: Batched human design phase for when the `needs-decision` escalation queue is non-empty
disable-model-invocation: true
---

# design-session

The execution phase is a dumb loop: `clockwork` dispatches `ready-for-agent` tickets to
a headless worker until design questions pile up in the `needs-decision` queue, then it
halts. This skill is the **matched human phase** — a batched design session that drains
that queue. Do the whole pile at once; answering escalations one at a time as they trickle
in puts you back on the critical path of every ticket, which defeats the point.

This is a **compiler**, not a chat: its output is a *patch* to canon plus ticket state
changes. An answer given only in conversation is the rot vector — agents keep reading stale
design. Don't let an answer *be* the resolution; the resolution is the emitted patch.

## Entry

The queue is non-empty:

```bash
issues list --status needs-decision --json
```

If it's empty, there's nothing to do — say so and stop.

## Load the pile

For each escalated ticket, read the `QUESTION:` / `PROPOSED DEFAULT:` the worker left in
its comments, plus its acceptance criteria and body:

```bash
issues show <id> --json     # body carries the ## Comments with QUESTION/PROPOSED DEFAULT
```

Also read the canonical design doc (default `docs/design.md`) so you resolve against
existing decisions, not from scratch. It is **normative** — decisions and constraints only,
each addressable as `D-N`.

## Resolve — present the whole pile at once

Present every queued question to the user **together**, each with the worker's proposed
default and your recommendation. Batching is the point; do not drip them out.

- Use **grilling** to stress-test a decision the user is unsure about — walk the branches
  one at a time until the decision is sharp.
- Use **domain-modeling** when a question is really a terminology or boundary dispute —
  pin the term before deciding the behaviour.
- Prefer the worker's proposed default unless there's a reason to override; the defaults
  exist to keep this session short.

## Emit — the resolution is a patch (per decision)

For **each** resolved question, do both halves. Neither is optional.

1. **Patch canon.** Append a new addressable decision to the design doc — normative,
   no code description. State the constraint, and record the **rationale that pins
   its scope**: *why* this, so the terse constraint can't later be over-extended to
   mean something adjacent but unintended. Size the rationale to the decision's
   weight — a genuinely one-line constraint gets a one-line why; a contested choice
   carries the alternative it beat. Record the reason itself, never a meta-tag
   *about* the decision (status, confidence, "how firmly held"): presence in canon
   already means it was weighed, so a confidence field is redundant and only invites
   hedging and relitigation.

   ```markdown
   ## D-<N>: <title>

   <the constraint the implementation must satisfy>

   **Why:** <the reason, which bounds what the constraint claims — plus the rejected
   alternative if the choice was contested>
   ```

   Admission test: an entry must be able to answer *"what did we weigh?"* If it
   can't, it's a convention wearing a decision's clothes — it belongs in code or
   `CLAUDE.md`, not canon (uncanonized, it stays cheaply overturnable). Reuse the
   existing `D-N` numbering; never renumber old entries (tickets cite them by
   number). When a new decision changes an old one, edit that entry in place — the
   number is the stable address, its content can move.

2. **Update the ticket.** Inline the decision **as derived, with a citation** — this is
   deliberate denormalization: the worker reads tickets, not diffs, and a citation makes
   staleness detectable and gives a precedence rule when copy and canon disagree.

   ```bash
   issues comment <id> --body "DECIDED (per D-<N>): <the derived instruction for this ticket>"
   issues status <id> ready-for-agent
   ```

   A `needs-decision → ready-for-agent` move is a legal transition. Category and
   acceptance criteria are already set from when the ticket was first routed, so the move
   passes the issue tracker's invariants. If a decision means a question is out of scope, route
   it to `wontfix` (or `ready-for-human`) instead, with the same citation.

   If a decision changes what "done" means, update the acceptance criteria too
   (`issues criteria <id> --add/--remove ...`) so clockwork's validation step checks the
   right thing on the next attempt.

## Exit criterion (not just entry)

Done means **both**: the `needs-decision` queue is empty **and** every decision is written
into the design doc as a `D-N` entry with the tickets citing it. Emitting the patch + ticket
updates *is* the done state. Formalization is the step you skip when tired — that's exactly
the rot clockwork exists to prevent, so don't stop until canon is patched. Verify:

```bash
issues list --status needs-decision    # must be empty
```

Then tell the user clockwork can be re-run (`clockwork`).
