"""Lint rules for issue files: format, id integrity, dependency graph sanity."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from . import config as config_mod
from . import deps as deps_mod
from . import model
from . import store as store_mod


@dataclass
class LintFinding:
    path: Path | None
    issue_id: int | None
    rule: str
    message: str

    def to_dict(self) -> dict:
        return {
            "path": str(self.path) if self.path else None,
            "id": self.issue_id,
            "rule": self.rule,
            "message": self.message,
        }

    def __str__(self) -> str:
        loc = str(self.path) if self.path else f"id {self.issue_id}"
        return f"[{self.rule}] {loc}: {self.message}"


def lint(root: Path, fix: bool = False) -> list[LintFinding]:
    findings: list[LintFinding] = []
    config = config_mod.load_config(root)

    parsed: list[tuple[Path, str, bool, model.Issue | None, Exception | None]] = []
    for path, feature, archived in store_mod.iter_issue_files(root):
        try:
            issue = model.parse_issue(path)
            parsed.append((path, feature, archived, issue, None))
        except model.ParseError as exc:
            parsed.append((path, feature, archived, None, exc))
            findings.append(LintFinding(path, None, "parse", str(exc)))

    valid = [(p, f, a, i) for (p, f, a, i, e) in parsed if i is not None]

    # id uniqueness
    by_id: dict[int, list[Path]] = defaultdict(list)
    for path, _feature, _archived, issue in valid:
        by_id[issue.id].append(path)
    for issue_id, paths in by_id.items():
        if len(paths) > 1:
            for path in paths:
                findings.append(
                    LintFinding(path, issue_id, "duplicate-id", f"id {issue_id} used by {len(paths)} files")
                )

    statuses = config_mod.all_statuses(config)
    categories = set(config.get("categories") or [])

    all_ids = set(by_id.keys())

    for path, _feature, _archived, issue in valid:
        # body present + H1 title
        if not issue.body.strip():
            findings.append(LintFinding(path, issue.id, "empty-body", "issue body is empty"))
        elif not model.body_has_h1(issue.body):
            findings.append(LintFinding(path, issue.id, "missing-title", "body has no H1 title"))

        # filename == <id>-<slug>.md
        expected_name = model.issue_filename(issue.id, issue.slug)
        if path.name != expected_name:
            findings.append(
                LintFinding(
                    path, issue.id, "filename-mismatch",
                    f"filename '{path.name}' does not match '{expected_name}'",
                )
            )

        # status / category validity
        if issue.status not in statuses:
            findings.append(
                LintFinding(path, issue.id, "unknown-status", f"status '{issue.status}' not in configured set")
            )
        if issue.category is not None and issue.category not in categories:
            findings.append(
                LintFinding(
                    path, issue.id, "unknown-category",
                    f"category '{issue.category}' not in configured set",
                )
            )

        # triage invariants: routed states need a category + acceptance criteria
        if config_mod.requires_category(config, issue.status) and not issue.category:
            findings.append(
                LintFinding(
                    path, issue.id, "missing-category",
                    f"status '{issue.status}' requires a category",
                )
            )
        if config_mod.requires_criteria(config, issue.status) and not issue.acceptance_criteria:
            findings.append(
                LintFinding(
                    path, issue.id, "missing-criteria",
                    f"status '{issue.status}' requires at least one acceptance criterion",
                )
            )

        # self-reference
        if issue.id in issue.blocked_by:
            findings.append(LintFinding(path, issue.id, "self-block", "issue blocks on itself"))
        if issue.parent == issue.id:
            findings.append(LintFinding(path, issue.id, "self-parent", "issue is its own parent"))

        # dangling references
        for blocker_id in issue.blocked_by:
            if blocker_id not in all_ids:
                findings.append(
                    LintFinding(path, issue.id, "dangling-blocked-by", f"blocked_by references missing id {blocker_id}")
                )
        if issue.parent is not None and issue.parent not in all_ids:
            findings.append(
                LintFinding(path, issue.id, "dangling-parent", f"parent references missing id {issue.parent}")
            )

    # cycle detection (uses last-wins index; duplicates already reported above)
    index = store_mod.load_index(root)
    for edge_attr, rule in (("blocked_by", "blocked-by-cycle"), ("parent", "parent-cycle")):
        cycle = deps_mod.find_cycle(index, edge_attr)
        if cycle:
            path = index[cycle[0]].path if cycle[0] in index else None
            findings.append(
                LintFinding(path, cycle[0], rule, "cycle: " + " -> ".join(str(c) for c in cycle))
            )

    # counter integrity
    max_id = max(all_ids) if all_ids else 0
    next_id = config.get("next_id", 1)
    if next_id <= max_id:
        findings.append(
            LintFinding(
                None, None, "counter-behind",
                f"next_id ({next_id}) is not greater than max existing id ({max_id})",
            )
        )
        if fix:
            config["next_id"] = max_id + 1
            config_mod.save_config(root, config)

    if fix:
        for path, _feature, _archived, issue in valid:
            expected_name = model.issue_filename(issue.id, issue.slug)
            if path.name != expected_name:
                continue  # never rename files
            normalized = model.serialize_issue(issue)
            if path.read_text() != normalized:
                path.write_text(normalized)

    findings.sort(key=lambda f: (f.issue_id if f.issue_id is not None else -1, f.rule))
    return findings
