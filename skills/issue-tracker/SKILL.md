---
name: issue-tracker
description: Agent-friendly man page for `tracker`, the plain-text issue-tracker CLI — statuses, storage, and command/flag reference. Use whenever you run `tracker`.
---

# issue-tracker

`tracker` is a plain-text issue tracker: markdown + YAML frontmatter under `.scratch/`,
global monotonic ids, automatic dependency resolution, archiving, lint. `tracker <command>
--help` is the exhaustive, always-current flag reference — trust it (and any error message
that enumerates accepted values) over prose.

## Running it

`tracker <command> ...` — it's on PATH. Root discovery walks up from the cwd looking for
`.scratch/`, so any subdirectory works.

Any body/comment/answer flag accepts `-` to read from stdin (`--body -`, `--answer -`) —
use it for multi-line text via a heredoc. Most read commands take `--json`.

## The state model

The default config (written in full to `.scratch/.tracker.yaml` by `tracker init`) defines
three buckets:

- **todo bucket**: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `open`
- **active bucket**: `in-progress`, `needs-decision`
- **done bucket**: `done`, `wontfix`

`tracker ready` returns only the **todo** bucket; active and done statuses are excluded.

Transitions (checked unless `--force`):

```
needs-triage    -> needs-info, ready-for-agent, ready-for-human, wontfix
needs-info      -> needs-triage, wontfix
ready-for-agent -> in-progress, ready-for-human, needs-info, needs-decision, wontfix
ready-for-human -> in-progress, ready-for-agent, needs-info, wontfix
in-progress     -> done, ready-for-agent, ready-for-human, needs-decision, wontfix
needs-decision  -> ready-for-agent, ready-for-human, wontfix
open            -> in-progress, ready-for-agent, ready-for-human, wontfix, needs-info
done            -> (terminal)
wontfix         -> needs-triage
```

Invariants (also bypassed by `--force`): moving into `ready-for-agent`/`ready-for-human`/
`wontfix` requires a **category**; into `ready-for-agent`/`ready-for-human` also requires a
non-empty **acceptance-criteria** checklist.

## Storage layout

```
.scratch/
  .tracker.yaml                      # config: full statuses+transitions maps (`tracker init`)
  <feature>/
    issues/<id>-<slug>.md            # active issues
    archive/<id>-<slug>.md           # archived (still resolve dependencies)
```

Never hand-edit frontmatter or filenames — every field has a command that writes it
correctly. Editing body prose outside the frontmatter (the title/question/spec text and
`## Comments`) is fine.

## Commands (essentials — `tracker <cmd> --help` for all flags)

- **`new <feature> <title>`** — `--category --status --criterion "<text>"` (repeatable)
  `--label --parent --blocked-by 1,2 --body - --force --json`.
- **`list`** — `--status --feature --category --label --assignee --json`.
- **`show <id>`** — `--json` for frontmatter + body (carries `## Comments`).
- **`edit <id>`** — `--add-label/--remove-label --status --category --body -`.
- **`comment <id> --body -`** — appends a timestamped line under `## Comments`.
- **`criteria <id>`** — `--add "<text>"` / `--check N` / `--uncheck N` / `--remove N`
  (0-indexed). No flags prints the checklist.
- **`status <id> <status>`** / **`claim <id> --as <name>`** /
  **`release <id> [--keep-status]`** (clears assignee; resets to `ready-for-agent` unless
  `--keep-status`).
- **`resolve <id> --answer "<text>" [--status <s>]`** — comments the answer, sets status
  (default `done`).
- **`ready [--unclaimed --feature <f> --json]`** — the todo-bucket frontier with all
  `blocked_by` satisfied.
- **`block <id> --on 1,2`** / **`blocked`** / **`blocking <id>`** — dependency edges/queries.
- **`lint [--fix]`** / **`archive <id>`** / **`archive --done`**.

## Gotchas

- Ids never get reused, even across archiving — let `new` allocate them.
- Archived issues still count toward dependency resolution; they're just hidden from default
  `list`/`triage`.
