"""Streaming pretty-printer for `PiRpcClient` events, shared by every agent run
the loop drives (`worker.drive`)."""

from __future__ import annotations

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


def trunc(text: str, width: int = 80) -> str:
    "Truncate string without collapsing whitespace"
    if len(text) <= width:
        return text
    return text[:width - 1] + "…"


class EventFormatter:
    def __init__(self):
        self.thinking: bool = False
        self.tool_uses: dict[str, ToolUseEvent] = {}

    def remember_tool_use(self, event: ToolUseEvent) -> None:
        self.tool_uses[event.call_id] = event

    def finish_tool_use(self, event: ToolResultEvent) -> None:
        if event.call_id not in self.tool_uses:
            return
        args = self.tool_uses.pop(event.call_id).arguments
        if event.name == "bash":
            print(f"┌ $ {args.get('command')}", flush=True)
        else:
            print(f"┌ + {event.name} {args.get('path')}", flush=True)
        lines = [f"│ {trunc(t)}" for t in event.output.splitlines()]
        if len(lines) > 8:
            lines = lines[:7] + ["│ …"]
        print("\n".join(lines), end="\n\n", flush=True)

    def print(self, event: Event) -> None:
        if isinstance(event, ReasoningEvent):
            if not self.thinking:
                print("╶ Thinking…", flush=True)
            self.thinking = True
            print(event.text, end="\n\n" if event.done else "", flush=True)
            return
        else:
            self.thinking = False

        if isinstance(event, (SessionStartEvent, TurnStartEvent, TurnEndEvent)):
            return

        if isinstance(event, UserMessageEvent):
            print(f"User: {event.text}", flush=True)
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
