---
name: design-session
description: Guided Q&A to drain the `needs-decision` escalation queue, unblocking its tickets
disable-model-invocation: true
---

# design-session

The execution phase is a dumb loop: `clockwork` dispatches `ready-for-agent` tickets to
a headless worker until design questions pile up in the `needs-decision` queue, then it
halts. This skill is the **matched human phase** — a guided Q&A that drains that queue.
Work the whole pile in one sitting; answering escalations one at a time as they trickle in
puts you back on the critical path of every ticket, which defeats the point.

The session is a grilling interview, not a chat. Its output is a *patch* to canon plus
ticket state changes. An answer given only in conversation is the rot vector — agents keep
reading stale design. Don't let an answer *be* the resolution; the resolution is the
emitted patch, made as each question lands so the queue ends empty and unblocked.

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

## Resolve — a grounded grilling, one question at a time

Run the queue as a **grilling** session: put the open questions to the user one at a time,
waiting for each answer before moving on, and walk down dependencies between decisions in
order. For each question, look up any *fact* the environment can answer (filesystem, the
tickets, the design doc) rather than asking, and offer a recommendation — usually the
worker's proposed default, which exists to keep the session short. The *decision* is the
user's; put it to them and wait.

Reach for **domain-modeling** when a question is really a terminology or boundary dispute —
pin the term before deciding the behaviour, and let it place the result in the right home
(canon vs. the naming registry).

## Emit — resolve each decision as it lands

The moment a question is settled, do both halves before moving to the next. Neither is
optional; leaving them for the end is the step you skip when tired.

1. **Patch canon.** Append the decision to the design doc as an addressable `D-N` entry —
   normative constraint plus the `Why:` that pins its scope. `domain-modeling` owns the
   format and the admission test (canon holds only weighed decisions; bare conventions go
   to the naming registry or code). Reuse the existing numbering; edit an entry in place
   when a new decision changes it, never renumber.

2. **Update the ticket.** Inline the decision as derived, with a citation — the worker
   reads tickets, not diffs, and the citation makes staleness detectable and sets
   precedence when copy and canon disagree.

   ```bash
   issues comment <id> --body "DECIDED (per D-<N>): <the derived instruction for this ticket>"
   issues status <id> ready-for-agent
   ```

   `needs-decision → ready-for-agent` is a legal transition; category and acceptance
   criteria were set when the ticket was first routed, so the move passes the tracker's
   invariants. If the decision puts the question out of scope, route to `wontfix` (or
   `ready-for-human`) instead, with the same citation. If it changes what "done" means,
   update the acceptance criteria too (`issues criteria <id> --add/--remove ...`) so
   clockwork validates the right thing on the next attempt.

## Exit criterion

Done means **both**: the `needs-decision` queue is empty **and** every decision is written
into the design doc as a `D-N` entry with the tickets citing it. Verify:

```bash
issues list --status needs-decision    # must be empty
```

Then tell the user clockwork can be re-run (`clockwork`).
