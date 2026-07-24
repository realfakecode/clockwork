"""Tracker operations, callable in-process.

Both entry points go through here: the `issues` CLI renders what these return,
and the orchestrator imports them directly. Every write funnels through one of
these functions, so transition and invariant checks have a single home and both
callers enforce identical rules. Reads return typed `Issue` objects (with their
`location` set); mutations return the affected `Issue`. Errors raise
`issues.store.IssuesError`.

Inputs are already-parsed values (ids as ints, bodies as strings) — argument
parsing (stdin `-`, comma-separated id lists) stays in the CLI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import config as config_mod
from . import deps
from . import model
from . import store
from .model import Issue
from .store import IssuesError

__all__ = [
    "IssuesError",
    "check_status_known",
    "check_category_known",
    "check_transition",
    "check_invariants",
    "show",
    "children",
    "list_status",
    "ready_unclaimed",
    "new",
    "edit",
    "set_status",
    "claim",
    "release",
    "comment",
    "resolve",
    "edit_labels",
    "block",
    "unblock",
    "archive",
    "archive_done",
    "edit_criteria",
    "check_criterion",
]


# -- validation ------------------------------------------------------------


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


# -- reads (the orchestrator's view) ---------------------------------------


def show(issue_id: int, cwd: str | Path | None = None) -> Issue:
    return store.get_issue(store.find_root(cwd), issue_id)


def children(issue_id: int, cwd: str | Path | None = None) -> list[Issue]:
    """Direct children (issues whose `parent` is `issue_id`), id-sorted."""
    root = store.find_root(cwd)
    return deps.children(store.load_index(root), issue_id)


def list_status(status: str, cwd: str | Path | None = None) -> list[Issue]:
    """Unarchived issues with the given status, id-sorted."""
    root = store.find_root(cwd)
    results = [
        issue
        for issue in store.load_index(root).values()
        if not issue.location.archived and issue.status == status
    ]
    results.sort(key=lambda i: i.id)
    return results


def ready_unclaimed(cwd: str | Path | None = None, *, feature: str | None = None) -> list[Issue]:
    root = store.find_root(cwd)
    config = config_mod.load_config(root)
    return deps.ready(store.load_index(root), config, feature=feature, unclaimed=True)


# -- mutations -------------------------------------------------------------


def new(
    cwd: str | Path | None = None,
    *,
    feature: str,
    title: str,
    slug: str | None = None,
    status: str | None = None,
    category: str | None = None,
    labels: list[str] | None = None,
    parent: int | None = None,
    blocked_by: list[int] | None = None,
    assignee: str | None = None,
    criteria_texts: list[str] | None = None,
    body: str | None = None,
    force: bool = False,
) -> Issue:
    root = store.find_root(cwd)
    config = config_mod.load_config(root)
    if status is None:
        todo = config.get("statuses", {}).get("todo") or []
        if not todo:
            raise IssuesError("no default status available; pass --status")
        status = todo[0]
    check_status_known(config, status)
    check_category_known(config, category)

    criteria: list[dict] = []
    for text in criteria_texts or []:
        model.add_criterion(criteria, text)

    if not force:
        check_invariants(config, status, category, criteria)

    return store.create_issue(
        root,
        feature,
        title,
        slug=slug,
        status=status,
        category=category,
        labels=labels or [],
        parent=parent,
        blocked_by=blocked_by or [],
        assignee=assignee,
        acceptance_criteria=criteria,
        body=body,
    )


def edit(
    issue_id: int,
    cwd: str | Path | None = None,
    *,
    title: str | None = None,
    slug: str | None = None,
    status: str | None = None,
    category: str | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
    parent: int | None = None,
    assignee: str | None = None,
    body: str | None = None,
    force: bool = False,
) -> Issue:
    root = store.find_root(cwd)
    config = config_mod.load_config(root)
    issue = store.get_issue(root, issue_id)

    if title is not None:
        issue.title = title
    if slug is not None:
        issue.slug = slug
    if category is not None:
        check_category_known(config, category)
        issue.category = category
    if status is not None:
        check_status_known(config, status)
        if not force:
            check_transition(config, issue.status, status)
            check_invariants(config, status, issue.category, issue.acceptance_criteria)
        issue.status = status
    if parent is not None:
        issue.parent = parent
    if assignee is not None:
        issue.assignee = assignee
    if body is not None:
        issue.body = body if body.endswith("\n") else body + "\n"

    for label in add_labels or []:
        if label not in issue.labels:
            issue.labels.append(label)
    for label in remove_labels or []:
        if label in issue.labels:
            issue.labels.remove(label)

    store.write_issue(issue)
    return issue


def set_status(issue_id: int, status: str, cwd: str | Path | None = None, *, force: bool = False) -> Issue:
    root = store.find_root(cwd)
    config = config_mod.load_config(root)
    check_status_known(config, status)
    issue = store.get_issue(root, issue_id)
    if not force:
        check_transition(config, issue.status, status)
        check_invariants(config, status, issue.category, issue.acceptance_criteria)
    issue.status = status
    store.write_issue(issue)
    return issue


def claim(issue_id: int, as_name: str, cwd: str | Path | None = None) -> Issue:
    root = store.find_root(cwd)
    issue = store.get_issue(root, issue_id)
    issue.assignee = as_name
    store.write_issue(issue)
    return issue


def release(issue_id: int, cwd: str | Path | None = None, *, keep_status: bool = False) -> Issue:
    """Clear an issue's claim. Unless `keep_status`, a still-open issue resets to
    the configured `unclaim_status` so it re-enters the frontier; a done issue keeps
    its status."""
    root = store.find_root(cwd)
    config = config_mod.load_config(root)
    issue = store.get_issue(root, issue_id)
    issue.assignee = None
    if not keep_status and config_mod.status_bucket(config, issue.status) != "done":
        issue.status = config["unclaim_status"]
    store.write_issue(issue)
    return issue


def comment(issue_id: int, body: str, cwd: str | Path | None = None) -> Issue:
    root = store.find_root(cwd)
    issue = store.get_issue(root, issue_id)
    if not body or not body.strip():
        raise IssuesError("comment body is empty")
    now = datetime.now().replace(microsecond=0)
    issue.body = model.append_comment(issue.body, body.strip(), now)
    store.write_issue(issue)
    return issue


def resolve(
    issue_id: int,
    cwd: str | Path | None = None,
    *,
    answer: str | None = None,
    status: str | None = None,
    force: bool = False,
) -> Issue:
    root = store.find_root(cwd)
    config = config_mod.load_config(root)
    status = status or "done"
    check_status_known(config, status)
    issue = store.get_issue(root, issue_id)
    if not force:
        check_transition(config, issue.status, status)
        check_invariants(config, status, issue.category, issue.acceptance_criteria)
    if answer and answer.strip():
        now = datetime.now().replace(microsecond=0)
        issue.body = model.append_comment(issue.body, answer.strip(), now)
    issue.status = status
    store.write_issue(issue)
    return issue


def edit_labels(
    issue_id: int,
    cwd: str | Path | None = None,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> Issue:
    root = store.find_root(cwd)
    issue = store.get_issue(root, issue_id)
    for label in add or []:
        if label not in issue.labels:
            issue.labels.append(label)
    for label in remove or []:
        if label in issue.labels:
            issue.labels.remove(label)
    store.write_issue(issue)
    return issue


def block(issue_id: int, cwd: str | Path | None = None, *, on: list[int]) -> Issue:
    root = store.find_root(cwd)
    issue = store.get_issue(root, issue_id)
    for blocker_id in on:
        if blocker_id not in issue.blocked_by:
            issue.blocked_by.append(blocker_id)
    store.write_issue(issue)
    return issue


def unblock(issue_id: int, cwd: str | Path | None = None, *, on: list[int]) -> Issue:
    root = store.find_root(cwd)
    issue = store.get_issue(root, issue_id)
    remove = set(on)
    issue.blocked_by = [b for b in issue.blocked_by if b not in remove]
    store.write_issue(issue)
    return issue


def archive(issue_id: int, cwd: str | Path | None = None) -> Issue:
    return store.archive_issue(store.find_root(cwd), issue_id)


def archive_done(cwd: str | Path | None = None) -> list[int]:
    """Archive every unarchived done-bucket issue; return the archived ids, id-sorted."""
    root = store.find_root(cwd)
    config = config_mod.load_config(root)
    index = store.load_index(root)
    targets = sorted(
        issue.id for issue in index.values()
        if not issue.location.archived and deps.is_done(issue, config)
    )
    for issue_id in targets:
        store.archive_issue(root, issue_id)
    return targets


def edit_criteria(
    issue_id: int,
    cwd: str | Path | None = None,
    *,
    add: list[str] | None = None,
    check: list[int] | None = None,
    uncheck: list[int] | None = None,
    remove: list[int] | None = None,
) -> tuple[Issue, list[dict]]:
    """Apply criteria mutations and return `(issue, added)` — `added` is the items
    this call appended, so a caller can render only what it added."""
    root = store.find_root(cwd)
    issue = store.get_issue(root, issue_id)
    criteria = issue.acceptance_criteria
    changed = False
    added: list[dict] = []

    for text in add or []:
        model.add_criterion(criteria, text)
        added.append(criteria[-1])
        changed = True
    for index in check or []:
        model.set_criterion_done(criteria, index, True)
        changed = True
    for index in uncheck or []:
        model.set_criterion_done(criteria, index, False)
        changed = True
    # Remove in descending order so earlier indices stay valid.
    for index in sorted(remove or [], reverse=True):
        model.remove_criterion(criteria, index)
        changed = True

    if changed:
        store.write_issue(issue)
    return issue, added


def check_criterion(issue_id: int, index: int, cwd: str | Path | None = None) -> Issue:
    issue, _ = edit_criteria(issue_id, cwd, check=[index])
    return issue
