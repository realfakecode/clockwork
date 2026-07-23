# Skills

Claude Code skills the clockwork workflow uses. Install them into the project the
harness drives (or user-globally) so Claude Code picks them up:

```bash
cp -r skills/* ~/.claude/skills/                 # user-global, or:
cp -r skills/* /path/to/target/.claude/skills/   # project-local
```

- **`design-session`** — the human design phase. Drains the `needs-decision` queue,
  patches the design doc with new `D-N` decisions, and routes tickets back to
  `ready-for-agent`. Run it with `/design-session` when the harness halts on a full
  queue.
- **`domain-modeling`** — how to write canon: which decisions earn a `D-N` in the
  design doc versus a plain name in the naming registry. Used by `design-session`.
- **`grilling`** — stress-tests a decision the user is unsure about, one branch at a
  time. Invoked by `design-session`.
- **`issue-tracker`** — reference for the `tracker` CLI (statuses, storage, commands).
