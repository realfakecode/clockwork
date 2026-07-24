"""The orchestrator now reaches the tracker in-process through `issues.service`,
not by shelling out to the CLI. These tests drive `orchestrator.issues` against a
real temp `.scratch/`, asserting the reads/writes land on actual files with no
subprocess, and that the retry/milestone label helpers work on the typed `Issue`."""

from __future__ import annotations

import pytest

from issues import service, store
from orchestrator import issues


@pytest.fixture
def repo(tmp_path):
    store.init_repo(tmp_path)
    return tmp_path


def test_reads_and_writes_go_through_real_files(repo):
    parent = service.new(
        repo, feature="alpha", title="parent", category="bug",
        criteria_texts=["c"], status="ready-for-agent",
    )
    child = service.new(repo, feature="alpha", title="child", parent=parent.id)

    got = issues.show(parent.id, cwd=repo)
    assert got.id == parent.id and got.title == "parent"

    kids = issues.children(parent.id, cwd=repo)
    assert [k.id for k in kids] == [child.id]

    issues.set_status(parent.id, "in-progress", cwd=repo)
    assert issues.show(parent.id, cwd=repo).status == "in-progress"

    issues.comment(parent.id, "a note from the loop", cwd=repo)
    assert "a note from the loop" in issues.show(parent.id, cwd=repo).body


def test_numbered_label_helpers_roundtrip(repo):
    m = service.new(repo, feature="alpha", title="map", status="wayfinding")
    issues.set_numbered_label(issues.show(m.id, cwd=repo),
                              issues.MILESTONE_ROUND_PREFIX, 2, cwd=repo)
    assert issues.read_numbered_label(
        issues.show(m.id, cwd=repo), issues.MILESTONE_ROUND_PREFIX) == 2

    issues.clear_numbered_label(issues.show(m.id, cwd=repo),
                                issues.MILESTONE_ROUND_PREFIX, cwd=repo)
    assert issues.read_numbered_label(
        issues.show(m.id, cwd=repo), issues.MILESTONE_ROUND_PREFIX) == 0


def test_no_subprocess_seam_remains():
    assert not hasattr(issues, "subprocess")
    assert not hasattr(issues, "_run")
    assert not hasattr(issues, "IssuesCliError")
