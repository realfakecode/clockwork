---
name: wayfinder
description: Chart a loose idea — too big for one loop run to hold — into the initial build tickets clockwork consumes, wired as a map on the issue tracker with the near frontier ticketed and the rest left as fog.
disable-model-invocation: true
---

# wayfinder

A loose idea has arrived — too big to hand the loop as one ticket, and wrapped in fog: the
way from here to the **destination** isn't visible yet. Wayfinding is the **bootstrap
phase** — it charts that way as a map on the issue tracker and files the build tickets the
`clockwork` loop then consumes, one at a time, until only fog and blocked work remain.

Clockwork already has two *runtime* phases: the execution loop drives `ready-for-agent`
tickets to a worker, and `/design-session` drains the `needs-decision` questions that
surface mid-flight. Wayfinding sits **before** both — nothing to execute until tickets
exist, and nothing to escalate until the loop runs. It runs once to seed the tracker, and
again later to graduate fog as the frontier advances.

## Chart, don't build

Wayfinding **plans**; it never writes product code. Its outputs are three: a **map** issue,
the near-frontier **build tickets** as children of it, and any up-front **decisions** it had
to settle, written into canon. The pull to start building the thing is the signal charting
is done — hand off to `clockwork` and let the loop build. Charting that emits code has
skipped its own exit.

Two kinds of ticket must not be confused. The tickets wayfinding files are **build
tickets** — slices of the thing to make, which the loop's triage agent specifies and a
worker implements. It does **not** file *decision* tickets; a decision that gates the shape
of the map is settled live during charting (below) and recorded in `docs/design.md`, and a
decision that surfaces later, mid-execution, is `/design-session`'s job. Wayfinding's
tickets are always work to do, never questions to answer.

## Refer by name

Every map and ticket is an issue, so it has a **name** — its title. In everything the human
reads, refer to it by that name, never a bare id (`#42`). A wall of `#42, #43, #44` is
illegible; names read at a glance. The id doesn't vanish — it rides inside the name — but it
never stands in for it.

## The map

The map is a single issue on the tracker, created directly in status **`wayfinding`** with
label **`wayfinder:map`** (`issues new <feature> "<destination name>" --status wayfinding
--label wayfinder:map --body -`). Its build tickets are its **children** (`issues new ...
--parent <map-id>`).

`wayfinding` is why the loop can't trip over the map. It sits in the `active` bucket, so
`issues ready` (todo bucket only) never surfaces the map — the loop can neither dispatch nor
triage it — and it isn't `needs-decision`, so it never counts toward the escalation-queue
threshold that halts a run. The map is inert to the loop by construction, not by a
convention the loop might outgrow. Its children are ordinary `needs-triage` tickets the loop
consumes like any other.

The map is an **index, not a store**. Decisions live in canon — `docs/design.md`, one
`D-N` unit each — and the map points there; it never restates a decision. Build-ticket
detail lives in each ticket (thin at first; triage fills it in). Open tickets are found by
query, not listed in the map body.

### The map body

```markdown
## Destination

<what reaching the end of this effort looks like — the spec, feature, or change this map is
finding its way to, and what "done" means for the whole. One or two lines; canon (D-N)
carries the normative detail.>

## Notes

<the feature name tickets file under; domain; skills every session should consult; standing
preferences for this effort. Point at docs/design.md and docs/vocabulary.md as the canon.>

## Not yet specified

<!-- see "Fog of war": in-scope work you can't ticket yet; graduates as the frontier advances -->

## Out of scope

<!-- see "Out of scope": work ruled beyond the destination; never graduates -->
```

There is no "decisions" section — that is `docs/design.md`. Keeping the record in one place
is the whole point; a second copy on the map is the rot vector that makes agents read stale
design.

### The tickets

Each build ticket is a **child** `needs-triage` issue — thin by design. Give it a title that
names the slice and a one-line body stating what it covers; leave category and acceptance
criteria to the loop's triage agent, which exists precisely to specify thin tickets against
the code and canon. Charting fixes the **shape and the edges**, not the spec.

```bash
issues new <feature> "<slice title>" --parent <map-id> --status needs-triage --body -
```

Wire dependencies with the tracker's native blocking (`--blocked-by 1,2` at creation, or
`issues block <id> --on 1,2` after). A ticket is ready once every ticket blocking it is
`done`; `issues ready` is the frontier the loop pulls from. Because ids must exist before
they can reference each other, **create the tickets first, then wire the edges in a second
pass**.

## Fog of war

The map is *deliberately* incomplete: don't chart what you can't yet see. Beyond the filed
tickets lies the **fog** — decisions and slices you can tell are coming but can't pin down,
because they hang on questions still open or on tickets not yet built. It goes in the map's
**Not yet specified** section: the suspected area, to revisit once the frontier reaches it.

**Fog or ticket?** The test is whether you can state the slice precisely now — *not* whether
it's buildable now.

- **Ticket when** the slice is already sharp, even if it's blocked and can't run yet.
- **Not yet specified when** you can't phrase it that sharply. Don't pre-slice the fog into
  tickets: one patch may graduate into several, or none, once the frontier reaches it.

Triage clears fog *within* a thin ticket — it can't invent a ticket from prose. Graduating a
patch of **Not yet specified** into fresh tickets is a wayfinding act: re-invoke this skill
as the frontier advances.

## Out of scope

The destination fixes the scope, so work beyond it is **out of scope** — not fog, and it
never graduates. It gets the map's **Out of scope** section: one line of gist plus why it's
ruled out. If a filed ticket turns out to sit past the destination, **close it**
(`issues status <id> wontfix` with a category, or delete it) and leave the line — a closed
ticket is unambiguously off the frontier.

## Canon is the decision record

Charting is where an effort's up-front decisions get settled — the ones that fix the shape
of the map. Settle them **live** and write them down:

- A weighed **decision** → `docs/design.md` as an addressable `D-N` unit (with the `Why:`
  that bounds its scope). Use `domain-modeling` for the admission test and format.
- A **name** for a concept the tickets will share → `docs/vocabulary.md`, so independent
  worker runs don't coin two names for one thing.

This is the same canon the loop's workers, triage, and validator read, and the same
`docs/design.md` a `/design-session` patches. Wayfinding writes the *first* decisions;
design-session writes the ones that surface later. One store, cited by `D-N`, never
duplicated onto the map.

## Invocation

### Chart the map

User invokes with a loose idea.

1. **Name the destination.** Run `/grilling` with `/domain-modeling` to pin down what this
   effort is finding its way to and what "done" means for the whole. The destination fixes
   the scope, so it settles first. Write its normative content to `docs/design.md`.
2. **Map the frontier — breadth-first.** Grill again, fanning across the whole space rather
   than deep on one thread: surface the open decisions the shape depends on, settle them
   into canon (or fire a `/research` subagent for any that turn on external facts, folding
   the finding into canon before you finalize), and surface the first build slices takeable
   now. **If this surfaces no fog** and the whole thing is a handful of tickets — the way is
   already clear — skip the map issue and just file the tickets under a feature; you don't
   need wayfinding for that.
3. **Create the map** (`--status wayfinding --label wayfinder:map`): Destination and Notes
   filled in, the fog sketched into **Not yet specified**, **Out of scope** seeded with
   anything you consciously ruled out.
4. **File the near frontier.** Create the build tickets you can specify now as thin
   `needs-triage` children of the map, then wire blocking edges in a second pass. Everything
   you can't specify stays in the fog.
5. **Stop.** Charting builds nothing. Tell the user the frontier is seeded and `clockwork`
   can run.

### Extend the map

Re-invoked as the frontier advances (the loop has drained the ready tickets, or a decision
cleared the view).

1. Load the **map** and `issues ready` — the low-res view, not every ticket body.
2. Graduate any **Not yet specified** patch the advanced frontier has made specifiable into
   fresh `needs-triage` children (create-then-wire), clearing each graduated patch from the
   section so it lives only as its tickets.
3. If new decisions had to be settled to do so, write them to `docs/design.md` as `D-N`. If
   something now reads as past the destination, rule it **out of scope** rather than
   ticketing it.
4. Stop and hand back to `clockwork`.

## Exit criterion

Done means all of: the map issue exists in `wayfinding` with Destination, Notes, and fog;
the near-frontier build tickets exist as `needs-triage` children with their blocking edges
wired; every up-front decision is in `docs/design.md` as a `D-N`; and `issues ready` shows
the takeable frontier. Verify the frontier is real:

```bash
issues ready --unclaimed     # the tickets clockwork will pick, in order
```

Then tell the user clockwork can run (`clockwork --validate "<test command>"`).
