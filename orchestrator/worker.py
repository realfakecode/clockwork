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
`zap`) and build it twice. Browse it by concept; do not just grep, because the word
you'd grep for is the one you're about to get wrong — the `Not:` lists are there to
catch exactly that. Your ticket's concepts should already carry canonical terms from
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


def build_validator_prompt(issue: dict, design_path: str, vocab_path: str, test_summary: str) -> str:
    """Read-only judge of a just-finished implementation against the criteria."""
    ticket_id = issue["id"]
    title = issue.get("title", "")
    body = (issue.get("body") or "").strip()
    criteria = render_criteria(issue.get("acceptance_criteria") or [])

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


async def drive(command: list[str], cwd: str, prompt: str) -> str:
    """Drive one fresh `pi` run to a stop, streaming with the shared formatter.
    Returns the concatenated agent-visible reply text. Stateless: one client, one
    prompt."""
    formatter = EventFormatter()
    parts: list[str] = []
    async with PiRpcClient(command, cwd=cwd) as client:
        await client.send_message(prompt)
        async for event in client.events():
            formatter.print(event)
            if isinstance(event, AgentMessageEvent):
                parts.append(event.text)
            if isinstance(event, TurnEndEvent) and event.stop_reason == "stop":
                break
            if isinstance(event, SessionEndEvent):
                break
    return "".join(parts)
