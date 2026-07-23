"""Worker + validator prompts and the drive-to-stop helper.

Two prompts, one driver:
- `build_worker_prompt` tells the headless `pi` agent how to implement, when to
  assume, and when to hard-block. Crucially it does NOT let the worker declare its
  own done-ness — completion authority lives in the loop's validation step.
- `build_validator_prompt` runs a fresh, read-only `pi` agent as a strict judge of
  the acceptance criteria the test command can't cover.
"""

from __future__ import annotations

from harnesses import (
    AgentMessageEvent,
    AgentSettledEvent,
    PiRpcClient,
    SessionEndEvent,
    TurnEndEvent,
)

from .formatter import EventFormatter

# The validator must end its reply with one of these markers.
VERDICT_PASS = "VALIDATION: PASS"
VERDICT_FAIL = "VALIDATION: FAIL"
VERDICT_ESCALATE = "VALIDATION: ESCALATE"


def render_criteria(criteria: list[dict]) -> str:
    if not criteria:
        return "(none — nothing machine-checkable; use your judgement)"
    lines = []
    for i, item in enumerate(criteria):
        box = "x" if item.get("done") else " "
        lines.append(f"{i}. [{box}] {item['text']}")
    return "\n".join(lines)


def render_children(children: list[dict]) -> str:
    if not children:
        return "(no child tickets)"
    return "\n".join(
        f"- #{c['id']} [{c.get('status', '?')}] {c.get('title', '')}".rstrip()
        for c in children
    )


def build_worker_prompt(issue: dict, design_path: str, vocab_path: str) -> str:
    """Per-ticket worker prompt from `issues show <id> --json`."""
    ticket_id = issue["id"]
    title = issue.get("title", "")
    body = (issue.get("body") or "").strip()
    criteria = render_criteria(issue.get("acceptance_criteria") or [])

    return f"""\
You are an unattended implementation worker. Implement exactly one ticket, end to
end. Do not ask the human anything — either proceed with a logged default or
hard-block via the issue tracker (rules below).

# Your ticket — #{ticket_id}: {title}

{body}

## Acceptance criteria
{criteria}

# Canonical design doc

Consult `{design_path}` before starting. It is NORMATIVE — decisions and
constraints only, addressable as `D-N`. When it answers a question, follow it and
cite the unit. Each `D-N` carries a **Why:** that bounds its scope — apply the
decision within that reason; do not stretch the bare constraint to cover an adjacent
case it wasn't weighed for. If it is silent, fall through to the decision rules
below.

# Naming registry

Consult `{vocab_path}` — the canonical naming registry — before you name anything.
It exists so separate runs don't coin different names for one concept (`zip` vs
`zap`) and build it twice. Read it through by concept before naming anything —
searching turns up only the term you already have in mind, which is the one you're
about to get wrong, so the `Not:` lists that would redirect you only help if you
read past them. Your ticket's concepts should already carry canonical terms from
triage; use them verbatim. Before coining a NEW name for an incidental concept,
check the registry and reuse the canonical term if it's there. You may NOT edit the
registry — triage owns it. If you hit a recurring concept it's missing, note it
rather than silently coining: `issues comment {ticket_id} --body "assumption:
<concept> is unregistered; used <name>"` for triage or a design session to fold in.

# Workflow rules

1. Implement to satisfy the acceptance criteria, running the tests as you go.
   Touch only what this ticket needs — do not wander into other tickets.
   Everything left in the repo tree is committed wholesale when the ticket passes,
   so keep scratch and experimental files OUT of the repo: write throwaway scripts,
   probes, and notes to a directory outside it (e.g. a system temp dir), not into
   the working tree.

2. ROUTINE decision — a sensible default exists that any careful implementer would
   pick and the human is unlikely to have an opinion on (naming, file/module layout,
   the error type for bad input, how an edge case is handled): proceed with the
   default and log it — `issues comment {ticket_id} --body "assumption: <what and why>"`.

3. GENUINE DESIGN DECISION — escalate instead of guessing, when EITHER:
   - the ticket or a `D-N` explicitly marks a point as open ("do not guess",
     "deliberately unspecified", "left open") — a direct instruction to escalate,
     not a default to pick; OR
   - intent is genuinely unclear: careful implementers would build materially
     different things and the human would have a real preference between them (not
     just a different mechanism to the same end).
   Then STOP. Do not guess:
     `issues status {ticket_id} needs-decision`
     `issues comment {ticket_id} --body "QUESTION: <the decision> PROPOSED DEFAULT: <your best call>"`
   Every escalation MUST carry a proposed default. Then end your turn.

   Escalate what is undecided or contested, not merely what is user-visible: a
   reasonable default nobody would object to stays in rule 2, even if observable.

4. When you believe every acceptance criterion is met, run the tests one final
   time to confirm, delete any temporary files you created in the working tree,
   then STOP. Do NOT resolve the ticket, mark it done, or check off criteria — an
   independent validation step reviews your work and closes the ticket. Leave it
   in-progress.

Begin now.
"""


def build_triage_prompt(issue: dict, design_path: str, vocab_path: str) -> str:
    """Turn a bare `needs-triage` ticket into an agent-ready one. The triage agent
    fills in the description + acceptance criteria + category, then promotes the
    ticket to `ready-for-agent`. It does NOT implement anything — promotion (or an
    escalation to `needs-info`) is the only outcome the loop acts on."""
    ticket_id = issue["id"]
    title = issue.get("title", "")
    body = (issue.get("body") or "").strip()
    criteria = render_criteria(issue.get("acceptance_criteria") or [])

    return f"""\
You are a triage agent. A ticket has been filed but is too thin to hand to an
implementation worker. Your job is to specify it: turn it into a detailed,
implementable description with concrete, checkable acceptance criteria — then
promote it. Do NOT write or change any project code (the naming registry below is
the one exception); triage only fills the ticket in.

# Your ticket — #{ticket_id}: {title}

{body or "(no description yet)"}

## Current acceptance criteria
{criteria}

# Canonical design doc

Consult `{design_path}` before triaging. It is NORMATIVE — decisions and
constraints only, addressable as `D-N`. Anchor the ticket to it: when it answers a
scope or behaviour question, follow it and cite the unit — within the scope its
**Why:** bounds, not the bare constraint stretched to fit. Read the surrounding code
to ground the description in what already exists.

# Naming registry

Also consult `{vocab_path}` — the canonical naming registry. It exists so
independent worker runs don't coin different names (`zip` vs `zap`) for one concept
and build it twice. You are its only writer, so as you specify this ticket:

- REUSE — when a concept is already in the registry, use that canonical term in the
  description and criteria; do not introduce a synonym.
- REGISTER — reading the code to specify this ticket, when you find or introduce a
  concept that recurs but is missing, add it: canonical term, a one-line meaning,
  and a `Not:` list of the synonyms it should displace.

      ### <canonical term>

      <one-line meaning>
      **Not:** <synonym>, <synonym>

  Editing this file is the ONE change to the repo tree triage may make. Keep it lean
  — register concepts two tickets could collide on, not every noun.
- INLINE — name this ticket's concepts with their canonical terms throughout the
  body and criteria, so the worker inherits the vocabulary and never has to guess it.

# What to produce

1. Write a detailed, self-contained description a worker could implement without
   guessing intent:
     `issues edit {ticket_id} --body "<the full description>"`

2. Add concrete, independently checkable acceptance criteria (repeat --add):
     `issues criteria {ticket_id} --add "<criterion>"`
   Each criterion should be objectively verifiable (a behaviour, an output, a
   file/API shape) — not "works correctly".

3. Set a category (enhancement / bug):
     `issues edit {ticket_id} --category <category>`

4. When the ticket is fully specified, promote it:
     `issues status {ticket_id} ready-for-agent`
   This is REJECTED until a description, at least one acceptance criterion, and a
   category are all present — that rejection is the definition of "not done yet".
   Fix what it reports and retry. A successful promotion ends triage — then STOP.

# When you cannot triage

If the ticket needs a human product/scope decision you cannot responsibly make
(genuinely ambiguous intent, not just an implementation detail), do NOT guess.
Escalate instead of promoting:
  `issues comment {ticket_id} --body "QUESTION: <the decision> PROPOSED DEFAULT: <your best call>"`
  `issues status {ticket_id} needs-info`
Then STOP.

Begin now.
"""


def build_validator_prompt(
    issue: dict, design_path: str, vocab_path: str, test_summary: str, empty_diff: bool = False
) -> str:
    """Read-only judge of a just-finished implementation against the criteria.

    `empty_diff` means the worker stopped without changing anything outside
    `.scratch/`. That's not an automatic failure — a retry can land on a ticket
    already satisfied by earlier work, or a parent ticket can be done once every
    child is — so it's folded into the judging instructions as a skepticism flag
    rather than handled as a separate gate."""
    ticket_id = issue["id"]
    title = issue.get("title", "")
    body = (issue.get("body") or "").strip()
    criteria = render_criteria(issue.get("acceptance_criteria") or [])
    empty_diff_check = f"""
0. EMPTY DIFF. The worker made NO changes outside `.scratch/`. This is only correct
   if every acceptance criterion is ALREADY genuinely satisfied by the code as it
   stands — e.g. a prior attempt or a different ticket already did this work, or
   (for a parent ticket) every child ticket is done (check with `issues children
   {ticket_id}` / `issues show <child-id>`). A green test-command result proves
   nothing here — it just means nothing broke, not that anything was built. Verify
   directly by reading the code and, for parent tickets, the children's status. If
   you cannot fully confirm every criterion is already met this way, FAIL.
""" if empty_diff else ""

    return f"""\
You are a STRICT, independent validator. A worker just implemented ticket
#{ticket_id} and stopped. Your job is to decide whether every acceptance criterion
is genuinely satisfied. You are READ-ONLY: do not modify any file, do not run the
worker's fixups, only read-only `issues` commands. Inspect the working tree
(`git diff`, `git status`, read files, run read-only checks) to judge the work.

# Ticket #{ticket_id}: {title}

{body}

## Acceptance criteria
{criteria}

## Normative design doc
`{design_path}` — the work must not contradict any `D-N` decision there, nor stretch
one past the scope its **Why:** bounds.

## Naming registry
`{vocab_path}` — the canonical name for each concept. Used in the correctness check
below to catch a concept reimplemented under a new synonym.

## Test-command result (already run by clockwork)
{test_summary}

# Judge, in order
{empty_diff_check}
1. CORRECTNESS. Judge the criteria the test command does not fully cover (behavioural
   gaps, contradictions with the design doc, criteria with no corresponding test). Be
   skeptical: a passing test suite is necessary but not sufficient. If the work does not
   genuinely satisfy every criterion, FAIL — this takes precedence over everything below.
   Also flag clear naming drift: a concept implemented under a new name when the registry
   (or existing code) already has a canonical one. Judge only clear cases — a genuinely
   novel concept with no registry entry is fine, not a failure.

2. SILENTLY-DEFAULTED DECISIONS. If the work would otherwise pass, make one more check.
   The worker was required to escalate (not guess) any GENUINE DESIGN DECISION and to
   only proceed on ROUTINE ones. Using the SAME bar, review its `assumption:` comments
   above and the diff for a genuine decision it resolved by defaulting instead of raising:
   - GENUINE (should have escalated): the ticket or a `D-N` explicitly marks the point
     open, OR intent is genuinely unclear — careful implementers would build materially
     different things and a human would have a real preference between them.
   - ROUTINE (fine to default, do NOT flag): a sensible choice any careful implementer
     would make and the human is unlikely to have an opinion on (naming, layout, the
     error type for bad input, edge-case handling).
   Flag ONLY a clear genuine decision; when in doubt it is ROUTINE. If you find one,
   ESCALATE instead of passing.

# Verdict

End your reply with EXACTLY one line, nothing after it — exactly one of:
  {VERDICT_PASS}
  {VERDICT_FAIL} — <one-sentence reason>
  {VERDICT_ESCALATE} — <the decision the worker defaulted, and the choice it made>
"""


def build_milestone_review_prompt(
    map_issue: dict,
    children: list[dict],
    design_path: str,
    vocab_path: str,
    *,
    max_tickets: int,
    can_file_tickets: bool,
) -> str:
    """Whole-effort review, one altitude above the per-ticket validator: does the
    assembled work actually reach the map's Destination, as a coherent whole? It
    fires when a wayfinding map's charted frontier has fully cleared. With
    `can_file_tickets`, its teeth are to file thin `needs-triage` follow-ups for
    CRITICAL gaps (the self-healing loop) — filing nothing is the fixpoint that
    unlocks the retrospective; otherwise it only reports. Read-only on code either
    way."""
    map_id = map_issue["id"]
    feature = map_issue.get("feature", "")
    title = map_issue.get("title", "")
    body = (map_issue.get("body") or "").strip()
    kids = render_children(children)

    if can_file_tickets:
        teeth = f"""\
# Your teeth: file follow-ups for CRITICAL gaps only

For each CRITICAL finding — up to {max_tickets} this pass — file a thin follow-up as a
child of this map, for the loop to specify and build:

    issues new {feature} "<slice title>" --parent {map_id} --status needs-triage --body "<one line: the gap and where it is>"

- Keep it thin (title + one-line body); the loop's triage agent fills it in.
- Wire a blocking edge (`issues block <new-id> --on <other-id>`) if one fix must
  precede another.
- If MORE than {max_tickets} critical gaps exist, file only the {max_tickets} most
  critical and add an `issues comment {map_id}` noting more remain — the next round
  catches them once these land. Do not file a pile.
- If NOTHING is critical, file NOTHING and add a one-line `issues comment {map_id}`
  confirming the effort meets its Destination. Filing nothing is the signal the
  effort is done: it unlocks the retrospective and lets the effort close.

You may create tickets and comment, but you may NOT modify project code or any
existing ticket's spec."""
    else:
        teeth = f"""\
# Report only — ticket-filing is disabled

Do not create or modify any ticket, and do not touch project code. Post your findings
as a SINGLE `issues comment {map_id}`: one line per critical gap, or a one-line "meets
its Destination" if the effort is clean. A human takes it from there."""

    return f"""\
You are a STRICT, independent reviewer judging a whole effort at once — one altitude
above the per-ticket validator, which only ever saw a single ticket. Every build
ticket under this map has passed its own validation and landed. Your question is the
one no per-ticket check could ask: **does the assembled work actually reach this
map's Destination, as a coherent whole?**

You are READ-ONLY on code: inspect the working tree (`git log`, `git diff`, read
files, run read-only checks) and read tickets with read-only `issues` commands.

# The map — #{map_id}: {title}

{body}

## Build tickets under this map (all terminal)
{kids}

## Normative design doc
`{design_path}` — the assembled work must not contradict any `D-N`, nor stretch one
past the scope its **Why:** bounds.

## Naming registry
`{vocab_path}` — the canonical name per concept; use it to catch one concept built
twice under two names across different tickets.

# What counts as CRITICAL — the bar is narrow

A finding is CRITICAL only if it means the effort does not actually reach its
Destination:

1. UNMET DESTINATION — a clause of the Destination is not delivered by the assembled
   work.
2. BROKEN SEAM — slices that each passed alone but do not compose: a broken
   end-to-end path, or mismatched interfaces between two tickets.
3. CONTRADICTS CANON — the assembled whole contradicts a `D-N` decision.
4. DEAD SCAFFOLDING — intermediate scaffolding a later slice was meant to remove and
   didn't.

NOT critical — do NOT raise: improvements, polish, refactors, "would be nicer", or new
scope. Unbuilt-but-desirable work is FOG for the next wayfinder pass, not a milestone
finding. When in doubt, it is not critical.

{teeth}

Begin now.
"""


def build_retrospective_prompt(
    map_issue: dict,
    children: list[dict],
    design_path: str,
    vocab_path: str,
    log_excerpt: str,
) -> str:
    """Read-only retrospective over ONE finished, clean-reviewed effort. Not a code
    review — the milestone review already passed — it mines how the effort *ran* for
    lessons its canon should absorb, and proposes them for a human to ratify. Its only
    write is a single summary comment on the map; advisory by design."""
    map_id = map_issue["id"]
    title = map_issue.get("title", "")
    body = (map_issue.get("body") or "").strip()
    kids = render_children(children)

    return f"""\
You are running a retrospective over one finished effort. Its whole-effort review is
clean, so the code is not in question here. Your job is to mine how the effort *ran*
for lessons its canon should absorb — so the next effort starts from a richer design
doc and naming registry instead of relearning the same things.

You are READ-ONLY: read files and use read-only `issues` commands. Your ONLY write is
the one summary comment below. Do not edit canon, and do not file or change tickets.

# The map — #{map_id}: {title}

{body}

## Build tickets under this map
{kids}

Read the tickets that struggled (`issues show <id>`) for their retry notes,
`assumption:` comments, and any `QUESTION:` escalations — that is where the friction
left a trace.

## Run-log excerpt for this effort
Each line is one dispatch/triage/validate/retry/escalate/commit event. Repetition and
counts here are the signal.

{log_excerpt}

# What to look for — systemic, not one-off

1. ESCALATION CLUSTER — several tickets that escalated on the same underlying axis:
   one `D-N`-shaped hole, not three separate questions.
2. REPEATED ASSUMPTION — the same concept defaulted via `assumption:` across tickets:
   a missing naming-registry entry, or a missing `D-N`.
3. ATTEMPT HOT-SPOT — tickets that took several attempts: where the spec or canon was
   too thin for a worker to land it first try.
4. RECURRING VALIDATOR ESCALATE — a silent-default the validator kept catching: a gap
   the worker prompt or the design doc should close at the source.

# Output — ONE comment, advisory

Post a single `issues comment {map_id}` with a short report. Propose, do not enact:

- **Canon (`D-N`) proposals** — each a one-line decision plus its **Why:**, for a
  human design session to ratify into `{design_path}`.
- **Registry proposals** — canonical terms worth adding to `{vocab_path}`.
- **Systemic gaps** — anything else worth a human's eye before the next effort.

Not every effort teaches something. If the run was clean — few retries, no escalation
cluster, no repeated assumption — say exactly that in one line. Do NOT invent findings
to fill the report. Then STOP.
"""


def parse_verdict(text: str) -> tuple[str, str]:
    """Return (verdict, reason) where verdict is 'pass', 'fail', 'escalate', or 'none'.

    'none' means the validator emitted no marker at all — a malformed judge, not a
    judgement about the code. The loop distinguishes it so it can re-run the
    validator (a flaky verdict format) instead of re-dispatching the worker over
    already-correct code. 'escalate' means the validator judged the work otherwise
    passable but caught the worker silently defaulting a genuine design decision — the
    loop routes it to needs-decision, not a worker retry. A 'fail' (or a FAIL with no
    reason) still fails closed. The latest marker in the text wins (the final line)."""
    upper = text.upper()
    idx = {
        "fail": upper.rfind(VERDICT_FAIL),
        "escalate": upper.rfind(VERDICT_ESCALATE),
        "pass": upper.rfind(VERDICT_PASS),
    }
    verdict = max(idx, key=idx.get)
    if idx[verdict] == -1:
        return "none", "validator produced no verdict marker"
    marker = {"fail": VERDICT_FAIL, "escalate": VERDICT_ESCALATE, "pass": VERDICT_PASS}[verdict]
    reason = text[idx[verdict] + len(marker):].lstrip(" —-\n").strip()
    if verdict == "pass":
        return "pass", ""
    if verdict == "fail":
        return "fail", reason or "validator failed the work (no reason given)"
    return "escalate", reason or "validator flagged a silently-defaulted design decision"


async def drive(command: list[str], cwd: str, prompt: str, label: str | None = None) -> str:
    """Drive one fresh `pi` run to a stop, streaming with the shared formatter.
    Returns the concatenated agent-visible reply text. Stateless: one client, one
    prompt. `label` (e.g. "worker #12") banners the dispatch instead of echoing the
    whole prompt."""
    formatter = EventFormatter(label)
    parts: list[str] = []
    async with PiRpcClient(command, cwd=cwd) as client:
        await client.send_message(prompt)
        async for event in client.events():
            formatter.print(event)
            if isinstance(event, AgentMessageEvent):
                parts.append(event.text)
            # `agent_settled` is the authoritative end of a prompt: the agent
            # has stopped with no retry, compaction, or queued continuation left.
            # A per-turn `stop` is only a fallback for a pi build that doesn't
            # emit settle -- on its own it would hang on a final turn that ends
            # `length`/`error`/`aborted`, or miss a retry/compaction tail.
            if isinstance(event, (AgentSettledEvent, SessionEndEvent)):
                break
            if isinstance(event, TurnEndEvent) and event.stop_reason == "stop":
                break
    return "".join(parts)
