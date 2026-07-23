"""argparse-based CLI for the issue tracker."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from . import deps as deps_mod
from . import lint as lint_mod
from . import model
from . import store as store_mod
from .store import IssueRecord, IssuesError


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


def record_to_dict(record: IssueRecord, *, include_body: bool = False) -> dict:
    issue = record.issue
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
        "feature": record.feature,
        "archived": record.archived,
        "path": str(record.path),
    }
    if include_body:
        data["body"] = issue.body
    return data


def format_line(
    record: IssueRecord,
    index: dict[int, IssueRecord] | None = None,
    config: dict | None = None,
) -> str:
    issue = record.issue
    bits = [f"#{issue.id}", issue.status, f"[{record.feature}]", issue.title]
    extras = []
    if issue.assignee:
        extras.append(f"assignee={issue.assignee}")
    if issue.labels:
        extras.append("labels=" + ",".join(issue.labels))
    if issue.blocked_by:
        # Show only blockers that are still unresolved. Without index+config we
        # can't tell, so fall back to the raw declared list.
        if index is not None and config is not None:
            blocking = deps_mod.unsatisfied_blockers(record, index, config)
        else:
            blocking = list(issue.blocked_by)
        if blocking:
            extras.append("blocked_by=" + ",".join(str(b) for b in blocking))
    if record.archived:
        extras.append("archived")
    line = "  ".join(bits)
    if extras:
        line += "  (" + " ".join(extras) + ")"
    return line


def print_records(
    records: list[IssueRecord],
    as_json: bool,
    index: dict[int, IssueRecord] | None = None,
    config: dict | None = None,
) -> None:
    if as_json:
        print(json.dumps([record_to_dict(r) for r in records], indent=2))
        return
    if not records:
        print("(none)")
        return
    for record in records:
        print(format_line(record, index, config))


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def check_status_known(config: dict, status: str) -> None:
    if status not in config_mod.all_statuses(config):
        raise IssuesError(
            f"unknown status '{status}'; accepted: {config_mod.status_help(config)}"
        )


def check_category_known(config: dict, category: str | None) -> None:
    if category is not None and category not in (config.get("categories") or []):
        raise IssuesError(
            f"unknown category '{category}'; accepted: {config_mod.category_help(config)}"
        )


def check_transition(config: dict, current: str, target: str) -> None:
    if not config_mod.can_transition(config, current, target):
        allowed = config_mod.allowed_transitions(config, current)
        allowed_str = ", ".join(allowed) if allowed else "(none)"
        raise IssuesError(
            f"cannot move issue from '{current}' to '{target}'; "
            f"allowed from '{current}': {allowed_str} (use --force to override)"
        )


def check_invariants(config: dict, status: str, category: str | None, criteria: list[dict]) -> None:
    if config_mod.requires_category(config, status) and not category:
        raise IssuesError(
            f"status '{status}' requires a category; set one of: "
            f"{config_mod.category_help(config)} (use --force to override)"
        )
    if config_mod.requires_criteria(config, status) and not criteria:
        raise IssuesError(
            f"status '{status}' requires at least one acceptance criterion — add with "
            "`issues criteria <id> --add ...` or `issues new ... --criterion ...` "
            "(use --force to override)"
        )


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = store_mod.init_repo()
    print(f"initialized issue tracker at {config_mod.scratch_dir(root)}")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    status = args.status
    if status is None:
        todo = config.get("statuses", {}).get("todo") or []
        if not todo:
            raise IssuesError("no default status available; pass --status")
        status = todo[0]
    check_status_known(config, status)
    check_category_known(config, args.category)

    criteria: list[dict] = []
    for text in args.criterion or []:
        model.add_criterion(criteria, text)

    if not args.force:
        check_invariants(config, status, args.category, criteria)

    record = store_mod.create_issue(
        root,
        args.feature,
        args.title,
        slug=args.slug,
        status=status,
        category=args.category,
        labels=args.label or [],
        parent=args.parent,
        blocked_by=parse_id_list(args.blocked_by),
        assignee=args.assignee,
        acceptance_criteria=criteria,
        body=read_text_arg(args.body),
    )
    if args.json:
        print(json.dumps(record_to_dict(record), indent=2))
    else:
        print(f"created issue {record.id}: {record.path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    index = store_mod.load_index(root)
    records = list(index.values())

    if not args.include_archived:
        records = [r for r in records if not r.archived]
    if args.feature:
        records = [r for r in records if r.feature == args.feature]
    if args.status:
        records = [r for r in records if r.issue.status == args.status]
    if args.category:
        records = [r for r in records if r.issue.category == args.category]
    if args.label:
        records = [r for r in records if args.label in r.issue.labels]
    if args.assignee:
        records = [r for r in records if r.issue.assignee == args.assignee]
    if args.parent is not None:
        records = [r for r in records if r.issue.parent == args.parent]

    records.sort(key=lambda r: r.id)
    print_records(records, args.json, index, config)
    return 0


def _print_issue(record: IssueRecord, as_json: bool) -> None:
    if as_json:
        print(json.dumps(record_to_dict(record, include_body=True), indent=2))
        return
    issue = record.issue
    print(model.serialize_issue(issue, include_criteria=False), end="")
    if issue.acceptance_criteria:
        print("\nAcceptance criteria:")
        print(model.render_criteria(issue.acceptance_criteria))
    print(f"\n(feature: {record.feature}, path: {record.path}, archived: {record.archived})")


def cmd_show(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    record = store_mod.get_issue(root, args.id)
    _print_issue(record, args.json)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    record = store_mod.get_issue(root, args.id)
    issue = record.issue

    if args.title is not None:
        issue.title = args.title
    if args.slug is not None:
        issue.slug = args.slug
    if args.category is not None:
        check_category_known(config, args.category)
        issue.category = args.category
    if args.status is not None:
        check_status_known(config, args.status)
        if not args.force:
            check_transition(config, issue.status, args.status)
            check_invariants(config, args.status, issue.category, issue.acceptance_criteria)
        issue.status = args.status
    if args.parent is not None:
        issue.parent = args.parent
    if args.assignee is not None:
        issue.assignee = args.assignee
    body_text = read_text_arg(args.body)
    if body_text is not None:
        issue.body = body_text if body_text.endswith("\n") else body_text + "\n"

    for label in args.add_label or []:
        if label not in issue.labels:
            issue.labels.append(label)
    for label in args.remove_label or []:
        if label in issue.labels:
            issue.labels.remove(label)

    store_mod.write_issue(record)
    print(f"updated issue {issue.id}")
    return 0


def cmd_comment(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    record = store_mod.get_issue(root, args.id)
    text = read_text_arg(args.body)
    if not text or not text.strip():
        raise IssuesError("comment body is empty (pass --body or pipe via --body -)")
    now = datetime.now().replace(microsecond=0)
    record.issue.body = model.append_comment(record.issue.body, text.strip(), now)
    store_mod.write_issue(record)
    print(f"commented on issue {record.id}")
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)

    if args.done:
        index = store_mod.load_index(root)
        targets = [
            r.id for r in index.values() if not r.archived and deps_mod.is_done(r, config)
        ]
        targets.sort()
        for issue_id in targets:
            store_mod.archive_issue(root, issue_id)
        print(f"archived {len(targets)} issue(s): {', '.join(str(t) for t in targets) or '(none)'}")
        return 0

    if args.id is None:
        raise IssuesError("pass an issue id or --done")
    record = store_mod.archive_issue(root, args.id)
    print(f"archived issue {record.id} -> {record.path}")
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
    root = store_mod.find_root()
    record = store_mod.get_issue(root, args.id)
    add = parse_id_list(args.on)
    for issue_id in add:
        if issue_id not in record.issue.blocked_by:
            record.issue.blocked_by.append(issue_id)
    store_mod.write_issue(record)
    print(f"issue {record.id} now blocked_by {record.issue.blocked_by}")
    return 0


def cmd_unblock(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    record = store_mod.get_issue(root, args.id)
    remove = set(parse_id_list(args.on))
    record.issue.blocked_by = [b for b in record.issue.blocked_by if b not in remove]
    store_mod.write_issue(record)
    print(f"issue {record.id} now blocked_by {record.issue.blocked_by}")
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
                    {**record_to_dict(record), "unsatisfied_blocked_by": unsatisfied}
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
    record = store_mod.get_issue(root, args.id)
    if record.issue.parent is None:
        raise IssuesError(f"issue {args.id} has no parent")
    parent_record = store_mod.get_issue(root, record.issue.parent)
    _print_issue(parent_record, args.json)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    check_status_known(config, args.status)
    record = store_mod.get_issue(root, args.id)
    issue = record.issue
    if not args.force:
        check_transition(config, issue.status, args.status)
        check_invariants(config, args.status, issue.category, issue.acceptance_criteria)
    issue.status = args.status
    store_mod.write_issue(record)
    print(f"issue {record.id} status -> {args.status}")
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    record = store_mod.get_issue(root, args.id)
    record.issue.assignee = args.as_name or getpass.getuser()
    store_mod.write_issue(record)
    print(f"issue {record.id} claimed by {record.issue.assignee}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    index = store_mod.load_index(root)

    if args.all:
        targets = [
            r
            for r in index.values()
            if not r.archived and r.issue.assignee and (not args.assignee or r.issue.assignee == args.assignee)
        ]
    else:
        if not args.ids:
            raise IssuesError("pass issue id(s) or --all")
        targets = []
        for issue_id in args.ids:
            record = index.get(issue_id)
            if record is None:
                raise IssuesError(f"no issue with id {issue_id}")
            targets.append(record)

    for record in targets:
        record.issue.assignee = None
        if not args.keep_status and config_mod.status_bucket(config, record.issue.status) != "done":
            record.issue.status = config["unclaim_status"]
        store_mod.write_issue(record)

    ids = ", ".join(str(r.id) for r in sorted(targets, key=lambda r: r.id)) or "(none)"
    print(f"released {len(targets)} issue(s): {ids}")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    status = args.status or "done"
    check_status_known(config, status)
    record = store_mod.get_issue(root, args.id)
    issue = record.issue
    if not args.force:
        check_transition(config, issue.status, status)
        check_invariants(config, status, issue.category, issue.acceptance_criteria)
    answer = read_text_arg(args.answer)
    if answer and answer.strip():
        now = datetime.now().replace(microsecond=0)
        issue.body = model.append_comment(issue.body, answer.strip(), now)
    issue.status = status
    store_mod.write_issue(record)
    print(f"issue {record.id} resolved -> {status}")
    return 0


def cmd_criteria(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    record = store_mod.get_issue(root, args.id)
    criteria = record.issue.acceptance_criteria
    changed = False

    for text in args.add or []:
        model.add_criterion(criteria, text)
        changed = True
    for index in args.check or []:
        model.set_criterion_done(criteria, index, True)
        changed = True
    for index in args.uncheck or []:
        model.set_criterion_done(criteria, index, False)
        changed = True
    # Remove in descending order so earlier indices stay valid.
    for index in sorted(args.remove or [], reverse=True):
        model.remove_criterion(criteria, index)
        changed = True

    if changed:
        store_mod.write_issue(record)

    if args.json:
        print(json.dumps(criteria, indent=2))
    elif criteria:
        print(model.render_criteria(criteria))
    else:
        print("(no acceptance criteria)")
    return 0


def cmd_triage(args: argparse.Namespace) -> int:
    root = store_mod.find_root()
    config = config_mod.load_config(root)
    index = store_mod.load_index(root)
    active = [r for r in index.values() if not r.archived]

    buckets = {
        "needs-triage": sorted(
            (r for r in active if r.issue.status == "needs-triage"),
            key=lambda r: r.issue.created,
        ),
        "needs-info": sorted(
            (r for r in active if r.issue.status == "needs-info"),
            key=lambda r: r.issue.created,
        ),
    }

    if args.json:
        print(
            json.dumps(
                {name: [record_to_dict(r) for r in records] for name, records in buckets.items()},
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

    p = sub.add_parser("show", help="show one issue")
    p.add_argument("id", type=int)
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
    p.add_argument("--body")
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

    p = sub.add_parser("status", help="set an issue's status")
    p.add_argument("id", type=int)
    p.add_argument("status")
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
    p.add_argument("--answer")
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
