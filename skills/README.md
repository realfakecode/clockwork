# Skills

Agent skills the clockwork workflow uses. Install them into the project
clockwork drives (or user-globally) so the agent harness picks them up.

- **`wayfinder`** — the bootstrap phase. Charts a loose idea into a `wayfinding` map issue
  and the thin `needs-triage` build tickets clockwork consumes, wiring their blocking edges
  and leaving the rest as fog. Run it with `/wayfinder` to seed a tracker before the first
  `clockwork` run, and again to graduate fog as the frontier advances.
- **`design-session`** — the human design phase. Drains the `needs-decision` queue,
  patches the design doc with new `D-N` decisions, and routes tickets back to
  `ready-for-agent`. Run it with `/design-session` when clockwork halts on a full
  queue.
- **`domain-modeling`** — how to write canon: which decisions earn a `D-N` in the
  design doc versus a plain name in the naming registry. Used by `design-session`.
- **`grilling`** — stress-tests a decision the user is unsure about, one branch at a
  time. Invoked by `design-session`.
- **`issue-tracker`** — reference for the `issues` CLI (statuses, storage, commands).
