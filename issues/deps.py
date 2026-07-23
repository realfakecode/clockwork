"""Dependency resolution: ready / blocked / blocking / cycle detection."""

from __future__ import annotations

from . import config as config_mod
from .store import IssueRecord


def is_done(record: IssueRecord, config: dict) -> bool:
    return config_mod.status_bucket(config, record.issue.status) == "done"


def is_todo(record: IssueRecord, config: dict) -> bool:
    return config_mod.status_bucket(config, record.issue.status) == "todo"


def is_active_status(record: IssueRecord, config: dict) -> bool:
    return config_mod.status_bucket(config, record.issue.status) == "active"


def unsatisfied_blockers(
    record: IssueRecord, index: dict[int, IssueRecord], config: dict
) -> list[int]:
    unsatisfied = []
    for blocker_id in record.issue.blocked_by:
        blocker = index.get(blocker_id)
        if blocker is None or not is_done(blocker, config):
            unsatisfied.append(blocker_id)
    return unsatisfied


def blockers_satisfied(record: IssueRecord, index: dict[int, IssueRecord], config: dict) -> bool:
    return not unsatisfied_blockers(record, index, config)


def ready(
    index: dict[int, IssueRecord],
    config: dict,
    *,
    feature: str | None = None,
    parent: int | None = None,
    unclaimed: bool = False,
) -> list[IssueRecord]:
    results = []
    for record in index.values():
        if record.archived:
            continue
        if not is_todo(record, config):
            continue
        if feature is not None and record.feature != feature:
            continue
        if parent is not None and record.issue.parent != parent:
            continue
        if unclaimed and record.issue.assignee:
            continue
        if not blockers_satisfied(record, index, config):
            continue
        results.append(record)
    results.sort(key=lambda r: r.id)
    return results


def blocked(
    index: dict[int, IssueRecord], config: dict
) -> list[tuple[IssueRecord, list[int]]]:
    results = []
    for record in index.values():
        if record.archived:
            continue
        if not (is_todo(record, config) or is_active_status(record, config)):
            continue
        unsatisfied = unsatisfied_blockers(record, index, config)
        if unsatisfied:
            results.append((record, unsatisfied))
    results.sort(key=lambda pair: pair[0].id)
    return results


def blocking(index: dict[int, IssueRecord], issue_id: int) -> list[IssueRecord]:
    results = [
        record
        for record in index.values()
        if not record.archived and issue_id in record.issue.blocked_by
    ]
    results.sort(key=lambda r: r.id)
    return results


def children(index: dict[int, IssueRecord], issue_id: int) -> list[IssueRecord]:
    results = [
        record
        for record in index.values()
        if not record.archived and record.issue.parent == issue_id
    ]
    results.sort(key=lambda r: r.id)
    return results


def find_cycle(index: dict[int, IssueRecord], edge_attr: str) -> list[int] | None:
    """DFS cycle detection over the graph formed by `edge_attr`
    ('blocked_by' -> list of ids, or 'parent' -> single id or None).
    Returns the cycle as a list of ids, or None.
    """

    def neighbors(node_id: int) -> list[int]:
        record = index.get(node_id)
        if record is None:
            return []
        value = getattr(record.issue, edge_attr)
        if edge_attr == "parent":
            return [value] if value is not None else []
        return list(value)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node_id: WHITE for node_id in index}
    stack_path: list[int] = []

    def visit(node_id: int) -> list[int] | None:
        color[node_id] = GRAY
        stack_path.append(node_id)
        for nxt in neighbors(node_id):
            if nxt not in color:
                continue  # dangling reference; reported separately by lint
            if color[nxt] == GRAY:
                cycle_start = stack_path.index(nxt)
                return stack_path[cycle_start:] + [nxt]
            if color[nxt] == WHITE:
                result = visit(nxt)
                if result is not None:
                    return result
        stack_path.pop()
        color[node_id] = BLACK
        return None

    for node_id in list(index):
        if color[node_id] == WHITE:
            result = visit(node_id)
            if result is not None:
                return result
    return None
