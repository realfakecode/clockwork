"""argparse-based CLI for the issue tracker."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import config as config_mod
from . import deps as deps_mod
from . import lint as lint_mod
from . import model
from . import service as service_mod
from . import store as store_mod
from .model import Issue
from .store import IssuesError


def read_text_arg(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "-":
        return sys.stdin.read()
    return value


def parse_id_list(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def issue_to_dict(issue: Issue, *, include_body: bool = False) -> dict:
    data = {
        "id": issue.id,
        "slug": issue.slug,
        "title": issue.title,
        "status": issue.status,
        "category": issue.category,
        "labels": issue.labels,
        "parent": issue.parent,
        "assignee": issue.assignee,
        "blocked_by": issue.blocked_by,
        "acceptance_criteria": issue.acceptance_criteria,
        "created": issue.created.isoformat(),
        "updated": issue.updated.isoformat(),
        "feature": issue.feature,
        "archived": issue.location.archived,
        "path": str(issue.location.path),
    }
    if include_body:
        data["body"] = issue.body
    return data


def format_line(
    issue: Issue,
    index: dict[int, Issue] | None = None,
    config: dict | None = None,
) -> str:
    bits = [f"#{issue.id}", issue.status, f"[{issue.feature}]", issue.title]
    extras = []
    if issue.assignee:
        extras.append(f"assignee={issue.assignee}")
    if issue.labels:
        extras.append("labels=" + ",".join(issue.labels))
    if issue.blocked_by:
        # Show only blockers that are still unresolved. Without index+config we
        # can't tell, so fall back to the raw declared list.
        if index is not None and config is not None:
            blocking = deps_mod.unsatisfied_blockers(issue, index, config)
        else:
            blocking = list(issue.blocked_by)
        if blocking:
            extras.append("blocked_by=" + ",".join(str(b) for b in blocking))
    if issue.location.archived:
        extras.append("archived")
    line = "  ".join(bits)
    if extras:
        line += "  (" + " ".join(extras) + ")"
    return line


def print_records(
    records: list[Issue],
    as_json: bool,
    index: dict[int, Issue] | None = None,
    config: dict | None = None,
) -> None:
    if as_json:
        print(json.dumps([issue_to_dict(r) for r in records], indent=2))
        return
    if not records:
        print("(none)")
        return
    for record in records:
        print(format_line(record, index, config))


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = store_mod.init_repo()
    print(f"initialized issue tracker at {config_mod.scratch_dir(root)}")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    issue = service_mod.new(
        feature=args.feature,
        title=args.title,
        slug=args.slug,
        status=args.status,
        category=args.category,
        labels=args.label or [],
        parent=args.parent,
        blocked_by=parse_id_list(args.blocked_by),
        assignee=args.assignee,
        criteria_texts=args.criterion or [],
        body=read_text_arg(args.body),
        force=args.force,
    )
    if args.json:
        print(json.dumps(issue_to_dict(issue), indent=2))
    else:
        print(f"created issue {issue.id}: {issue.location.path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    index = store_mod.load_index(root)
    records = list(index.values())

    if not args.include_archived:
        records = [r for r in records if not r.location.archived]
    if args.feature:
        records = [r for r in records if r.feature == args.feature]
    if args.status:
        records = [r for r in records if r.status == args.status]
    if args.category:
        records = [r for r in records if r.category == args.category]
    if args.label:
        records = [r for r in records if args.label in r.labels]
    if args.assignee:
        records = [r for r in records if r.assignee == args.assignee]
    if args.parent is not None:
        records = [r for r in records if r.parent == args.parent]

    records.sort(key=lambda r: r.id)
    print_records(records, args.json, index, config)
    return 0


def _print_issue(issue: Issue, as_json: bool) -> None:
    if as_json:
        print(json.dumps(issue_to_dict(issue, include_body=True), indent=2))
        return
    print(model.serialize_issue(issue, include_criteria=False), end="")
    if issue.acceptance_criteria:
        print("\nAcceptance criteria:")
        print(model.render_criteria(issue.acceptance_criteria))
    loc = issue.location
    print(f"\n(feature: {issue.feature}, path: {loc.path}, archived: {loc.archived})")


def cmd_show(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    index = store_mod.load_index(root)
    records = []
    for issue_id in args.ids:
        issue = index.get(issue_id)
        if issue is None:
            raise IssuesError(f"no issue with id {issue_id}")
        records.append(issue)
    if args.json:
        payload = [issue_to_dict(r, include_body=True) for r in records]
        print(json.dumps(payload if len(payload) > 1 else payload[0], indent=2))
        return 0
    for i, record in enumerate(records):
        if i:
            print()
        _print_issue(record, False)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    issue = service_mod.edit(
        args.id,
        title=args.title,
        slug=args.slug,
        status=args.status,
        category=args.category,
        add_labels=args.add_label,
        remove_labels=args.remove_label,
        parent=args.parent,
        assignee=args.assignee,
        body=read_text_arg(args.body),
        force=args.force,
    )
    print(f"updated issue {issue.id}")
    return 0


def one_text_arg(positional: str | None, flagged: str | None, name: str) -> str | None:
    """Resolve a payload that may arrive as a positional or a flag. At most one
    source may be set; either can be `-` to read stdin."""
    provided = [v for v in (positional, flagged) if v is not None]
    if len(provided) > 1:
        raise IssuesError(f"pass {name} once — as a positional argument or the flag, not both")
    return read_text_arg(provided[0]) if provided else None


def cmd_comment(args: argparse.Namespace) -> int:
    text = one_text_arg(args.body, args.body_flag, "the comment body")
    if not text or not text.strip():
        raise IssuesError("comment body is empty (pass it as an argument or via '-' for stdin)")
    issue = service_mod.comment(args.id, text)
    print(f"commented on issue {issue.id}")
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    if args.done:
        targets = service_mod.archive_done()
        print(f"archived {len(targets)} issue(s): {', '.join(str(t) for t in targets) or '(none)'}")
        return 0

    if args.id is None:
        raise IssuesError("pass an issue id or --done")
    issue = service_mod.archive(args.id)
    print(f"archived issue {issue.id} -> {issue.location.path}")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    findings = lint_mod.lint(root, fix=args.fix)
    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
    else:
        if not findings:
            print("lint: clean")
        else:
            for f in findings:
                print(str(f))
            print(f"\n{len(findings)} issue(s) found")
    return 1 if findings and not args.fix else 0


def cmd_block(args: argparse.Namespace) -> int:
    issue = service_mod.block(args.id, on=parse_id_list(args.on))
    print(f"issue {issue.id} now blocked_by {issue.blocked_by}")
    return 0


def cmd_unblock(args: argparse.Namespace) -> int:
    issue = service_mod.unblock(args.id, on=parse_id_list(args.on))
    print(f"issue {issue.id} now blocked_by {issue.blocked_by}")
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    index = store_mod.load_index(root)
    records = deps_mod.ready(
        index, config, feature=args.feature, parent=args.parent, unclaimed=args.unclaimed
    )
    print_records(records, args.json, index, config)
    return 0


def cmd_blocked(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    index = store_mod.load_index(root)
    pairs = deps_mod.blocked(index, config)
    if args.json:
        print(
            json.dumps(
                [
                    {**issue_to_dict(record), "unsatisfied_blocked_by": unsatisfied}
                    for record, unsatisfied in pairs
                ],
                indent=2,
            )
        )
        return 0
    if not pairs:
        print("(none)")
        return 0
    for record, _ in pairs:
        print(format_line(record, index, config))
    return 0


def cmd_blocking(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    index = store_mod.load_index(root)
    records = deps_mod.blocking(index, args.id)
    print_records(records, args.json, index, config)
    return 0


def cmd_children(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    index = store_mod.load_index(root)
    records = deps_mod.children(index, args.id)
    print_records(records, args.json, index, config)
    return 0


def cmd_parent(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    issue = store_mod.get_issue(root, args.id)
    if issue.parent is None:
        raise IssuesError(f"issue {args.id} has no parent")
    parent = store_mod.get_issue(root, issue.parent)
    _print_issue(parent, args.json)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if args.status is None:
        print(service_mod.show(args.id).status)
        return 0
    issue = service_mod.set_status(args.id, args.status, force=args.force)
    print(f"issue {issue.id} status -> {args.status}")
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    issue = service_mod.claim(args.id, args.as_name or getpass.getuser())
    print(f"issue {issue.id} claimed by {issue.assignee}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    index = store_mod.load_index(root)

    if args.all:
        targets = [
            issue.id
            for issue in index.values()
            if not issue.location.archived and issue.assignee
            and (not args.assignee or issue.assignee == args.assignee)
        ]
    else:
        if not args.ids:
            raise IssuesError("pass issue id(s) or --all")
        for issue_id in args.ids:
            if index.get(issue_id) is None:
                raise IssuesError(f"no issue with id {issue_id}")
        targets = list(args.ids)

    for issue_id in targets:
        service_mod.release(issue_id, keep_status=args.keep_status)

    ids = ", ".join(str(t) for t in sorted(targets)) or "(none)"
    print(f"released {len(targets)} issue(s): {ids}")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    answer = one_text_arg(args.answer, args.answer_flag, "the answer")
    issue = service_mod.resolve(args.id, answer=answer, status=args.status, force=args.force)
    print(f"issue {issue.id} resolved -> {issue.status}")
    return 0


def cmd_criteria(args: argparse.Namespace) -> int:
    issue, added = service_mod.edit_criteria(
        args.id, add=args.add, check=args.check, uncheck=args.uncheck, remove=args.remove
    )
    criteria = issue.acceptance_criteria

    if args.json:
        print(json.dumps(criteria, indent=2))
    elif added:
        # Print only what THIS call added (at its real position in the full
        # list), not the whole checklist — so one call with several --add
        # flags and several separate --add calls read the same in a
        # transcript, instead of each call re-echoing everything added so far.
        indices = [i for i, item in enumerate(criteria) if any(item is a for a in added)]
        print(model.render_criteria([criteria[i] for i in indices], indices))
    elif criteria:
        print(model.render_criteria(criteria))
    else:
        print("(no acceptance criteria)")
    return 0


def cmd_triage(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    index = store_mod.load_index(root)
    active = [r for r in index.values() if not r.location.archived]

    buckets = {
        "needs-triage": sorted(
            (r for r in active if r.status == "needs-triage"),
            key=lambda r: r.created,
        ),
        "needs-info": sorted(
            (r for r in active if r.status == "needs-info"),
            key=lambda r: r.created,
        ),
    }

    if args.json:
        print(
            json.dumps(
                {name: [issue_to_dict(r) for r in records] for name, records in buckets.items()},
                indent=2,
            )
        )
        return 0

    for name, records in buckets.items():
        print(f"{name} ({len(records)}):")
        if not records:
            print("  (none)")
        for record in records:
            print("  " + format_line(record, index, config))
    return 0


def cmd_help(args: argparse.Namespace) -> int:
    """`issues help [command]` — a discoverable alias for `--help`. With no
    topic it prints the top-level usage; with one it prints that subcommand's
    help. `_parser`/`_subparsers` are threaded in via set_defaults."""
    topic = args.topic
    if topic is None:
        args._parser.print_help()
        return 0
    subparser = args._subparsers.get(topic)
    if subparser is None:
        raise IssuesError(f"unknown command '{topic}'; run `issues help` for the list")
    subparser.print_help()
    return 0


# ---------------------------------------------------------------------------
# argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issues", description="Plain-text issue tracker.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create .scratch/ + config")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new", help="create a new issue")
    p.add_argument("feature")
    p.add_argument("title")
    p.add_argument("--slug")
    p.add_argument("--category")
    p.add_argument("--status")
    p.add_argument("--label", action="append")
    p.add_argument("--parent", type=int)
    p.add_argument("--blocked-by")
    p.add_argument("--assignee")
    p.add_argument("--criterion", action="append", help="acceptance criterion (repeatable)")
    p.add_argument("--body")
    p.add_argument("--force", action="store_true", help="skip transition/invariant checks")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("list", help="list issues")
    p.add_argument("--feature")
    p.add_argument("--status")
    p.add_argument("--category")
    p.add_argument("--label")
    p.add_argument("--assignee")
    p.add_argument("--parent", type=int)
    p.add_argument("--include-archived", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one or more issues")
    p.add_argument("ids", type=int, nargs="+", metavar="id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("edit", help="edit an issue")
    p.add_argument("id", type=int)
    p.add_argument("--title")
    p.add_argument("--slug")
    p.add_argument("--status")
    p.add_argument("--category")
    p.add_argument("--add-label", action="append")
    p.add_argument("--remove-label", action="append")
    p.add_argument("--parent", type=int)
    p.add_argument("--assignee")
    p.add_argument("--body")
    p.add_argument("--force", action="store_true", help="skip transition/invariant checks")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("comment", help="append a comment")
    p.add_argument("id", type=int)
    p.add_argument("body", nargs="?", help="comment text (or '-' to read stdin)")
    p.add_argument("--body", dest="body_flag", help="comment text; '-' reads stdin")
    p.set_defaults(func=cmd_comment)

    p = sub.add_parser("archive", help="archive an issue (or all done issues)")
    p.add_argument("id", type=int, nargs="?")
    p.add_argument("--done", action="store_true")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("lint", help="validate issue files")
    p.add_argument("--json", action="store_true")
    p.add_argument("--fix", action="store_true")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("block", help="add blockers to an issue")
    p.add_argument("id", type=int)
    p.add_argument("--on", required=True, help="comma-separated ids")
    p.set_defaults(func=cmd_block)

    p = sub.add_parser("unblock", help="remove blockers from an issue")
    p.add_argument("id", type=int)
    p.add_argument("--on", required=True, help="comma-separated ids")
    p.set_defaults(func=cmd_unblock)

    p = sub.add_parser("ready", help="show the ready frontier")
    p.add_argument("--feature")
    p.add_argument("--parent", type=int)
    p.add_argument("--unclaimed", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ready)

    p = sub.add_parser("blocked", help="show blocked issues")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_blocked)

    p = sub.add_parser("blocking", help="show issues blocked by <id>")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_blocking)

    p = sub.add_parser("children", help="show issues whose parent is <id>")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_children)

    p = sub.add_parser("parent", help="show the parent of <id>")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_parent)

    p = sub.add_parser("status", help="set an issue's status (or show it if none given)")
    p.add_argument("id", type=int)
    p.add_argument("status", nargs="?", help="new status; omit to print the current one")
    p.add_argument("--force", action="store_true", help="skip transition/invariant checks")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("claim", help="claim an issue")
    p.add_argument("id", type=int)
    p.add_argument("--as", dest="as_name")
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("release", help="clear claim(s) and reset status")
    p.add_argument("ids", type=int, nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--assignee")
    p.add_argument("--keep-status", action="store_true")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("resolve", help="mark an issue done with an optional answer comment")
    p.add_argument("id", type=int)
    p.add_argument("answer", nargs="?", help="answer comment (or '-' to read stdin)")
    p.add_argument("--answer", dest="answer_flag", help="answer comment; '-' reads stdin")
    p.add_argument("--status")
    p.add_argument("--force", action="store_true", help="skip transition/invariant checks")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("criteria", help="manage an issue's acceptance-criteria checklist")
    p.add_argument("id", type=int)
    p.add_argument("--add", action="append", help="add a criterion (repeatable)")
    p.add_argument("--check", action="append", type=int, metavar="N", help="mark criterion N done")
    p.add_argument("--uncheck", action="append", type=int, metavar="N", help="mark criterion N not done")
    p.add_argument("--remove", action="append", type=int, metavar="N", help="remove criterion N")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_criteria)

    p = sub.add_parser("triage", help="show issues needing triage attention")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_triage)

    p = sub.add_parser("help", help="show help for a command (or all commands)")
    p.add_argument("topic", nargs="?", help="command name to show help for")
    p.set_defaults(func=cmd_help, _parser=parser, _subparsers=sub.choices)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except IssuesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
