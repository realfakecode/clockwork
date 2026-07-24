# Architecture

How the `clockwork` loop turns issue-tracker state into agent runs. The
[README](../README.md) covers the state machine and the repo layout; this file is the
mechanics behind one iteration. Code lives in `orchestrator/` (`loop.py`,
`worker.py`, `issues.py`, `formatter.py`).

## One iteration

`Clockwork._step` (`orchestrator/loop.py`) runs serially — one ticket at a time:

1. **Guard the queue.** If the `needs-decision` queue has reached `--queue-threshold`,
   stop and ask for a design session.
2. **Pick work.** Take the first `ready-for-agent` ticket on the unclaimed frontier
   (`issues ready --unclaimed`). If there is none, fall back to a `needs-triage`
   ticket; if there is none of those either, fall back to a **milestone review** of a
   completed effort (below). Dispatch is checked before triage before milestone
   review, so ready work never waits behind specification or a whole-effort review.
3. **Dispatch.** Claim the ticket, mark it `in-progress`, and run one stateless agent
   to a stop with the prompt from `worker.build_worker_prompt`.
4. **Validate and finish** (below).

`run` repeats until a stop condition: queue full, nothing ready, or the
`--max-dispatches` safety cap. `--once` stops after a single dispatch; `--dry-run`
reports the pick and changes nothing.

## The loop holds completion authority, not the worker

The worker implements and **stops** — it never resolves the ticket or checks off its
own criteria. That is what lets an attempt fail. After the worker stops,
`_validate_and_finish` decides the outcome in order:

1. **Did the worker escalate?** If the ticket is already `needs-decision`, the worker
   raised a design question itself. Keep its `QUESTION:`/`PROPOSED DEFAULT:` comment,
   discard its half-finished code, and leave it for the design session.
2. **Test-command gate.** Run `--validate` (if given) as a hard pass/fail gate.
3. **Validator agent.** A fresh, read-only agent (`build_validator_prompt`) judges the
   acceptance criteria the tests can't cover. It returns one of three verdicts:
   - **PASS** — the loop checks off every criterion, resolves the ticket, and commits.
   - **FAIL** — a failed attempt (below).
   - **ESCALATE** — the work looked passable but the worker silently defaulted a
     genuine design decision; route it to `needs-decision` instead of retrying, so the
     next run doesn't just re-guess.

   A missing verdict marker means a malformed judge, not a code failure — the loop
   re-runs the validator once before treating it as a fault.

   A worker that changed no code outside `.scratch/` isn't an automatic fail: a retry
   can land on a ticket already satisfied by earlier work, and a parent ticket can be
   genuinely done once every child is. The loop tells the validator the diff was empty
   instead of short-circuiting itself, so it stays skeptical — a green pre-existing
   test suite proves nothing broke, not that anything was built — but still gets a
   real judgment instead of an automatic failure.

## Failed attempts and escalation

A failed attempt reverts the working tree, records the reason as a ticket comment, and
bumps an `attempts:N` label (`issues.bump_attempts`). Below `--max-attempts` the
ticket returns to `ready-for-agent` with the failure note in its body, so the retry
worker sees why the last try failed. At the cap it auto-escalates to `needs-decision`
("not ambiguous, just hard").

`needs-decision` is the single escalation state, reached three ways: the worker raises
a question, the validator catches a silently-defaulted decision, or a ticket exhausts
its attempts. It lives in the `active` bucket, so `issues ready` (todo bucket only)
won't re-dispatch it until a design session moves it back.

## Triage

When no ready work remains, `_triage` runs an agent (`build_triage_prompt`) to specify
a bare `needs-triage` ticket. Triage fills in the description, acceptance criteria, and
category, then promotes the ticket to `ready-for-agent`. The issue tracker's
`require_category` / `require_acceptance_criteria` invariants reject that promotion
until the work is actually done, so the loop just observes the resulting status —
promoted, routed to `needs-info`, or (if the agent stalled) forced to `needs-info` so
the same thin ticket isn't picked again.

Triage is also the sole writer of the naming registry (`--vocab`): it reads the code to
specify a ticket, so it reuses or registers canonical names and inlines them. Workers
and the validator only read the registry.

## Milestone review and retrospective

Triage refills the frontier one thin ticket at a time. Above it sits a third
frontier-refill altitude that fires only when a whole effort has cleared: dispatch
beats triage beats milestone review, so per-ticket work never waits behind it.

When `_step` finds nothing ready and nothing to triage, it looks for a **completed
map** — a `wayfinding` issue whose direct children are all terminal (`done`/`wontfix`)
and whose child count has grown past its last clean review. That map's build tickets
each passed their own validation and landed, but no per-ticket check ever asked the
question one altitude up: does the assembled work reach the map's **Destination** as a
coherent whole? Two agents answer it.

**The review** (`build_milestone_review_prompt`) runs read-only against the assembled
tree and judges it against the Destination on a deliberately narrow bar — unmet
Destination clause, a broken seam between slices, a contradiction with a `D-N`, or dead
scaffolding a later slice should have removed. Improvements and new scope are explicitly
*not* findings; that is fog for the next wayfinder pass. With `--milestone-file-tickets`
it files up to `--milestone-max-tickets` thin `needs-triage` children for the critical
gaps it finds; without it, the review only posts its findings as a map comment and files
nothing.

**The fixpoint.** Filing tickets and filing nothing are the two outcomes, and "filed
nothing" *is* the signal the effort is done:

- **Filed follow-ups** → not clean. The new children re-open the frontier; ordinary
  dispatch and triage build them, and once they clear the review fires again. This is a
  self-healing loop, so it's generative, never a gate: the landed commits are never
  reverted (they each passed validation), the review only adds work ahead of them.
- **Filed nothing** → the fixpoint. The loop marks the map reviewed at its current child
  count, resets the round counter, and runs the **retrospective** (`build_retrospective_
  prompt`) once. The retrospective is advisory and read-only but for a single summary
  comment: it mines the effort's tickets and run-log slice for escalation clusters,
  repeated `assumption:` defaults, attempt hot-spots, and recurring validator escalates,
  and proposes `D-N` and naming-registry additions for a human design session to ratify.
  It never edits canon itself.

**State lives in three labels on the map**, so the loop stays stateless between
iterations:

- `milestone-round:N` — review rounds since the last clean pass. At
  `--milestone-max-rounds` the effort is judged non-converging: the map gets a
  `milestone-blocked` label and a comment, and the loop stops re-reviewing it (the
  map-level analogue of a ticket's attempt cap).
- `milestone-reviewed:N` — child count at the last clean review. The review re-fires
  only once the frontier grows past it, so a settled map stays quiet while fix tickets or
  graduated fog reopen it.
- `milestone-blocked` — set by the round cap; excludes the map from review until a human
  clears it.

Everything the phase writes is `.scratch/` state (labels, comments, filed tickets),
committed by `_commit_milestone` on every branch so the tree is clean before the next
dispatch — the same invariant triage upholds. `--no-milestone-review` disables the whole
rung, restoring the plain dispatch→triage→halt loop.

## Clean tree between dispatches

Everything the worker leaves in the tree is committed wholesale when a ticket passes,
so each phase ends with a clean tree: one commit per resolved ticket, one per triage
run, and a revert on every non-accept exit. `.scratch/` is excluded from the revert —
it *is* the issue-tracker database, so the loop's own comments and labels survive. A stray
uncommitted diff would otherwise pollute the next worker's and the read-only
validator's `git diff` / `git status` view.

## The run log

Every dispatch, triage, validation, commit, escalation, retry, and halt appends one
JSON line to `.scratch/.clockwork-log.jsonl` (and echoes to stdout). It is the primary
instrument for a run — breakdowns show up here before they show up in code.

## Design canon and the naming registry

Clockwork reads two documents in the target project, both addressed by CLI flag:

- **`--design` (default `docs/design.md`)** — normative decisions, each an addressable
  `D-N` unit that workers, triage, and the validator cite. Design sessions patch it.
- **`--vocab` (default `docs/vocabulary.md`)** — one canonical name per concept, so
  independent stateless runs don't coin two names for the same thing and build it
  twice.

Both defaults resolve against the target project's working directory. The templates
for them live in [`project-template/docs/`](../project-template/docs/); how to write
canon lives in the `domain-modeling` skill.

## Not currently planned

- Parallel dispatch. Keeping things serial to avoid race conditions and the code
  that would need to resolve them. Also harder to follow as a human.
