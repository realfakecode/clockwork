"""Unit tests for the shared `issues.service` layer, exercised directly against a
real `.scratch/` in a temp directory. These pin the behaviour both the CLI and the
orchestrator now inherit: transition/invariant enforcement, the release status
reset, resolve's answer+status, and that reads hand back typed `Issue` objects."""

from __future__ import annotations

import pytest

from issues import service, store
from issues.model import Issue
from issues.store import IssuesError


@pytest.fixture
def repo(tmp_path):
    store.init_repo(tmp_path)
    return tmp_path


def _agent_ready(repo, *, feature="alpha", title="t") -> Issue:
    """Create an issue directly in `ready-for-agent` (needs category + a criterion)."""
    return service.new(
        repo, feature=feature, title=title, category="bug",
        criteria_texts=["it works"], status="ready-for-agent",
    )


def test_new_returns_located_issue(repo):
    issue = service.new(repo, feature="alpha", title="First", criteria_texts=["c"])
    assert isinstance(issue, Issue)
    assert issue.status == "needs-triage"           # default = first todo status
    assert issue.location is not None
    assert issue.location.archived is False
    assert issue.location.path.exists()


def test_set_status_rejects_illegal_transition(repo):
    issue = service.new(repo, feature="a", title="t")
    with pytest.raises(IssuesError):
        service.set_status(issue.id, "done", cwd=repo)   # needs-triage -> done not allowed


def test_set_status_force_overrides(repo):
    issue = service.new(repo, feature="a", title="t")
    service.set_status(issue.id, "done", cwd=repo, force=True)
    assert service.show(issue.id, repo).status == "done"


def test_release_resets_open_status(repo):
    issue = _agent_ready(repo)
    service.claim(issue.id, "worker", cwd=repo)
    service.set_status(issue.id, "in-progress", cwd=repo)
    released = service.release(issue.id, cwd=repo)
    assert released.assignee is None
    assert released.status == "ready-for-agent"      # unclaim_status


def test_release_keep_status_leaves_status(repo):
    issue = _agent_ready(repo)
    service.claim(issue.id, "worker", cwd=repo)
    service.set_status(issue.id, "in-progress", cwd=repo)
    released = service.release(issue.id, cwd=repo, keep_status=True)
    assert released.assignee is None
    assert released.status == "in-progress"


def test_resolve_sets_done_and_appends_answer(repo):
    issue = _agent_ready(repo)
    service.set_status(issue.id, "in-progress", cwd=repo)
    resolved = service.resolve(issue.id, cwd=repo, answer="all shipped")
    assert resolved.status == "done"
    assert "all shipped" in service.show(issue.id, repo).body


def test_resolve_custom_status(repo):
    issue = service.new(repo, feature="a", title="t", category="bug")
    resolved = service.resolve(issue.id, cwd=repo, status="wontfix")
    assert resolved.status == "wontfix"


def test_reads_return_typed_issue_lists(repo):
    parent = _agent_ready(repo, title="parent")
    child = service.new(repo, feature="alpha", title="child", parent=parent.id)

    kids = service.children(parent.id, repo)
    assert [i.id for i in kids] == [child.id]
    assert all(isinstance(i, Issue) for i in kids)

    ready_ids = {i.id for i in service.ready_unclaimed(repo)}
    assert parent.id in ready_ids                    # ready-for-agent + unclaimed

    listed_ids = {i.id for i in service.list_status("needs-triage", repo)}
    assert child.id in listed_ids


def test_show_carries_body_and_location(repo):
    issue = service.new(repo, feature="a", title="t", body="hello world")
    got = service.show(issue.id, repo)
    assert "hello world" in got.body
    assert got.location.path == issue.location.path
