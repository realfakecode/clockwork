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


def _log_ticket(line: str) -> int | None:
    """The `ticket` field of one run-log JSON line, or None if it has none / is
    malformed. Used to slice the log to a single effort's tickets."""
    try:
        return json.loads(line).get("ticket")
    except ValueError:
        return None


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

    def _pick_ticket(self) -> issues.Issue | None:
        for record in issues.ready_unclaimed(self.cwd, feature=self.args.feature):
            if record.status == "ready-for-agent":
                return record
        return None

    def _pick_triage_ticket(self) -> issues.Issue | None:
        """A bare ticket on the unclaimed frontier awaiting specification. Only
        consulted when nothing is ready-for-agent, so dispatch never starves."""
        for record in issues.ready_unclaimed(self.cwd, feature=self.args.feature):
            if record.status == "needs-triage":
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
        """True if the worker touched anything outside `.scratch/`. An empty diff
        doesn't prove the ticket is unimplemented on its own — feeds the validator's
        skepticism prompt instead of gating directly; see `_validate_and_finish`."""
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

    def _commit_ticket(self, ticket_id: int, issue: issues.Issue) -> None:
        """One commit per resolved ticket so the tree is clean before the next
        dispatch — an uncommitted diff otherwise leaks into the next worker's and
        the read-only validator's view (`git diff`/`git status`). Simple by design:
        stage everything (including this ticket's `.scratch` state) and commit."""
        title = (issue.title or "").strip()
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
        view AND mislead `_has_code_changes` into reading a stray registry edit as
        the worker's own changes. Staging `.scratch` too keeps each phase's work atomic."""
        ok, detail = self._git_commit_all(f"triage #{ticket_id}")
        if not ok:
            self.log("commit", ticket=ticket_id, stage="triage", ok=False, error=detail)
        elif detail != "nothing to commit":
            self.log("commit", ticket=ticket_id, stage="triage", ok=True, sha=detail or None)

    def _commit_milestone(self, map_id: int, note: str) -> None:
        """Commit the milestone phase's `.scratch` output (round/reviewed labels,
        review + retrospective comments, any filed follow-up tickets) before the next
        dispatch — the same clean-tree invariant `_commit_triage` upholds. The review
        and retrospective touch only the issue tracker, never project code."""
        ok, detail = self._git_commit_all(f"milestone #{map_id}: {note}")
        if not ok:
            self.log("commit", ticket=map_id, stage="milestone", ok=False, error=detail)
        elif detail != "nothing to commit":
            self.log("commit", ticket=map_id, stage="milestone", ok=True, sha=detail or None)

    def _accept(self, ticket_id: int, issue: issues.Issue) -> None:
        for i in range(len(issue.acceptance_criteria or [])):
            issues.check_criterion(ticket_id, i, cwd=self.cwd)
        issues.resolve(
            ticket_id, cwd=self.cwd,
            answer="validated: test gate + independent validator both passed",
        )
        self._commit_ticket(ticket_id, issue)
        self.log("done", ticket=ticket_id)

    async def _validate_and_finish(self, ticket_id: int, attempts: int) -> None:
        issue = issues.show(ticket_id, cwd=self.cwd)
        if issue.status == "needs-decision":
            # Worker self-escalated (QUESTION comment already in .scratch). Discard
            # its half-done code but keep the escalation payload for the design phase.
            self._reset_worktree(ticket_id)
            self.log("escalate", ticket=ticket_id, reason="agent")
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
        #    A worker that changed no code outside `.scratch/` isn't automatically a
        #    failure — a retry can land on a ticket already satisfied by earlier work,
        #    and a parent ticket can be genuinely done once all its children are. The
        #    validator is told so it stays skeptical rather than trusting a green
        #    pre-existing test suite, which proves nothing changed, not that anything
        #    was built.
        prompt = worker.build_validator_prompt(
            issue, self.args.design, self.args.vocab, summary,
            empty_diff=not self._has_code_changes(),
        )
        reply = await worker.drive(self.command, str(self.cwd), prompt,
                                    label=f"validator #{ticket_id}")
        verdict, reason = worker.parse_verdict(reply)
        if verdict == "none":
            self.log("validate", ticket=ticket_id, stage="agent", ok=False,
                     reason="no verdict marker — re-running validator")
            reply = await worker.drive(self.command, str(self.cwd), prompt,
                                       label=f"validator #{ticket_id} (retry)")
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
        await worker.drive(self.command, str(self.cwd), prompt,
                           label=f"triage #{ticket_id}")

        status = issues.show(ticket_id, cwd=self.cwd).status
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

    # -- milestone review + retrospective (a cleared effort's frontier) ----
    #
    # The third frontier-refill altitude, above triage. When a `wayfinding` map's
    # charted frontier has fully cleared, a whole-effort review judges the assembled
    # work against the map's Destination — the altitude the per-ticket validator
    # can't reach. With teeth enabled it files thin follow-up tickets for critical
    # gaps; those re-open the frontier, and the review re-fires when they clear. The
    # empty (nothing-filed) pass is the fixpoint: it marks the frontier reviewed and
    # unlocks a one-shot retrospective that proposes canon changes for a human.

    @staticmethod
    def _all_children_terminal(children: list[issues.Issue]) -> bool:
        return bool(children) and all(
            c.status in issues.TERMINAL_STATUSES for c in children
        )

    def _pick_completed_map(self) -> issues.Issue | None:
        """A `wayfinding` map whose charted frontier has fully cleared and whose
        current size exceeds its last clean review — so a settled map stays quiet
        while fix tickets or graduated fog reopen it. Only consulted when nothing is
        ready or awaiting triage, so per-ticket work always wins."""
        if not self.args.milestone_review:
            return None
        for candidate in issues.list_status("wayfinding", cwd=self.cwd):
            if self.args.feature and candidate.feature != self.args.feature:
                continue
            if issues.MILESTONE_BLOCKED_LABEL in candidate.labels:
                continue
            kids = issues.children(candidate.id, cwd=self.cwd)
            if not self._all_children_terminal(kids):
                continue
            if len(kids) <= issues.read_numbered_label(candidate, issues.MILESTONE_REVIEWED_PREFIX):
                continue
            return candidate
        return None

    async def _milestone(self, map_issue: issues.Issue) -> None:
        map_id = map_issue.id
        round_n = issues.read_numbered_label(map_issue, issues.MILESTONE_ROUND_PREFIX)

        # Convergence backstop: if the self-heal loop keeps surfacing gaps, stop and
        # flag the effort for a human instead of grinding forever — the map-level
        # analogue of a ticket's attempt cap.
        if round_n >= self.args.milestone_max_rounds:
            issues.add_label(map_id, issues.MILESTONE_BLOCKED_LABEL, cwd=self.cwd)
            issues.comment(
                map_id,
                f"milestone review did not converge after {round_n} rounds — this effort "
                "needs a human look (see the review comments above). Remove the "
                "`milestone-blocked` label once addressed to re-enable review.",
                cwd=self.cwd,
            )
            self.log("milestone", ticket=map_id, stage="blocked", rounds=round_n)
            self._commit_milestone(map_id, "review did not converge — flagged for a human")
            return

        kids = issues.children(map_id, cwd=self.cwd)
        kids_before = {k.id for k in kids}
        issues.set_numbered_label(map_issue, issues.MILESTONE_ROUND_PREFIX, round_n + 1, cwd=self.cwd)
        self.log("milestone", ticket=map_id, stage="review", round=round_n + 1)

        prompt = worker.build_milestone_review_prompt(
            map_issue, kids, self.args.design, self.args.vocab,
            max_tickets=self.args.milestone_max_tickets,
            can_file_tickets=self.args.milestone_file_tickets,
        )
        await worker.drive(self.command, str(self.cwd), prompt,
                           label=f"milestone-review #{map_id}")

        new_kids = [k for k in issues.children(map_id, cwd=self.cwd)
                    if k.id not in kids_before]
        if new_kids:
            # Not the fixpoint: the review filed fix tickets. They re-open the
            # frontier; leaving the reviewed marker unset is what lets the review
            # re-fire once they clear.
            self.log("milestone", ticket=map_id, stage="filed",
                     tickets=[k.id for k in new_kids])
            self._commit_milestone(map_id, f"review filed {len(new_kids)} follow-up ticket(s)")
            return

        # Fixpoint reached — nothing filed. Mark the frontier reviewed at its current
        # size and reset the round counter (re-read the map so the label edits see the
        # round bump just written), then retrospect once over the finished effort.
        fresh = issues.show(map_id, cwd=self.cwd)
        child_count = len(kids)
        issues.set_numbered_label(fresh, issues.MILESTONE_REVIEWED_PREFIX, child_count, cwd=self.cwd)
        issues.clear_numbered_label(fresh, issues.MILESTONE_ROUND_PREFIX, cwd=self.cwd)
        self.log("milestone", ticket=map_id, stage="clean", children=child_count)
        await self._retrospect(map_id)
        self._commit_milestone(map_id, "review clean; retrospective recorded")

    async def _retrospect(self, map_id: int) -> None:
        map_issue = issues.show(map_id, cwd=self.cwd)
        children = issues.children(map_id, cwd=self.cwd)
        excerpt = self._effort_log_excerpt([map_id, *(c.id for c in children)])
        prompt = worker.build_retrospective_prompt(
            map_issue, children, self.args.design, self.args.vocab, excerpt)
        await worker.drive(self.command, str(self.cwd), prompt,
                           label=f"retrospective #{map_id}")

    def _effort_log_excerpt(self, ids: list[int], tail: int = 4000) -> str:
        """The run-log lines for this effort's tickets — the retrospective's raw
        signal (dispatch/retry/validate/escalate counts). Best-effort: a missing or
        unreadable log is not fatal."""
        try:
            lines = self.log_path.read_text().splitlines()
        except OSError:
            return "(run log unavailable)"
        wanted = set(ids)
        kept = [line for line in lines if _log_ticket(line) in wanted]
        text = "\n".join(kept)
        return text[-tail:] if text else "(no run-log events recorded for this effort)"

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
                triage_id = triage_ticket.id
                if args.dry_run:
                    self.log("dry-run", would="triage", ticket=triage_id,
                             title=triage_ticket.title)
                    return "stop:dry-run"
                await self._triage(triage_id)
                return "continue"
            # Nothing ready and nothing to triage — the frontier under some effort may
            # have fully cleared. Fall through to a whole-effort milestone review.
            completed_map = self._pick_completed_map()
            if completed_map is not None:
                map_id = completed_map.id
                if args.dry_run:
                    self.log("dry-run", would="milestone", ticket=map_id,
                             title=completed_map.title)
                    return "stop:dry-run"
                await self._milestone(completed_map)
                return "continue"
            self.log("halt", reason="no-ready")
            return "stop:no-ready"
        ticket_id = ticket.id

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
                     title=ticket.title, attempts=attempts)
            return "stop:dry-run"

        # 4. Claim + mark in-progress.
        issues.claim(ticket_id, WORKER_ASSIGNEE, cwd=self.cwd)
        issues.set_status(ticket_id, "in-progress", cwd=self.cwd)
        self.log("dispatch", ticket=ticket_id, title=ticket.title, attempts=attempts)

        # 5. Run the worker to a stop, then validate before accepting.
        issue = issues.show(ticket_id, cwd=self.cwd)
        prompt = worker.build_worker_prompt(issue, args.design, args.vocab)
        await worker.drive(self.command, str(self.cwd), prompt,
                           label=f"worker #{ticket_id}")
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
