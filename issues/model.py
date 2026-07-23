"""Issue dataclass and frontmatter (de)serialization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

FRONTMATTER_DELIM = "---"

# Order in which frontmatter keys are written. Also used by `lint --fix`
# to normalize key order.
FIELD_ORDER = [
    "id",
    "slug",
    "feature",
    "title",
    "status",
    "category",
    "labels",
    "parent",
    "assignee",
    "blocked_by",
    "acceptance_criteria",
    "created",
    "updated",
]


class _NoAliasDumper(yaml.SafeDumper):
    """SafeDumper that never emits anchors/aliases, so repeated values (e.g. a
    shared created/updated timestamp) are written out verbatim."""

    def ignore_aliases(self, data) -> bool:
        return True


class ParseError(Exception):
    pass


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "issue"


def issue_filename(issue_id: int, slug: str) -> str:
    return f"{issue_id}-{slug}.md"


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "isoformat") and not isinstance(value, str):
        # yaml may parse a bare date as datetime.date
        return datetime.fromisoformat(value.isoformat())
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ParseError(f"cannot parse timestamp: {value!r}")


@dataclass
class Issue:
    id: int
    slug: str
    feature: str
    status: str
    created: datetime
    title: str = ""
    category: str | None = None
    labels: list[str] = field(default_factory=list)
    parent: int | None = None
    assignee: str | None = None
    blocked_by: list[int] = field(default_factory=list)
    # Acceptance-criteria checklist; each item is {"text": str, "done": bool}.
    acceptance_criteria: list[dict] = field(default_factory=list)
    updated: datetime | None = None
    body: str = ""

    def __post_init__(self):
        if self.updated is None:
            self.updated = self.created

    def to_frontmatter_dict(self) -> dict:
        data = {
            "id": self.id,
            "slug": self.slug,
            "feature": self.feature,
            "title": self.title,
            "status": self.status,
            "category": self.category,
            "labels": self.labels,
            "parent": self.parent,
            "assignee": self.assignee,
            "blocked_by": self.blocked_by,
            "acceptance_criteria": self.acceptance_criteria,
            "created": self.created,
            "updated": self.updated,
        }
        return data


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split raw file text into (frontmatter_yaml, body). Raises ParseError."""
    if not text.startswith(FRONTMATTER_DELIM):
        raise ParseError("missing opening frontmatter delimiter '---'")
    rest = text[len(FRONTMATTER_DELIM):]
    if rest.startswith("\n"):
        rest = rest[1:]
    parts = rest.split(f"\n{FRONTMATTER_DELIM}", 1)
    if len(parts) != 2:
        raise ParseError("missing closing frontmatter delimiter '---'")
    fm_text, body = parts
    body = body[1:] if body.startswith("\n") else body
    return fm_text, body


def parse_frontmatter(fm_text: str) -> dict:
    try:
        data = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise ParseError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ParseError("frontmatter must be a mapping")
    return data


def coerce_criteria(value) -> list[dict]:
    """Normalize raw frontmatter into [{"text": str, "done": bool}]. Raises
    ParseError on shapes that can't be interpreted."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ParseError("acceptance_criteria must be a list")
    items: list[dict] = []
    for raw in value:
        if not isinstance(raw, dict) or "text" not in raw:
            raise ParseError("acceptance_criteria items must be mappings with a 'text' key")
        items.append({"text": str(raw["text"]), "done": bool(raw.get("done", False))})
    return items


def issue_from_dict(data: dict, body: str) -> Issue:
    missing = [
        k for k in ("id", "slug", "feature", "status", "created") if k not in data or data[k] is None
    ]
    if missing:
        raise ParseError(f"missing required field(s): {', '.join(missing)}")
    try:
        issue_id = int(data["id"])
    except (TypeError, ValueError) as exc:
        raise ParseError(f"id is not an integer: {data['id']!r}") from exc

    created = _to_datetime(data["created"])
    updated = _to_datetime(data["updated"]) if data.get("updated") is not None else created

    return Issue(
        id=issue_id,
        slug=str(data["slug"]),
        feature=str(data["feature"]),
        title=str(data.get("title") or ""),
        status=str(data["status"]),
        category=data.get("category"),
        labels=list(data.get("labels") or []),
        parent=int(data["parent"]) if data.get("parent") is not None else None,
        assignee=data.get("assignee"),
        blocked_by=[int(b) for b in (data.get("blocked_by") or [])],
        acceptance_criteria=coerce_criteria(data.get("acceptance_criteria")),
        created=created,
        updated=updated,
        body=body,
    )


def parse_issue_text(text: str) -> Issue:
    fm_text, body = split_frontmatter(text)
    data = parse_frontmatter(fm_text)
    return issue_from_dict(data, body)


def parse_issue(path: Path) -> Issue:
    return parse_issue_text(path.read_text())


def serialize_frontmatter(issue: Issue, *, include_criteria: bool = True) -> str:
    """Emit frontmatter via PyYAML so arbitrary text (colons, quotes, leading
    dashes, newlines) is quoted/escaped correctly rather than hand-formatted.

    `include_criteria=False` drops `acceptance_criteria` from the dump — used by
    `issues show`, which renders criteria as a checklist and doesn't want them
    duplicated in the raw YAML. The on-disk write path always keeps them."""
    data = issue.to_frontmatter_dict()
    ordered: dict = {}
    for key in FIELD_ORDER:
        value = data[key]
        if key == "acceptance_criteria" and not include_criteria:
            continue
        if key in ("category", "parent", "assignee") and value is None:
            continue
        if key in ("labels", "blocked_by", "acceptance_criteria") and not value:
            continue
        ordered[key] = value
    dumped = yaml.dump(
        ordered,
        Dumper=_NoAliasDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=float("inf"),
    ).rstrip("\n")
    return f"{FRONTMATTER_DELIM}\n{dumped}\n{FRONTMATTER_DELIM}"


def serialize_issue(issue: Issue, *, include_criteria: bool = True) -> str:
    fm = serialize_frontmatter(issue, include_criteria=include_criteria)
    body = issue.body if issue.body.startswith("\n") else "\n" + issue.body
    text = fm + body
    if not text.endswith("\n"):
        text += "\n"
    return text


def new_body(title: str, body_text: str | None) -> str:
    parts = [f"# {title}", ""]
    if body_text:
        parts.append(body_text.rstrip())
        parts.append("")
    parts.append("## Comments")
    parts.append("")
    return "\n".join(parts) + "\n"


def append_comment(body: str, text: str, when: datetime) -> str:
    stamp = when.isoformat()
    entry = f"- {stamp} — {text}"
    body = body.rstrip("\n")
    if "## Comments" not in body:
        body = body + "\n\n## Comments"
    return body + "\n" + entry + "\n"


def add_criterion(criteria: list[dict], text: str) -> None:
    criteria.append({"text": text.strip(), "done": False})


def set_criterion_done(criteria: list[dict], index: int, done: bool) -> None:
    if index < 0 or index >= len(criteria):
        raise IndexError(f"no criterion at index {index}")
    criteria[index]["done"] = done


def remove_criterion(criteria: list[dict], index: int) -> None:
    if index < 0 or index >= len(criteria):
        raise IndexError(f"no criterion at index {index}")
    del criteria[index]


def render_criteria(criteria: list[dict], indices: list[int] | None = None) -> str:
    """Human-readable checklist, one `N. [ ] text` line per criterion.

    `indices`, if given, supplies the displayed number for each item — e.g. to
    print a subset of a larger list under its real position — instead of the
    default 0-based enumeration.
    """
    lines = []
    for i, item in zip(indices if indices is not None else range(len(criteria)), criteria):
        box = "x" if item.get("done") else " "
        lines.append(f"{i}. [{box}] {item['text']}")
    return "\n".join(lines)


def body_has_h1(body: str) -> bool:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith("# ")
    return False
