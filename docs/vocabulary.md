# Vocabulary — canonical naming registry

One canonical name per concept, so independent agent runs converge on the same word
instead of coining synonyms. The failure this prevents: two stateless workers name
the same thing `zip` and `zap`, each greps for its own word, misses the other's
implementation, and builds it twice. This happens even in a fully serial loop — the
second agent only has to search for the wrong term to sail past the first agent's
committed code. A shared registry gives every run one place to recognise "this
concept already has a name."

This file is **convention, not canon**. It records *what we call things*, not *what
we weighed* — so it carries no rationale and no `D-N` address. A term that was
actually *contested* — where the definition itself was argued out and decided — is a
weighed decision and belongs in `docs/design.md` as a `D-N`, not here. This file is
for the uncontested majority: names nobody argued about but everybody must share.
(The two have opposite admission tests: canon admits only what was weighed; this
registry admits any concept two runs might both touch. One list can't gate both.)

## How to read it

**Browse it, don't grep it.** You can only search for a name you already thought of —
which is exactly the name you're about to get wrong. Read the entries by concept
before you name something new. Each entry leads with the synonyms to avoid, so when
the word in your head is the wrong one, you find it here in a `Not:` list and follow
it to the canonical term. That only works while this file stays short enough to read
top to bottom every run — a registry nobody finishes reading prevents nothing.

## Who writes it

**Triage only.** Triage already reads the code to specify a ticket, so it is the
natural point to reuse an existing canonical name (and inline it into the ticket) and
to register a recurring concept it finds unregistered. Workers and the validator
**read** the registry; they do not edit it — a single writer keeps it from forking.
A worker that hits a collision-prone concept the registry misses records an
`assumption:` for triage or a design session to fold in later; it does not self-add.

## Format

    ### <canonical term>

    <one-line meaning — enough to recognise the concept, not to define it richly>
    **Not:** <synonym>, <synonym>   — wrong words that must redirect here

Keep it lean. If it ever grows past what a worker will read each run, split the
rarely-collided long tail into a separate on-demand glossary and keep this file to
the high-collision core.

## Terms

_(none yet — triage appends the first as it specifies tickets.)_
