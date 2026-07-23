---
name: domain-modeling
description: Build and sharpen a project's domain model against the canonical design doc. Use when pinning down terminology or a boundary dispute, or when design-session needs to resolve a term before deciding behaviour.
---

# Domain Modeling (clockwork workflow)

Actively sharpen the project's domain model as you design. This is the *active* discipline
— challenging terms, inventing edge-case scenarios, and writing decisions down the moment
they crystallise.

A resolved term lands in one of **two** homes, and which one depends on whether the term was
*weighed*:

- **A contested definition → the design doc** (`docs/design.md` by default) as an addressable
  `D-N` unit. When the *meaning* of a term was argued out — "does 'token' mean X or Y?", a
  boundary genuinely in dispute — that resolution is a weighed decision like any other, and
  canon is where weighed decisions live (it must answer *"what did we weigh?"*).
- **An uncontested canonical name → the naming registry** (`docs/vocabulary.md`). Most naming
  isn't argued about; someone just has to pick one word so independent agent runs don't coin
  `zip` and `zap` for the same concept and build it twice. That's convention, not canon — it
  fails the "what did we weigh?" test and would only dilute the `D-N` list. During execution
  the registry is **triage's** to maintain; here in a design session you fold in names a
  question surfaced and graduate any term that turns out to have been genuinely contested up
  into a `D-N`.

The two have *opposite* admission tests — canon admits only what was weighed, the registry
admits any concept two runs might collide on — so one list can't gate both; that's why they
are separate files. The registry is subordinate to canon: a term graduates *up* into a `D-N`
the moment its definition is actually weighed.

## What earns a `D-N`

Only genuinely **weighed** decisions. Presence in canon *means* "we weighed this," which is
why there is deliberately **no confidence or status field** — a bare convention or an
arbitrary default does not belong in canon and stays cheaply overturnable precisely because
nothing enshrined it (put it in code or `CLAUDE.md`). A confidence field would be redundant
with that and only invite hedging and relitigation.

Admission test: an entry must be able to answer *"what did we weigh?"* If it can't, it's a
convention wearing a decision's clothes — registry or code, not a `D-N`.

Each entry:

```markdown
## D-<N>: <title>

<the constraint the implementation must satisfy>

**Why:** <the reason — which bounds what the constraint may be read to mean, so the terse
words can't be repurposed into an adjacent, unintended claim; include the rejected
alternative when the choice was contested>
```

The `Why:` is load-bearing, not decoration: it fixes the decision's *scope*. Size it to the
decision's weight — a one-line constraint gets a one-line why; a hard tradeoff carries the
alternative it beat. Never renumber existing entries (tickets cite them by number); when a
decision changes, edit the entry in place — the `D-N` number is the stable address, its
content can move.

## During a session

### Challenge against canon

When a term conflicts with an existing `D-N` decision, call it out immediately. "D-4 defines
'token' as X, but you seem to mean Y — which is it?"

### Sharpen fuzzy language

When the user uses a vague or overloaded term, propose a precise canonical term. "You're
saying 'node' — do you mean the AST node or the parse-stack entry? Those are different."

### Discuss concrete scenarios

Stress-test relationships with specific inputs that probe edge cases and force precision
about the boundaries between concepts.

### Cross-reference with code

When the user states how something works, check whether the code agrees. Surface any
contradiction: "Your parser flattens nested groups, but you just said grouping is preserved
— which is right?"

### Write it down where it belongs — inline, not batched

When a term or boundary resolves, record it the moment it crystallises. Route by whether it
was weighed:

**Contested definition → canon.** Add or amend a `D-N` decision in the design doc, in the
format above (*What earns a `D-N`*). Keep the doc **normative and devoid of implementation
detail**: decisions and constraints only, never a description of the code (code describes
itself and prose about it goes stale instantly).

**Uncontested canonical name → the naming registry** (`docs/vocabulary.md`). If you just
need everyone to use one word for a concept nobody argued about, add it there — canonical
term, a one-line meaning, and a `Not:` list of the synonyms it displaces. Don't spend a
`D-N` on it; that would dilute the weighed-decisions list with plain vocabulary.

```markdown
### <canonical term>

<one-line meaning>
**Not:** <synonym>, <synonym>
```

If you can't tell which it is, ask "what did we weigh?" — if there's a real answer, it's a
`D-N`; if the honest answer is "nothing, we just picked a word," it's a registry entry.
