---
name: issue-tracker
description: Agent-friendly man page for `issues`, the plain-text issue-tracker CLI — statuses, storage, and command/flag reference. Use whenever you run `issues`.
---

# issue-tracker

`issues` is a plain-text issue tracker: markdown + YAML frontmatter under `.scratch/`,
global monotonic ids, automatic dependency resolution, archiving, lint. `issues <command>
--help` is the exhaustive, always-current flag reference — trust it (and any error message
that enumerates accepted values) over prose.

## Running it

`issues <command> ...` — it's on PATH. Root discovery walks up from the cwd looking for
`.scratch/`, so any subdirectory works.

Any body/comment/answer flag accepts `-` to read from stdin (`--body -`, `--answer -`) —
use it for multi-line text via a heredoc. Most read commands take `--json`.

## The state model

The default config (written in full to `.scratch/.issues.yaml` by `issues init`) defines
three buckets:

- **todo bucket**: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`
- **active bucket**: `in-progress`, `needs-decision`, `wayfinding`
- **done bucket**: `done`, `wontfix`

`wayfinding` marks a `/wayfinder` map issue (the parent that holds an effort's Destination,
fog, and out-of-scope prose; its build tickets are children). It is `active` so it stays out
of `issues ready` and never counts as escalation work.

`issues ready` returns only the **todo** bucket; active and done statuses are excluded.

Transitions (checked unless `--force`):

```
needs-triage    -> needs-info, ready-for-agent, ready-for-human, wontfix
needs-info      -> needs-triage, wontfix
ready-for-agent -> in-progress, ready-for-human, needs-info, needs-decision, wontfix
ready-for-human -> in-progress, ready-for-agent, needs-info, wontfix
in-progress     -> done, ready-for-agent, ready-for-human, needs-decision, wontfix
needs-decision  -> ready-for-agent, ready-for-human, wontfix
wayfinding      -> done, wontfix
done            -> (terminal)
wontfix         -> needs-triage
```

Invariants (also bypassed by `--force`): moving into `ready-for-agent`/`ready-for-human`/
`wontfix` requires a **category**; into `ready-for-agent`/`ready-for-human` also requires a
non-empty **acceptance-criteria** checklist.

## Storage layout

```
.scratch/
  <feature>/
    issues/<id>-<slug>.md            # active issues, grouped by feature
  archive/<id>-<slug>.md             # archived issues, all features together
```

`archive/` is a single top-level directory, not per-feature — ids are global and
monotonic, so archived filenames never collide even though every feature's archived
issues land in the same place. An issue's feature is a frontmatter field (`feature:`),
not something inferred from its path, so it survives the move to `archive/`.

Never hand-edit frontmatter or filenames — every field has a command that writes it
correctly. Editing body prose outside the frontmatter (the title/question/spec text and
`## Comments`) is fine.

## Commands (essentials — `issues <cmd> --help` for all flags)

- **`new <feature> <title>`** — `--category --status --criterion "<text>"` (repeatable)
  `--label --parent --blocked-by 1,2 --body - --force --json`.
- **`list`** — `--status --feature --category --label --assignee --json`.
- **`show <id> [<id> ...]`** — one or more issues; `--json` for frontmatter + body
  (carries `## Comments`).
- **`edit <id>`** — `--add-label/--remove-label --status --category --body -`.
- **`comment <id> "<text>"`** — appends a timestamped line under `## Comments`. The text
  may be a positional argument, `--body`, or `-`/`--body -` for stdin.
- **`criteria <id>`** — `--add "<text>"` / `--check N` / `--uncheck N` / `--remove N`
  (0-indexed). No flags prints the checklist.
- **`status <id> [<status>]`** — set the status, or print the current one when `<status>`
  is omitted. **`claim <id> --as <name>`** /
  **`release <id> [--keep-status]`** (clears assignee; resets to `ready-for-agent` unless
  `--keep-status`).
- **`resolve <id> "<text>" [--status <s>]`** — comments the answer (positional, `--answer`,
  or stdin) and sets status (default `done`).
- **`ready [--unclaimed --feature <f> --json]`** — the todo-bucket frontier with all
  `blocked_by` satisfied.
- **`block <id> --on 1,2`** / **`blocked`** / **`blocking <id>`** — dependency edges/queries.
- **`children <id>`** / **`parent <id>`** — the child/parent edges of one issue.
- **`tree [<id>] [--feature <f> --include-archived]`** — the parent/child hierarchy as an
  indented tree; give an `<id>` to root it at that issue's subtree.
- **`path <id> [<id> ...]`** — print each issue's file path (one per line), e.g.
  `vim $(issues path 3)`.
- **`lint [--fix]`** / **`archive <id>`** / **`archive --done`**.

## Gotchas

- Ids never get reused, even across archiving — let `new` allocate them.
- Archived issues still count toward dependency resolution; they're just hidden from default
  `list`/`triage`.
