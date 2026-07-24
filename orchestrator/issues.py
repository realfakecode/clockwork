"""In-process access to the `issues` tracker.

The orchestrator and the `issues` CLI share one service layer (`issues.service`).
This module re-exports the operations the loop uses — so the `issues.*` namespace
it calls resolves to real, traceable functions — and adds the retry/milestone
label bookkeeping the tracker itself knows nothing about.

Reads return typed `Issue` objects; the loop decides everything from the state
observed here. (`orchestrator.issues` and the top-level `issues` package are
distinct modules; the absolute import below resolves to the package.)
"""

from __future__ import annotations

from pathlib import Path

from issues.model import Issue
from issues.service import (
    IssuesError,
    check_criterion,
    children,
    claim,
    comment,
    edit_labels,
    list_status,
    ready_unclaimed,
    release,
    resolve,
    set_status,
    show,
)

__all__ = [
    "Issue",
    "IssuesError",
    "check_criterion",
    "children",
    "claim",
    "comment",
    "edit_labels",
    "list_status",
    "ready_unclaimed",
    "release",
    "resolve",
    "set_status",
    "show",
    "read_attempts",
    "bump_attempts",
    "read_numbered_label",
    "set_numbered_label",
    "clear_numbered_label",
    "add_label",
    "ATTEMPTS_PREFIX",
    "TERMINAL_STATUSES",
    "MILESTONE_ROUND_PREFIX",
    "MILESTONE_REVIEWED_PREFIX",
    "MILESTONE_BLOCKED_LABEL",
]

ATTEMPTS_PREFIX = "attempts:"


# -- attempt-counter label helpers ----------------------------------------


def read_attempts(issue: Issue) -> int:
    """Current `attempts:N` value from an issue's labels (0 if absent)."""
    for label in issue.labels:
        if label.startswith(ATTEMPTS_PREFIX):
            try:
                return int(label[len(ATTEMPTS_PREFIX):])
            except ValueError:
                continue
    return 0


def bump_attempts(issue_id: int, current: int, cwd: str | Path | None = None) -> int:
    """Swap the `attempts:N` label for `attempts:N+1` and return the new count."""
    new = current + 1
    remove = [f"{ATTEMPTS_PREFIX}{current}"] if current > 0 else None
    edit_labels(issue_id, cwd, remove=remove, add=[f"{ATTEMPTS_PREFIX}{new}"])
    return new


# -- milestone-review label helpers ---------------------------------------
#
# A wayfinding map carries two counters and one flag, all as labels so tracker
# state stays the single source of truth (same reason `attempts:N` lives here):
#   milestone-round:N     — review rounds fired since the last clean pass; the
#                           convergence backstop reads it.
#   milestone-reviewed:N  — child count at the last CLEAN review. The frontier
#                           re-fires only once it grows past this, so a clean map
#                           stays quiet while fix tickets or graduated fog reopen it.
#   milestone-blocked     — set when a map exhausts its rounds without converging,
#                           so the loop stops re-reviewing it and a human steps in.

TERMINAL_STATUSES = ("done", "wontfix")
MILESTONE_ROUND_PREFIX = "milestone-round:"
MILESTONE_REVIEWED_PREFIX = "milestone-reviewed:"
MILESTONE_BLOCKED_LABEL = "milestone-blocked"


def read_numbered_label(issue: Issue, prefix: str) -> int:
    """Value of a `prefix<N>` label on the issue (0 if absent or unparseable)."""
    for label in issue.labels:
        if label.startswith(prefix):
            try:
                return int(label[len(prefix):])
            except ValueError:
                continue
    return 0


def set_numbered_label(issue: Issue, prefix: str, value: int, cwd: str | Path | None = None) -> None:
    """Replace every `prefix<n>` label on the issue with a single `prefix<value>`.
    `issue` supplies the labels to strip, so pass a freshly-read issue — a stale
    snapshot would leave an orphaned counter behind."""
    remove = [label for label in issue.labels if label.startswith(prefix)]
    edit_labels(issue.id, cwd, remove=remove or None, add=[f"{prefix}{value}"])


def clear_numbered_label(issue: Issue, prefix: str, cwd: str | Path | None = None) -> None:
    """Drop every `prefix<n>` label from the issue (no-op when none are present)."""
    remove = [label for label in issue.labels if label.startswith(prefix)]
    if remove:
        edit_labels(issue.id, cwd, remove=remove)


def add_label(issue_id: int, label: str, cwd: str | Path | None = None) -> None:
    edit_labels(issue_id, cwd, add=[label])
