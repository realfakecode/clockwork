"""Load/save `.issues.yaml`, defaults, status buckets, id allocation."""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_FILENAME = ".issues.yaml"
SCRATCH_DIRNAME = ".scratch"

BUCKETS = ("todo", "active", "done")


def default_config() -> dict:
    return {
        "next_id": 1,
        "statuses": {
            "todo": ["needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "open"],
            # `needs-decision` is the escalation state: it lives in `active`, not
            # `todo`, so `issues ready` (todo bucket only) never re-dispatches an
            # escalated ticket while it waits on a human design decision.
            "active": ["in-progress", "needs-decision"],
            "done": ["done", "wontfix"],
        },
        "categories": ["bug", "enhancement"],
        "unclaim_status": "ready-for-agent",
        # Triage state machine: source status -> allowed target statuses. When
        # absent (older configs) transitions are unenforced. `--force` overrides.
        "transitions": {
            "needs-triage": ["needs-info", "ready-for-agent", "ready-for-human", "wontfix"],
            "needs-info": ["needs-triage", "wontfix"],
            "ready-for-agent": ["in-progress", "ready-for-human", "needs-info", "needs-decision", "wontfix"],
            "ready-for-human": ["in-progress", "ready-for-agent", "needs-info", "wontfix"],
            "in-progress": ["done", "ready-for-agent", "ready-for-human", "needs-decision", "wontfix"],
            # A design session drains `needs-decision` back onto the frontier.
            "needs-decision": ["ready-for-agent", "ready-for-human", "wontfix"],
            "open": ["in-progress", "ready-for-agent", "ready-for-human", "wontfix", "needs-info"],
            "done": [],
            "wontfix": ["needs-triage"],
        },
        # A triaged issue must carry a category before entering these states.
        "require_category": ["ready-for-agent", "ready-for-human", "wontfix"],
        # These states demand a non-empty acceptance-criteria checklist.
        "require_acceptance_criteria": ["ready-for-agent", "ready-for-human"],
    }


def scratch_dir(root: Path) -> Path:
    return root / SCRATCH_DIRNAME


def config_path(root: Path) -> Path:
    return scratch_dir(root) / CONFIG_FILENAME


def load_config(root: Path) -> dict:
    path = config_path(root)
    if not path.exists():
        raise FileNotFoundError(f"no config at {path}")
    data = yaml.safe_load(path.read_text()) or {}
    cfg = default_config()
    cfg.update(data)
    return cfg


def save_config(root: Path, config: dict) -> None:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False))


def all_statuses(config: dict) -> set[str]:
    statuses: set[str] = set()
    for bucket in BUCKETS:
        statuses.update(config.get("statuses", {}).get(bucket, []))
    return statuses


def status_bucket(config: dict, status: str) -> str | None:
    for bucket in BUCKETS:
        if status in config.get("statuses", {}).get(bucket, []):
            return bucket
    return None


def ordered_statuses(config: dict) -> list[str]:
    """All statuses in bucket order (todo, active, done), de-duplicated."""
    ordered: list[str] = []
    for bucket in BUCKETS:
        for status in config.get("statuses", {}).get(bucket, []):
            if status not in ordered:
                ordered.append(status)
    return ordered


def status_help(config: dict) -> str:
    return ", ".join(ordered_statuses(config))


def category_help(config: dict) -> str:
    return ", ".join(config.get("categories") or [])


def allowed_transitions(config: dict, frm: str) -> list[str]:
    return list(config.get("transitions", {}).get(frm, []))


def can_transition(config: dict, frm: str, to: str) -> bool:
    """True when the move is permitted. A no-op (frm == to) is always allowed,
    as is any move when no `transitions` map is configured."""
    if frm == to:
        return True
    transitions = config.get("transitions")
    if not transitions:
        return True
    return to in transitions.get(frm, [])


def requires_category(config: dict, status: str) -> bool:
    return status in (config.get("require_category") or [])


def requires_criteria(config: dict, status: str) -> bool:
    return status in (config.get("require_acceptance_criteria") or [])


def allocate_id(root: Path) -> int:
    config = load_config(root)
    issue_id = config["next_id"]
    config["next_id"] = issue_id + 1
    save_config(root, config)
    return issue_id
