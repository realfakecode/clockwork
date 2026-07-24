"""Milestone-review state machine, with the `issues` CLI and the `pi` driver stubbed
by an in-memory fake. Covers the pure helpers, the completed-map eligibility gate, and
the three outcomes of a review round — filed follow-ups (re-fire), a clean pass
(mark + retrospect), and the round cap (block) — without a real tracker or agent.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import pytest

from issues.model import Issue
from orchestrator import issues as issuesmod
from orchestrator import loop as loopmod
from orchestrator import worker as workermod

# issues.* the loop mutates; the fake stands in for all of them. The pure reads
# (read_numbered_label, constants) stay real.
_STUBBED = (
    "list_status", "children", "show", "add_label", "comment",
    "set_numbered_label", "clear_numbered_label",
)


def mk_issue(mid: int, *, status="wayfinding", title=None, feature="feat",
             labels=None, body="## Destination\nx") -> Issue:
    """A minimal in-memory Issue for the loop to read (no file behind it)."""
    return Issue(
        id=mid, slug=f"i{mid}", feature=feature, status=status,
        created=datetime(2024, 1, 1),
        title=f"map {mid}" if title is None else title,
        labels=list(labels or []), body=body,
    )


class FakeTracker:
    """In-memory stand-in for the issue-tracker state the loop reads and writes."""

    def __init__(self):
        self.labels: dict[int, list[str]] = {}
        self.kids: dict[int, list[Issue]] = {}
        self.maps: list[int] = []
        self.feature: dict[int, str] = {}
        self.comments: list[tuple[int, str]] = []

    def add_map(self, mid: int, *, n_children: int, feature="feat",
                child_status="done", labels=None):
        self.maps.append(mid)
        self.feature[mid] = feature
        if labels:
            self.labels[mid] = list(labels)
        self.kids[mid] = [
            mk_issue(mid * 100 + i, status=child_status, title=f"k{i}", feature=feature)
            for i in range(n_children)
        ]
        return mid

    def map_issue(self, mid: int) -> Issue:
        return mk_issue(mid, feature=self.feature.get(mid, "feat"),
                        labels=list(self.labels.get(mid, [])))

    # -- stubbed issues.* --
    def list_status(self, status, cwd=None):
        return [self.map_issue(m) for m in self.maps] if status == "wayfinding" else []

    def children(self, mid, cwd=None):
        return list(self.kids.get(mid, []))

    def show(self, mid, cwd=None):
        return self.map_issue(mid)

    def add_label(self, mid, label, cwd=None):
        self.labels.setdefault(mid, []).append(label)

    def comment(self, mid, body, cwd=None):
        self.comments.append((mid, body))

    def set_numbered_label(self, issue, prefix, value, cwd=None):
        kept = [l for l in self.labels.get(issue.id, []) if not l.startswith(prefix)]
        self.labels[issue.id] = kept + [f"{prefix}{value}"]

    def clear_numbered_label(self, issue, prefix, cwd=None):
        self.labels[issue.id] = [
            l for l in self.labels.get(issue.id, []) if not l.startswith(prefix)
        ]

    # -- assertion helpers --
    def label_value(self, mid, prefix):
        for label in self.labels.get(mid, []):
            if label.startswith(prefix):
                return int(label[len(prefix):])
        return None

    def has_label(self, mid, label):
        return label in self.labels.get(mid, [])


@pytest.fixture
def tracker(monkeypatch):
    t = FakeTracker()
    for name in _STUBBED:
        monkeypatch.setattr(issuesmod, name, getattr(t, name))
    return t


def make_clockwork(tmp_path, **argoverrides):
    args = argparse.Namespace(
        model=None, design="docs/design.md", vocab="docs/vocabulary.md", feature=None,
        milestone_review=True, milestone_file_tickets=True,
        milestone_max_rounds=3, milestone_max_tickets=3,
    )
    for key, value in argoverrides.items():
        setattr(args, key, value)
    cw = loopmod.Clockwork(args)
    cw.log_path = tmp_path / "log.jsonl"
    cw.events = []
    cw.log = lambda event, **f: cw.events.append({"event": event, **f})
    cw._git_commit_all = lambda message: (True, "deadbee")
    return cw


def drive_milestone(monkeypatch, cw, map_id, tracker, effect):
    """Run `_milestone`, with `worker.drive` replaced by `effect(label, tracker)` —
    the hook that simulates what the review/retrospective agent does to the tracker."""
    async def fake_drive(command, cwd, prompt, label=None):
        effect(label, tracker)
        return "ok"

    monkeypatch.setattr(workermod, "drive", fake_drive)
    asyncio.run(cw._milestone(tracker.map_issue(map_id)))


def milestone_stages(cw):
    return [e.get("stage") for e in cw.events if e["event"] == "milestone"]


# -- pure helpers ---------------------------------------------------------

def test_read_numbered_label_absent_is_zero():
    assert issuesmod.read_numbered_label(mk_issue(1, labels=["other"]), "milestone-round:") == 0


def test_read_numbered_label_reads_value():
    assert issuesmod.read_numbered_label(mk_issue(1, labels=["milestone-round:4"]), "milestone-round:") == 4


@pytest.mark.parametrize("statuses, expected", [
    ([], False),
    (["done", "wontfix"], True),
    (["done", "in-progress"], False),
    (["needs-triage"], False),
])
def test_all_children_terminal(statuses, expected):
    children = [mk_issue(i, status=s) for i, s in enumerate(statuses)]
    assert loopmod.Clockwork._all_children_terminal(children) is expected


def test_log_ticket_parses():
    assert loopmod._log_ticket('{"ticket": 7, "event": "retry"}') == 7


def test_log_ticket_malformed_is_none():
    assert loopmod._log_ticket("not json") is None


# -- eligibility (_pick_completed_map) ------------------------------------

def test_cleared_frontier_never_reviewed_is_eligible(tracker, tmp_path):
    tracker.add_map(1, n_children=3)
    assert make_clockwork(tmp_path)._pick_completed_map().id == 1


def test_reviewed_at_current_size_not_eligible(tracker, tmp_path):
    tracker.add_map(1, n_children=3, labels=["milestone-reviewed:3"])
    assert make_clockwork(tmp_path)._pick_completed_map() is None


def test_grew_since_review_is_eligible(tracker, tmp_path):
    tracker.add_map(1, n_children=5, labels=["milestone-reviewed:3"])
    assert make_clockwork(tmp_path)._pick_completed_map().id == 1


def test_blocked_label_not_eligible(tracker, tmp_path):
    tracker.add_map(1, n_children=3, labels=["milestone-blocked"])
    assert make_clockwork(tmp_path)._pick_completed_map() is None


def test_in_flight_child_not_eligible(tracker, tmp_path):
    tracker.add_map(1, n_children=3)
    tracker.kids[1][0].status = "in-progress"
    assert make_clockwork(tmp_path)._pick_completed_map() is None


def test_no_children_not_eligible(tracker, tmp_path):
    tracker.add_map(1, n_children=0)
    assert make_clockwork(tmp_path)._pick_completed_map() is None


def test_feature_filter_excludes_other_feature(tracker, tmp_path):
    tracker.add_map(1, n_children=3, feature="other")
    assert make_clockwork(tmp_path, feature="feat")._pick_completed_map() is None


def test_disabled_never_eligible(tracker, tmp_path):
    tracker.add_map(1, n_children=3)
    assert make_clockwork(tmp_path, milestone_review=False)._pick_completed_map() is None


# -- _milestone: the three round outcomes ---------------------------------

def test_review_files_tickets_refires(tracker, tmp_path, monkeypatch):
    tracker.add_map(1, n_children=3)

    def files_two(label, tr):
        if label.startswith("milestone-review"):
            tr.kids[1] += [mk_issue(200, status="needs-triage", title="fix a"),
                           mk_issue(201, status="needs-triage", title="fix b")]

    cw = make_clockwork(tmp_path)
    drive_milestone(monkeypatch, cw, 1, tracker, files_two)

    stages = milestone_stages(cw)
    assert "filed" in stages
    assert "clean" not in stages                       # not the fixpoint
    assert tracker.label_value(1, "milestone-round:") == 1
    # Reviewed marker stays unset so the review re-fires once the fixes land.
    assert tracker.label_value(1, "milestone-reviewed:") is None


def test_clean_pass_marks_and_retrospects(tracker, tmp_path, monkeypatch):
    tracker.add_map(1, n_children=3)
    retro_ran = []

    def clean(label, tr):
        if label.startswith("retrospective"):
            retro_ran.append(label)
        # milestone-review files nothing

    cw = make_clockwork(tmp_path)
    drive_milestone(monkeypatch, cw, 1, tracker, clean)

    assert "clean" in milestone_stages(cw)
    assert len(retro_ran) == 1                          # retrospective gated on clean
    assert tracker.label_value(1, "milestone-reviewed:") == 3
    assert tracker.label_value(1, "milestone-round:") is None   # reset on clean


def test_round_cap_blocks_without_reviewing(tracker, tmp_path, monkeypatch):
    tracker.add_map(1, n_children=1, labels=["milestone-round:3"])
    review_ran = []

    def track_review(label, tr):
        if label.startswith("milestone-review"):
            review_ran.append(label)

    cw = make_clockwork(tmp_path, milestone_max_rounds=3)
    drive_milestone(monkeypatch, cw, 1, tracker, track_review)

    assert "blocked" in milestone_stages(cw)
    assert tracker.has_label(1, "milestone-blocked")
    assert review_ran == []                             # capped before running the agent
    assert len(tracker.comments) == 1
