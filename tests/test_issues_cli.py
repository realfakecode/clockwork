"""End-to-end tests for the `issues` CLI, driven through `cli.main` against a real
`.scratch/` tracker in a temp directory. Each test asserts on captured stdout and/or
the files left on disk, exercising the command surface a user actually types."""

from __future__ import annotations

import io
import json

import pytest

from issues import cli, config


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A fresh initialized tracker, with cwd inside it so root discovery finds it."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    return tmp_path


def run(capsys, argv, *, stdin=None, monkeypatch=None):
    """Invoke `cli.main(argv)`; return (exit_code, stdout). `stdin` feeds `-` reads."""
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    capsys.readouterr()  # drop anything buffered from earlier calls
    code = cli.main(argv)
    return code, capsys.readouterr().out


def new_issue(capsys, feature, title, **flags):
    """Create an issue and return its id, parsed from `--json` output."""
    argv = ["new", feature, title, "--json"]
    for key, value in flags.items():
        argv += [f"--{key}", value]
    code, out = run(capsys, argv)
    assert code == 0
    return json.loads(out)["id"]


# -- status: query vs set -------------------------------------------------

def test_status_with_no_target_prints_current(repo, capsys):
    iid = new_issue(capsys, "auth", "Login")
    code, out = run(capsys, ["status", str(iid)])
    assert code == 0
    assert out.strip() == "needs-triage"


def test_status_with_target_sets_it(repo, capsys):
    iid = new_issue(capsys, "auth", "Login")
    assert run(capsys, ["status", str(iid), "needs-info"])[0] == 0
    assert run(capsys, ["status", str(iid)])[1].strip() == "needs-info"


def test_status_query_does_not_mutate(repo, capsys):
    iid = new_issue(capsys, "auth", "Login")
    before = config.load_config(repo)  # querying must not touch config/files
    run(capsys, ["status", str(iid)])
    assert config.load_config(repo) == before


# -- show: one or many ----------------------------------------------------

def test_show_multiple_ids_prints_each(repo, capsys):
    a = new_issue(capsys, "auth", "Login")
    b = new_issue(capsys, "auth", "Logout")
    code, out = run(capsys, ["show", str(a), str(b)])
    assert code == 0
    assert "Login" in out and "Logout" in out


def test_show_multiple_json_is_array(repo, capsys):
    a = new_issue(capsys, "auth", "Login")
    b = new_issue(capsys, "billing", "Invoice")
    code, out = run(capsys, ["show", str(a), str(b), "--json"])
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert [p["id"] for p in payload] == [a, b]


def test_show_single_json_is_object(repo, capsys):
    a = new_issue(capsys, "auth", "Login")
    payload = json.loads(run(capsys, ["show", str(a), "--json"])[1])
    assert isinstance(payload, dict) and payload["id"] == a


def test_show_unknown_id_errors(repo, capsys):
    a = new_issue(capsys, "auth", "Login")
    assert run(capsys, ["show", str(a), "999"])[0] == 1


# -- comment: positional / flag / stdin -----------------------------------

def _comments(repo, capsys, iid):
    return json.loads(run(capsys, ["show", str(iid), "--json"])[1])["body"]


def test_comment_positional_body(repo, capsys):
    iid = new_issue(capsys, "auth", "Login")
    assert run(capsys, ["comment", str(iid), "This is confusing"])[0] == 0
    assert "This is confusing" in _comments(repo, capsys, iid)


def test_comment_flag_still_works(repo, capsys):
    iid = new_issue(capsys, "auth", "Login")
    assert run(capsys, ["comment", str(iid), "--body", "via flag"])[0] == 0
    assert "via flag" in _comments(repo, capsys, iid)


def test_comment_stdin_dash(repo, capsys, monkeypatch):
    iid = new_issue(capsys, "auth", "Login")
    code, _ = run(capsys, ["comment", str(iid), "-"], stdin="from stdin\n", monkeypatch=monkeypatch)
    assert code == 0
    assert "from stdin" in _comments(repo, capsys, iid)


def test_comment_positional_and_flag_conflict(repo, capsys):
    iid = new_issue(capsys, "auth", "Login")
    assert run(capsys, ["comment", str(iid), "a", "--body", "b"])[0] == 1


def test_comment_empty_errors(repo, capsys):
    iid = new_issue(capsys, "auth", "Login")
    assert run(capsys, ["comment", str(iid)])[0] == 1


# -- resolve: positional answer -------------------------------------------

def test_resolve_positional_answer(repo, capsys):
    iid = new_issue(capsys, "auth", "Question")
    # --force to skip the transition gate; this test is about answer parsing.
    assert run(capsys, ["resolve", str(iid), "the answer", "--force"])[0] == 0
    assert run(capsys, ["status", str(iid)])[1].strip() == "done"
    assert "the answer" in _comments(repo, capsys, iid)


# -- archive: prune emptied feature scaffolding ---------------------------

def test_archive_removes_emptied_feature_dir(repo, capsys):
    iid = new_issue(capsys, "auth", "Login")
    feature_dir = config.scratch_dir(repo) / "auth"
    assert feature_dir.is_dir()
    assert run(capsys, ["archive", str(iid)])[0] == 0
    assert not feature_dir.exists()
    assert (config.scratch_dir(repo) / "archive").is_dir()


def test_archive_keeps_feature_dir_with_other_issues(repo, capsys):
    a = new_issue(capsys, "auth", "Login")
    new_issue(capsys, "auth", "Logout")
    feature_dir = config.scratch_dir(repo) / "auth"
    assert run(capsys, ["archive", str(a)])[0] == 0
    assert feature_dir.is_dir()


# -- default config -------------------------------------------------------

def test_open_status_is_gone(repo):
    statuses = config.all_statuses(config.default_config())
    assert "open" not in statuses
