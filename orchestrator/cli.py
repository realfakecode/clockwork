"""`clockwork` entry point: kick off the dispatch loop against the current
directory's `.scratch/`."""

from __future__ import annotations

import argparse
import asyncio

from .loop import Clockwork
from .issues import IssuesError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clockwork",
        description="Unattended ticket→implementation loop. Dispatches ready-for-agent "
        "tickets to a headless `pi` worker one at a time, escalating design "
        "questions to the `needs-decision` queue, until no workable ticket remains.",
    )
    parser.add_argument("--design", default="docs/design.md",
                        help="path to the canonical (normative) design doc (default: docs/design.md)")
    parser.add_argument("--vocab", default="docs/vocabulary.md",
                        help="path to the naming registry — canonical concept names, "
                        "maintained by triage, read by workers (default: docs/vocabulary.md)")
    parser.add_argument("--feature",
                        help="scope the ready frontier to one feature")
    parser.add_argument("--validate",
                        help="shell command run in the cwd as the hard test gate after a "
                        "worker stops (e.g. 'uv run pytest -q'); non-zero exit fails the "
                        "attempt. Omit to skip the test gate and rely on the validator agent.")
    parser.add_argument("--validate-timeout", type=int, default=600,
                        help="seconds before the --validate command is killed (default: 600)")
    parser.add_argument("--max-attempts", type=int, default=2,
                        help="auto-escalate a ticket after this many failed attempts (default: 2)")
    parser.add_argument("--queue-threshold", type=int, default=5,
                        help="stop when the needs-decision queue reaches this size (default: 5)")
    parser.add_argument("--max-dispatches", type=int, default=20,
                        help="safety cap on iterations per run (default: 20)")
    parser.add_argument("--milestone-review", action=argparse.BooleanOptionalAction, default=True,
                        help="when a wayfinding map's charted frontier fully clears, review the "
                        "assembled effort against its Destination and retrospect over the run "
                        "(default: on; --no-milestone-review disables both)")
    parser.add_argument("--milestone-file-tickets", action="store_true",
                        help="let the milestone review FILE follow-up tickets for critical gaps — "
                        "the self-healing loop. Off by default: the review only reports its "
                        "findings, and no agent creates tickets unattended.")
    parser.add_argument("--milestone-max-rounds", type=int, default=3,
                        help="give up self-healing a map after this many review rounds still find "
                        "gaps, and flag it for a human (default: 3)")
    parser.add_argument("--milestone-max-tickets", type=int, default=3,
                        help="most follow-up tickets one milestone review may file in a single "
                        "pass (default: 3)")
    parser.add_argument("--model",
                        help="model id passed to `pi --model` (e.g. provider/model)")
    parser.add_argument("--once", action="store_true",
                        help="dispatch a single ticket and stop")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be dispatched without running the worker")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(Clockwork(args).run())
    except IssuesError as exc:
        print(f"error: {exc}", flush=True)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
