"""Root discovery, issue indexing, and file I/O."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from . import model
from .config import SCRATCH_DIRNAME
from .model import Issue

ISSUES_DIRNAME = "issues"
ARCHIVE_DIRNAME = "archive"


class IssuesError(Exception):
    """User-facing error: bad root, missing issue, bad input, etc."""


def _serialize_checked(issue: Issue) -> str:
    """Serialize `issue` and verify it round-trips before returning the text.

    Guards every write against serialization bugs (e.g. text that breaks YAML
    quoting): parse the output back and re-serialize it; if the file would not
    parse, or would parse to something that serializes differently, raise
    rather than write a file that silently loses or corrupts data on read.
    """
    text = model.serialize_issue(issue)
    try:
        reparsed = model.parse_issue_text(text)
    except model.ParseError as exc:
        raise IssuesError(
            f"internal error: issue {issue.id} did not serialize to valid form: {exc}"
        ) from exc
    if model.serialize_issue(reparsed) != text:
        raise IssuesError(
            f"internal error: issue {issue.id} does not round-trip through serialization"
        )
    return text


@dataclass
class IssueRecord:
    issue: Issue
    path: Path
    feature: str
    archived: bool

    @property
    def id(self) -> int:
        return self.issue.id


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default cwd) looking for a `.scratch` directory."""
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / SCRATCH_DIRNAME).is_dir():
            return candidate
    raise IssuesError(
        "no .scratch/ found in this directory or any parent — run `issues init` first"
    )


def init_repo(start: Path | None = None) -> Path:
    root = (start or Path.cwd()).resolve()
    scratch = config_mod.scratch_dir(root)
    scratch.mkdir(parents=True, exist_ok=True)
    cfg_path = config_mod.config_path(root)
    if not cfg_path.exists():
        config_mod.save_config(root, config_mod.default_config())
    return root


def feature_dirs(root: Path) -> list[str]:
    scratch = config_mod.scratch_dir(root)
    if not scratch.is_dir():
        return []
    return sorted(
        p.name for p in scratch.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def _issue_paths(root: Path, feature: str, archived: bool) -> Path:
    sub = ARCHIVE_DIRNAME if archived else ISSUES_DIRNAME
    return config_mod.scratch_dir(root) / feature / sub


def iter_issue_files(root: Path):
    """Yield (path, feature, archived) for every issue file on disk."""
    for feature in feature_dirs(root):
        for archived, dirname in ((False, ISSUES_DIRNAME), (True, ARCHIVE_DIRNAME)):
            d = config_mod.scratch_dir(root) / feature / dirname
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.md")):
                yield path, feature, archived


def load_index(root: Path) -> dict[int, IssueRecord]:
    """Scan all issue files into an id-keyed index. Skips unparsable files."""
    index: dict[int, IssueRecord] = {}
    for path, feature, archived in iter_issue_files(root):
        try:
            issue = model.parse_issue(path)
        except model.ParseError:
            continue
        index[issue.id] = IssueRecord(issue=issue, path=path, feature=feature, archived=archived)
    return index


def load_index_with_errors(root: Path) -> tuple[dict[int, IssueRecord], list[tuple[Path, str]]]:
    """Like load_index but also returns (path, error message) for unparsable files."""
    index: dict[int, IssueRecord] = {}
    errors: list[tuple[Path, str]] = []
    for path, feature, archived in iter_issue_files(root):
        try:
            issue = model.parse_issue(path)
        except model.ParseError as exc:
            errors.append((path, str(exc)))
            continue
        index[issue.id] = IssueRecord(issue=issue, path=path, feature=feature, archived=archived)
    return index, errors


def get_issue(root: Path, issue_id: int) -> IssueRecord:
    index = load_index(root)
    record = index.get(issue_id)
    if record is None:
        raise IssuesError(f"no issue with id {issue_id}")
    return record


def create_issue(
    root: Path,
    feature: str,
    title: str,
    *,
    slug: str | None = None,
    status: str,
    category: str | None = None,
    labels: list[str] | None = None,
    parent: int | None = None,
    blocked_by: list[int] | None = None,
    assignee: str | None = None,
    acceptance_criteria: list[dict] | None = None,
    body: str | None = None,
) -> IssueRecord:
    issue_id = config_mod.allocate_id(root)
    slug = slug or model.slugify(title)
    now = datetime.now().replace(microsecond=0)
    issue = Issue(
        id=issue_id,
        slug=slug,
        title=title,
        status=status,
        category=category,
        labels=list(labels or []),
        parent=parent,
        assignee=assignee,
        blocked_by=list(blocked_by or []),
        acceptance_criteria=list(acceptance_criteria or []),
        created=now,
        updated=now,
        body=model.new_body(title, body),
    )
    issues_dir = _issue_paths(root, feature, archived=False)
    issues_dir.mkdir(parents=True, exist_ok=True)
    path = issues_dir / model.issue_filename(issue_id, slug)
    path.write_text(_serialize_checked(issue))
    return IssueRecord(issue=issue, path=path, feature=feature, archived=False)


def write_issue(record: IssueRecord, *, touch: bool = True) -> None:
    if touch:
        record.issue.updated = datetime.now().replace(microsecond=0)
    record.path.write_text(_serialize_checked(record.issue))


def archive_issue(root: Path, issue_id: int) -> IssueRecord:
    record = get_issue(root, issue_id)
    if record.archived:
        return record
    archive_dir = _issue_paths(root, record.feature, archived=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / record.path.name
    record.path.rename(dest)
    new_record = replace(record, path=dest, archived=True)
    return new_record
