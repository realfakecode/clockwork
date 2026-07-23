"""The dispatch loop: dumb by design. It shells out to `issues`, runs one `pi`
worker per ready ticket, and decides everything from the status observed after
each run. All intelligence lives in issue-tracker state + the worker prompt.

Serial dispatch only. The loop keeps going until no workable ticket remains
(escalation queue full, nothing ready, or the safety cap) — see `run`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import issues, worker

WORKER_ASSIGNEE = "clockwork"
_OUTPUT_TAIL = 3000  # chars of validation output kept for the log/comment/validator


def _pi_command(model: str | None) -> list[str]:
    cmd = ["pi", "--mode", "rpc", "--no-session"]
    if model:
        cmd += ["--model", model]
    return cmd


def _scratch_dir(cwd: Path) -> Path:
    """Walk up from cwd for `.scratch/`, matching the issue tracker's root discovery.
    Falls back to cwd/.scratch so the first log line still lands somewhere sane."""
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".scratch").is_dir():
            return candidate / ".scratch"
    return cwd / ".scratch"


class Clockwork:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.cwd = Path.cwd()
        self.command = _pi_command(args.model)
        self.log_path = _scratch_dir(self.cwd) / ".clockwork-log.jsonl"

    # -- the seam log ------------------------------------------------------

    def log(self, event: str, **fields) -> None:
        """Append one JSON line — the trial's primary instrument. Breakdowns
        show up here before they show up in code."""
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        line = json.dumps(record)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            print(f"[clockwork] could not write log: {exc}", flush=True)
        print(f"[clockwork] {line}", flush=True)

    # -- escalation --------------------------------------------------------

    def _escalate(self, ticket_id: int, body: str) -> None:
        """Route a ticket to the design queue and drop any clockwork claim so the
        design session can reassign it cleanly."""
        issues.set_status(ticket_id, "needs-decision", cwd=self.cwd)
        issues.release(ticket_id, cwd=self.cwd, keep_status=True)
        issues.comment(ticket_id, body, cwd=self.cwd)

    def _pick_ticket(self) -> dict | None:
        for record in issues.ready_unclaimed(self.cwd, feature=self.args.feature):
            if record.get("status") == "ready-for-agent":
                return record
        return None

    def _pick_triage_ticket(self) -> dict | None:
        """A bare ticket on the unclaimed frontier awaiting specification. Only
        consulted when nothing is ready-for-agent, so dispatch never starves."""
        for record in issues.ready_unclaimed(self.cwd, feature=self.args.feature):
            if record.get("status") == "needs-triage":
                return record
        return None

    # -- validation (after the worker stops) -------------------------------
    #
    # The worker does NOT judge its own done-ness. Once it stops, the loop is the
    # sole authority: a hard test-command gate, then an independent validator
    # agent for the criteria tests don't cover. Only if both pass does the loop
    # check off the criteria and resolve the ticket.

    def _run_validation_command(self) -> tuple[bool, str]:
        cmd = self.args.validate
        if not cmd:
            return True, "(no --validate command configured; test gate skipped)"
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=self.cwd, capture_output=True, text=True,
                timeout=self.args.validate_timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"$ {cmd}\n(timed out after {self.args.validate_timeout}s)"
        output = ((proc.stdout or "") + (proc.stderr or ""))[-_OUTPUT_TAIL:]
        return proc.returncode == 0, f"$ {cmd}\nexit={proc.returncode}\n{output}".strip()

    # -- worktree hygiene --------------------------------------------------

    def _reset_worktree(self, ticket_id: int) -> None:
        """Revert the working tree to HEAD, EXCLUDING `.scratch/`, on every
        non-accept exit. A failed or escalated worker's half-finished code must not
        leak into the next dispatch's or the read-only validator's `git diff`/`git
        status` view — the same clean-tree invariant `_commit_ticket` upholds on the
        accept path. `.scratch/` is git-tracked (it *is* the issue-tracker DB), so it is
        excluded so issue-tracker state survives: the failure comment/attempts label the
        loop is about to write, and a worker's own QUESTION escalation comment."""
        for argv in (
            ["git", "checkout", "HEAD", "--", ".", ":(exclude).scratch"],
            ["git", "clean", "-fd", "-e", ".scratch"],
        ):
            proc = subprocess.run(argv, cwd=self.cwd, capture_output=True, text=True)
            if proc.returncode != 0:
                err = ((proc.stderr or "") + (proc.stdout or "")).strip()[-_OUTPUT_TAIL:]
                self.log("reset", ticket=ticket_id, ok=False, error=err)
                return
        self.log("reset", ticket=ticket_id, ok=True)

    def _has_code_changes(self) -> bool:
        """True if the worker touched anything outside `.scratch/`. A worker that
        stops having changed no code has not implemented the ticket — the test gate
        alone can't catch that (pre-existing tests stay green)."""
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":(exclude).scratch"],
            cwd=self.cwd, capture_output=True, text=True,
        )
        return bool(proc.stdout.strip())

    def _fail_attempt(self, ticket_id: int, attempts: int, note: str) -> None:
        # Revert first so the loop's own bookkeeping (comment/attempts/release,
        # written into .scratch below) survives the reset and the retry worker
        # starts from a clean tree.
        self._reset_worktree(ticket_id)
        issues.comment(ticket_id, note, cwd=self.cwd)
        new_attempts = issues.bump_attempts(ticket_id, attempts, cwd=self.cwd)
        self.log("retry", ticket=ticket_id, attempts=new_attempts)
        if new_attempts >= self.args.max_attempts:
            self._escalate(
                ticket_id,
                f"auto-escalated after {new_attempts} attempts — not ambiguous, just hard",
            )
            self.log("escalate", ticket=ticket_id, reason="attempt-cap", attempts=new_attempts)
        else:
            # Release clears the clockwork claim and resets to ready-for-agent so
            # the ticket re-enters the unclaimed frontier next iteration. The
            # failure note is now in the ticket body, so the retry worker sees it.
            issues.release(ticket_id, cwd=self.cwd)

    def _git_commit_all(self, message: str) -> tuple[bool, str]:
        """Stage everything and commit. Returns (ok, detail): detail is the short
        HEAD sha on success, 'nothing to commit', or an error tail. Shared by the
        per-ticket and triage commits so both keep the tree clean the same way."""
        try:
            subprocess.run(["git", "add", "-A"], cwd=self.cwd,
                           capture_output=True, text=True, check=True)
            proc = subprocess.run(["git", "commit", "-m", message], cwd=self.cwd,
                                  capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            return False, str(exc)
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode == 0:
            head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.cwd,
                                  capture_output=True, text=True)
            return True, head.stdout.strip()
        if "nothing to commit" in output:
            return True, "nothing to commit"
        return False, output[-_OUTPUT_TAIL:]

    def _commit_ticket(self, ticket_id: int, issue: dict) -> None:
        """One commit per resolved ticket so the tree is clean before the next
        dispatch — an uncommitted diff otherwise leaks into the next worker's and
        the read-only validator's view (`git diff`/`git status`). Simple by design:
        stage everything (including this ticket's `.scratch` state) and commit."""
        title = (issue.get("title") or "").strip()
        ok, detail = self._git_commit_all(f"ticket #{ticket_id}: {title}".rstrip())
        if not ok:
            self.log("commit", ticket=ticket_id, ok=False, error=detail)
        elif detail == "nothing to commit":
            self.log("commit", ticket=ticket_id, ok=True, note="nothing to commit")
        else:
            self.log("commit", ticket=ticket_id, ok=True, sha=detail or None)

    def _commit_triage(self, ticket_id: int) -> None:
        """Commit triage's own output before the next dispatch. Triage now writes the
        repo tree (naming-registry edits), so an uncommitted diff would otherwise leak
        into the next worker's and the read-only validator's `git diff`/`git status`
        view AND defeat the empty-diff gate (which would read a stray registry edit as
        the worker's changes). Staging `.scratch` too keeps each phase's work atomic."""
        ok, detail = self._git_commit_all(f"triage #{ticket_id}")
        if not ok:
            self.log("commit", ticket=ticket_id, stage="triage", ok=False, error=detail)
        elif detail != "nothing to commit":
            self.log("commit", ticket=ticket_id, stage="triage", ok=True, sha=detail or None)

    def _accept(self, ticket_id: int, issue: dict) -> None:
        for i in range(len(issue.get("acceptance_criteria") or [])):
            issues.check_criterion(ticket_id, i, cwd=self.cwd)
        issues.resolve(
            ticket_id, cwd=self.cwd,
            answer="validated: test gate + independent validator both passed",
        )
        self._commit_ticket(ticket_id, issue)
        self.log("done", ticket=ticket_id)

    async def _validate_and_finish(self, ticket_id: int, attempts: int) -> None:
        issue = issues.show(ticket_id, cwd=self.cwd)
        if issue.get("status") == "needs-decision":
            # Worker self-escalated (QUESTION comment already in .scratch). Discard
            # its half-done code but keep the escalation payload for the design phase.
            self._reset_worktree(ticket_id)
            self.log("escalate", ticket=ticket_id, reason="agent")
            return

        # 0. Empty-diff gate: a worker that changed no code hasn't implemented the
        #    ticket. Catch it before the test gate, which pre-existing tests pass.
        if not self._has_code_changes():
            self._fail_attempt(ticket_id, attempts,
                               "validation failed (no changes): worker stopped without modifying any code")
            return

        # 1. Hard test-command gate.
        tests_ok, summary = self._run_validation_command()
        self.log("validate", ticket=ticket_id, stage="tests", ok=tests_ok)
        if not tests_ok:
            self._fail_attempt(ticket_id, attempts, f"validation failed (tests):\n{summary}")
            return

        # 2. Independent validator agent for what tests can't cover. A missing
        #    verdict marker is a malformed judge, not a code failure — re-run the
        #    validator once before falling back to a worker retry, so a flaky
        #    verdict format doesn't burn a full re-implementation of correct code.
        prompt = worker.build_validator_prompt(issue, self.args.design, self.args.vocab, summary)
        reply = await worker.drive(self.command, str(self.cwd), prompt)
        verdict, reason = worker.parse_verdict(reply)
        if verdict == "none":
            self.log("validate", ticket=ticket_id, stage="agent", ok=False,
                     reason="no verdict marker — re-running validator")
            reply = await worker.drive(self.command, str(self.cwd), prompt)
            verdict, reason = worker.parse_verdict(reply)
        passed = verdict == "pass"
        self.log("validate", ticket=ticket_id, stage="agent", ok=passed,
                 verdict=verdict, reason=reason or None)

        # 2a. Validator caught the worker silently defaulting a genuine design decision.
        #     Route to the human queue (not a worker retry, which would re-guess) with the
        #     decision as the QUESTION. Discard the guessed code like any escalation; the
        #     worker's `assumption:` comments survive in .scratch for the design session.
        if verdict == "escalate":
            self._reset_worktree(ticket_id)
            self._escalate(
                ticket_id,
                "escalated by the validator — a genuine design decision was resolved by "
                f"defaulting instead of asking.\nQUESTION: {reason}\nPROPOSED DEFAULT: "
                "confirm the worker's choice or change it (see its `assumption:` comments).",
            )
            self.log("escalate", ticket=ticket_id, reason="validator")
            return

        if not passed:
            self._fail_attempt(ticket_id, attempts, f"validation failed (validator): {reason}")
            return

        # 3. Both passed → the loop closes the ticket.
        self._accept(ticket_id, issue)

    # -- triage (bare ticket -> ready-for-agent) ---------------------------
    #
    # A triage agent specifies a `needs-triage` ticket (description + criteria +
    # category) and promotes it. The issue tracker's require_category/require_criteria
    # invariants reject the promotion until that's actually done, so the loop just
    # observes the resulting status — no separate validation step.

    async def _triage(self, ticket_id: int) -> None:
        self.log("triage", ticket=ticket_id, stage="start")
        issue = issues.show(ticket_id, cwd=self.cwd)
        prompt = worker.build_triage_prompt(issue, self.args.design, self.args.vocab)
        await worker.drive(self.command, str(self.cwd), prompt)

        status = issues.show(ticket_id, cwd=self.cwd).get("status")
        if status == "ready-for-agent":
            self.log("triage", ticket=ticket_id, stage="done", status=status)
        elif status in ("needs-info", "ready-for-human", "wontfix"):
            # The agent deliberately routed it elsewhere (e.g. needs a human call).
            self.log("triage", ticket=ticket_id, stage="routed", status=status)
        else:
            # Still needs-triage: the agent didn't finish. Route to needs-info so
            # the same bare ticket isn't picked again next iteration, and surface it.
            issues.set_status(ticket_id, "needs-info", cwd=self.cwd)
            issues.comment(
                ticket_id,
                "triage did not complete — routed to needs-info for human triage",
                cwd=self.cwd,
            )
            self.log("triage", ticket=ticket_id, stage="failed", routed="needs-info")
        # Commit triage's output (registry edits + `.scratch` state) in every branch,
        # so the tree is clean before the next dispatch regardless of the outcome.
        self._commit_triage(ticket_id)

    # -- one iteration -----------------------------------------------------

    async def _step(self) -> str:
        """Run a single iteration. Returns "continue" or "stop:<reason>"."""
        args = self.args

        # 1. Precondition — escalation queue not full.
        queue = issues.list_status("needs-decision", cwd=self.cwd)
        if len(queue) >= args.queue_threshold:
            self.log("halt", reason="queue-threshold", queue=len(queue),
                     threshold=args.queue_threshold)
            return "stop:queue-threshold"

        # 2. Pick the first ready-for-agent ticket on the unclaimed frontier.
        #    If none, fall back to triaging a bare ticket so the frontier refills.
        ticket = self._pick_ticket()
        if ticket is None:
            triage_ticket = self._pick_triage_ticket()
            if triage_ticket is not None:
                triage_id = triage_ticket["id"]
                if args.dry_run:
                    self.log("dry-run", would="triage", ticket=triage_id,
                             title=triage_ticket.get("title"))
                    return "stop:dry-run"
                await self._triage(triage_id)
                return "continue"
            self.log("halt", reason="no-ready")
            return "stop:no-ready"
        ticket_id = ticket["id"]

        # 3. Attempt cap — a cursed ticket shouldn't burn the whole run.
        attempts = issues.read_attempts(ticket)
        if attempts >= args.max_attempts:
            if args.dry_run:
                self.log("dry-run", would="escalate", ticket=ticket_id, attempts=attempts)
                return "stop:dry-run"
            self._escalate(
                ticket_id,
                f"auto-escalated after {attempts} attempts — not ambiguous, just hard",
            )
            self.log("escalate", ticket=ticket_id, reason="attempt-cap", attempts=attempts)
            return "continue"

        if args.dry_run:
            self.log("dry-run", would="dispatch", ticket=ticket_id,
                     title=ticket.get("title"), attempts=attempts)
            return "stop:dry-run"

        # 4. Claim + mark in-progress.
        issues.claim(ticket_id, WORKER_ASSIGNEE, cwd=self.cwd)
        issues.set_status(ticket_id, "in-progress", cwd=self.cwd)
        self.log("dispatch", ticket=ticket_id, title=ticket.get("title"), attempts=attempts)

        # 5. Run the worker to a stop, then validate before accepting.
        issue = issues.show(ticket_id, cwd=self.cwd)
        prompt = worker.build_worker_prompt(issue, args.design, args.vocab)
        await worker.drive(self.command, str(self.cwd), prompt)
        await self._validate_and_finish(ticket_id, attempts)
        return "continue"

    # -- driver ------------------------------------------------------------

    async def run(self) -> int:
        args = self.args
        self.log("start", design=args.design, feature=args.feature,
                 max_attempts=args.max_attempts, queue_threshold=args.queue_threshold,
                 max_dispatches=args.max_dispatches, once=args.once, dry_run=args.dry_run)
        dispatches = 0
        while True:
            if dispatches >= args.max_dispatches:
                self.log("halt", reason="max-dispatches", dispatches=dispatches)
                print("[clockwork] hit --max-dispatches safety cap.", flush=True)
                return 0

            token = await self._step()
            if token.startswith("stop:"):
                self._explain_stop(token.removeprefix("stop:"))
                return 0

            dispatches += 1
            if args.once:
                self.log("halt", reason="once")
                return 0

    def _explain_stop(self, reason: str) -> None:
        if reason == "queue-threshold":
            print("[clockwork] escalation queue is full — run a design session "
                  "(`/design-session`) to clear it, then re-run clockwork.", flush=True)
        elif reason == "no-ready":
            print("[clockwork] no ready tickets — nothing workable remains.", flush=True)
        elif reason == "dry-run":
            print("[clockwork] dry run: nothing dispatched.", flush=True)
