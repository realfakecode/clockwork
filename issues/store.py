"""Root discovery, issue indexing, and file I/O."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import config as config_mod
from . import model
from .config import SCRATCH_DIRNAME
from .model import Issue, Location

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
    """Feature directories under `.scratch/` — each holds that feature's active
    `issues/`. `archive/` is a reserved top-level name, not a feature."""
    scratch = config_mod.scratch_dir(root)
    if not scratch.is_dir():
        return []
    return sorted(
        p.name
        for p in scratch.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name != ARCHIVE_DIRNAME
    )


def _issues_dir(root: Path, feature: str) -> Path:
    return config_mod.scratch_dir(root) / feature / ISSUES_DIRNAME


def _archive_dir(root: Path) -> Path:
    return config_mod.scratch_dir(root) / ARCHIVE_DIRNAME


def iter_issue_files(root: Path):
    """Yield (path, dir_feature, archived) for every issue file on disk.

    `dir_feature` is the feature directory an active issue is filed under; an
    issue's authoritative feature is its frontmatter `feature` field
    (`Issue.feature`), not this. Archived issues live in one flat `archive/`
    directory with no per-feature subdir, so `dir_feature` is always `None`
    for them.
    """
    for feature in feature_dirs(root):
        d = _issues_dir(root, feature)
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.md")):
            yield path, feature, False
    archive_dir = _archive_dir(root)
    if archive_dir.is_dir():
        for path in sorted(archive_dir.glob("*.md")):
            yield path, None, True


def load_index(root: Path) -> dict[int, Issue]:
    """Scan all issue files into an id-keyed index. Skips unparsable files."""
    index: dict[int, Issue] = {}
    for path, _dir_feature, archived in iter_issue_files(root):
        try:
            issue = model.parse_issue(path)
        except model.ParseError:
            continue
        issue.location = Location(path=path, archived=archived)
        index[issue.id] = issue
    return index


def load_index_with_errors(root: Path) -> tuple[dict[int, Issue], list[tuple[Path, str]]]:
    """Like load_index but also returns (path, error message) for unparsable files."""
    index: dict[int, Issue] = {}
    errors: list[tuple[Path, str]] = []
    for path, _dir_feature, archived in iter_issue_files(root):
        try:
            issue = model.parse_issue(path)
        except model.ParseError as exc:
            errors.append((path, str(exc)))
            continue
        issue.location = Location(path=path, archived=archived)
        index[issue.id] = issue
    return index, errors


def get_issue(root: Path, issue_id: int) -> Issue:
    index = load_index(root)
    issue = index.get(issue_id)
    if issue is None:
        raise IssuesError(f"no issue with id {issue_id}")
    return issue


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
) -> Issue:
    issue_id = config_mod.allocate_id(root)
    slug = slug or model.slugify(title)
    now = datetime.now().replace(microsecond=0)
    issue = Issue(
        id=issue_id,
        slug=slug,
        feature=feature,
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
    issues_dir = _issues_dir(root, feature)
    issues_dir.mkdir(parents=True, exist_ok=True)
    path = issues_dir / model.issue_filename(issue_id, slug)
    path.write_text(_serialize_checked(issue))
    issue.location = Location(path=path, archived=False)
    return issue


def write_issue(issue: Issue, *, touch: bool = True) -> None:
    if touch:
        issue.updated = datetime.now().replace(microsecond=0)
    issue.location.path.write_text(_serialize_checked(issue))


def archive_issue(root: Path, issue_id: int) -> Issue:
    """Move an issue's file into the single top-level `archive/` directory.
    Ids are globally unique and monotonic, so filenames never collide there
    even though issues from every feature land in the same place."""
    issue = get_issue(root, issue_id)
    if issue.location.archived:
        return issue
    archive_dir = _archive_dir(root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    src_path = issue.location.path
    dest = archive_dir / src_path.name
    src_path.rename(dest)
    _prune_empty_feature_dir(src_path.parent)
    issue.location = Location(path=dest, archived=True)
    return issue


def _prune_empty_feature_dir(issues_dir: Path) -> None:
    """After the last active issue leaves a feature's `issues/`, drop the now-empty
    scaffolding: the `issues/` directory and its parent feature directory, each only
    if empty. A feature directory holding anything else is left untouched."""
    for path in (issues_dir, issues_dir.parent):
        try:
            path.rmdir()
        except OSError:
            break
