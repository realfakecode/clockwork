"""Streaming pretty-printer for `PiRpcClient` events, shared by every agent run
the loop drives (`worker.drive`)."""

from __future__ import annotations

import difflib

from harnesses import (
    AgentMessageEvent,
    Event,
    ReasoningEvent,
    SessionStartEvent,
    ToolResultEvent,
    ToolUseEvent,
    TurnEndEvent,
    TurnStartEvent,
    UserMessageEvent,
)

# ANSI styling. Kept as bare constants so the format strings below read like the
# lines they produce.
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
MAGENTA = "\x1b[35m"

WIDTH = 100          # truncate long single lines to this
SHELL_LINES = 3      # output lines kept for `bash`
READ_LINES = 3       # output lines kept for reads and other tools
DIFF_LINES = 16      # diff lines kept across all edits in one call


def trunc(text: str, width: int = WIDTH) -> str:
    "Truncate string without collapsing whitespace"
    if len(text) <= width:
        return text
    return text[:width - 1] + "…"


class EventFormatter:
    def __init__(self, label: str | None = None):
        # `label` names the dispatch (e.g. "worker #12"); when set, the repeated
        # prompt is replaced by a one-line banner.
        self.label = label
        self.thinking: bool = False
        self.tool_uses: dict[str, ToolUseEvent] = {}

    def remember_tool_use(self, event: ToolUseEvent) -> None:
        self.tool_uses[event.call_id] = event

    def _body(self, output: str, cap: int, is_error: bool = False) -> None:
        "Print capped tool output under a dim gutter."
        lines = output.splitlines() or [""]
        gutter = f"{RED}│{RESET}" if is_error else f"{DIM}│{RESET}"
        for line in lines[:cap]:
            print(f"{gutter} {trunc(line)}", flush=True)
        extra = len(lines) - cap
        if extra > 0:
            print(f"{DIM}│ … (+{extra} more lines){RESET}", flush=True)
        print(flush=True)

    def _diff(self, args: dict) -> None:
        "Render an edit call as a unified diff of its old→new blocks."
        path = args.get("path", "")
        print(f"{MAGENTA}✎{RESET} {BOLD}edit{RESET} {DIM}{path}{RESET}", flush=True)
        rendered: list[str] = []
        for edit in args.get("edits", []):
            old = edit.get("oldText", "").splitlines()
            new = edit.get("newText", "").splitlines()
            for line in difflib.unified_diff(old, new, lineterm="", n=1):
                if line.startswith(("---", "+++")):
                    continue
                if line.startswith("@@"):
                    rendered.append(f"{DIM}  {line}{RESET}")
                elif line.startswith("+"):
                    rendered.append(f"{GREEN}  {trunc(line)}{RESET}")
                elif line.startswith("-"):
                    rendered.append(f"{RED}  {trunc(line)}{RESET}")
                else:
                    rendered.append(f"{DIM}  {trunc(line)}{RESET}")
        for line in rendered[:DIFF_LINES]:
            print(line, flush=True)
        extra = len(rendered) - DIFF_LINES
        if extra > 0:
            print(f"{DIM}  … (+{extra} more diff lines){RESET}", flush=True)
        print(flush=True)

    def finish_tool_use(self, event: ToolResultEvent) -> None:
        use = self.tool_uses.pop(event.call_id, None)
        args = use.arguments if use else {}

        if event.name == "bash":
            cmd = trunc(args.get("command", "").replace("\n", " "))
            print(f"{GREEN}${RESET} {BOLD}{cmd}{RESET}", flush=True)
            self._body(event.output, SHELL_LINES, event.is_error)
            return

        if "edits" in args:
            self._diff(args)
            return

        print(
            f"{CYAN}◇{RESET} {BOLD}{event.name}{RESET} {DIM}{args.get('path', '')}{RESET}",
            flush=True,
        )
        self._body(event.output, READ_LINES, event.is_error)

    def _end_thinking(self) -> None:
        print(RESET, end="\n\n", flush=True)
        self.thinking = False

    def print(self, event: Event) -> None:
        if isinstance(event, ReasoningEvent):
            if not self.thinking:
                print(f"{DIM}{ITALIC}", end="", flush=True)
                self.thinking = True
            print(event.text, end="", flush=True)
            if event.done:
                self._end_thinking()
            return

        # Any non-reasoning event closes an open thinking block so its styling
        # never leaks onto the following output.
        if self.thinking:
            self._end_thinking()

        if isinstance(event, (SessionStartEvent, TurnStartEvent, TurnEndEvent)):
            return

        if isinstance(event, UserMessageEvent):
            if self.label is not None:
                print(f"\n{CYAN}{BOLD}▶ {self.label}{RESET}\n", flush=True)
            else:
                print(f"{YELLOW}▸{RESET} {trunc(event.text)}", flush=True)
            return

        # We can get a bunch of uses at once, hold onto them
        # so we can print the result with each use
        if isinstance(event, ToolUseEvent):
            self.remember_tool_use(event)
            return

        if isinstance(event, ToolResultEvent):
            self.finish_tool_use(event)
            return

        if isinstance(event, AgentMessageEvent):
            print(event.text, end="\n" if event.done else "", flush=True)
            return

        print(event, flush=True)
