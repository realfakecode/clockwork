"""Thin wrapper over the real `tracker` CLI (dogfooding the same interface the
worker uses). Every call shells out to `tracker … --json` and returns parsed
dicts; the loop makes decisions purely from the state observed here.

The command is `tracker` on PATH by default (installed alongside `harness` by the
same `uv tool install .`). Override with `HARNESS_TRACKER_CMD` for local dev,
e.g. `HARNESS_TRACKER_CMD="uv run tracker"`.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

ATTEMPTS_PREFIX = "attempts:"


class TrackerCliError(RuntimeError):
    """A `tracker` invocation exited non-zero."""

    def __init__(self, argv: list[str], returncode: int, stderr: str):
        super().__init__(f"tracker {' '.join(argv)} exited {returncode}: {stderr.strip()}")
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr


def _base_cmd() -> list[str]:
    return shlex.split(os.environ.get("HARNESS_TRACKER_CMD", "tracker"))


def _run(args: list[str], *, cwd: str | Path | None = None) -> str:
    argv = [*_base_cmd(), *args]
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise TrackerCliError(args, proc.returncode, proc.stderr)
    return proc.stdout


def _run_json(args: list[str], *, cwd: str | Path | None = None):
    return json.loads(_run([*args, "--json"], cwd=cwd))


# -- reads -----------------------------------------------------------------


def ready_unclaimed(cwd: str | Path | None = None, *, feature: str | None = None) -> list[dict]:
    args = ["ready", "--unclaimed"]
    if feature:
        args += ["--feature", feature]
    return _run_json(args, cwd=cwd)


def list_status(status: str, cwd: str | Path | None = None) -> list[dict]:
    return _run_json(["list", "--status", status], cwd=cwd)


def show(issue_id: int, cwd: str | Path | None = None) -> dict:
    return _run_json(["show", str(issue_id)], cwd=cwd)


# -- writes ----------------------------------------------------------------


def set_status(issue_id: int, status: str, cwd: str | Path | None = None) -> None:
    _run(["status", str(issue_id), status], cwd=cwd)


def claim(issue_id: int, as_name: str, cwd: str | Path | None = None) -> None:
    _run(["claim", str(issue_id), "--as", as_name], cwd=cwd)


def release(issue_id: int, cwd: str | Path | None = None, *, keep_status: bool = False) -> None:
    args = ["release", str(issue_id)]
    if keep_status:
        args.append("--keep-status")
    _run(args, cwd=cwd)


def comment(issue_id: int, body: str, cwd: str | Path | None = None) -> None:
    # Pass the body via stdin (--body -) so newlines/quotes survive intact.
    argv = [*_base_cmd(), "comment", str(issue_id), "--body", "-"]
    proc = subprocess.run(argv, cwd=cwd, input=body, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TrackerCliError(argv, proc.returncode, proc.stderr)


def check_criterion(issue_id: int, index: int, cwd: str | Path | None = None) -> None:
    _run(["criteria", str(issue_id), "--check", str(index)], cwd=cwd)


def resolve(issue_id: int, cwd: str | Path | None = None, *, answer: str | None = None) -> None:
    args = ["resolve", str(issue_id)]
    if answer:
        args += ["--answer", answer]
    _run(args, cwd=cwd)


def edit_labels(
    issue_id: int,
    cwd: str | Path | None = None,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> None:
    args = ["edit", str(issue_id)]
    for label in remove or []:
        args += ["--remove-label", label]
    for label in add or []:
        args += ["--add-label", label]
    _run(args, cwd=cwd)


# -- attempt-counter label helpers ----------------------------------------


def read_attempts(issue: dict) -> int:
    """Current `attempts:N` value from an issue's labels (0 if absent)."""
    for label in issue.get("labels") or []:
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
