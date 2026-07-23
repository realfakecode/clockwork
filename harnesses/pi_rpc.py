"""Async client for the `pi --mode rpc` JSONL protocol (see docs/rpc.md).

Covers a working subset of the protocol: sending/steering messages, model and
thinking-level control, abort, get_state, and bash. Extension UI dialog
requests (select/confirm/input/editor) can't be shown headless, so they are
auto-dismissed (cancelled) to keep the agent from blocking on stdin waiting for
a reply; the fire-and-forget UI requests (notify/setStatus/...) are ignored.

Output events are translated into a small set of harness-agnostic dataclasses
(reasoning, tool use/result, user/agent message, turn and session
start/end) instead of exposing the protocol's raw event shapes.

One `PiRpcClient` instance owns exactly one subprocess and one session --
there is no session switching or multi-session management here.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


# High-water mark for the subprocess stdout StreamReader's internal buffer
# (asyncio's default is 64 KiB). The read loop pulls fixed-size chunks and
# frames lines itself, so this bound never caps a single line's length -- it
# only governs how much unread data asyncio buffers before pausing the
# transport for backpressure. A single pi event can be large: a big tool
# result or file read, or a long agent/thinking message, which the protocol
# re-echoes in full on every streaming delta. Keep it generous.
_STDOUT_LIMIT = 64 * 1024 * 1024

# Size of each stdout read. Lines are reassembled from these chunks by
# splitting on LF, so no line-length ceiling applies -- unlike
# `StreamReader.readline()`, which raises `ValueError` once a line grows past
# the buffer limit and takes the reader down with it.
_READ_CHUNK = 64 * 1024

# Extension UI methods that block pi on stdin until they get an
# `extension_ui_response`. The fire-and-forget ones (notify/setStatus/...) do
# not block and need no reply.
_UI_DIALOG_METHODS = frozenset({"select", "confirm", "input", "editor"})


# ---------------------------------------------------------------------------
# Generalized event types
# ---------------------------------------------------------------------------


@dataclass
class Event:
    """Base class for all events yielded by `PiRpcClient.events()`."""


@dataclass
class SessionStartEvent(Event):
    """The underlying process has started and is ready for commands."""


@dataclass
class SessionEndEvent(Event):
    """The underlying process has exited. Terminal event; `events()` stops after this."""

    exit_code: int | None = None
    # Set when the reader stopped because of an exception rather than a clean
    # process exit, so a consumer watching the event stream sees *why* it ended
    # immediately -- without waiting for close() to re-raise.
    error: str | None = None


@dataclass
class TurnStartEvent(Event):
    """A new turn (one assistant response plus any resulting tool calls) has begun."""


@dataclass
class TurnEndEvent(Event):
    """A turn has completed."""

    stop_reason: str | None = None


@dataclass
class AgentSettledEvent(Event):
    """The agent run has fully settled: no automatic retry, compaction retry, or
    queued continuation remains. This is the authoritative "the prompt is done,
    pi is now waiting for the next command" signal. A per-turn `TurnEndEvent` is
    not -- a turn can end mid-run, and even the final one may carry a stop reason
    of `length`/`error`/`aborted` rather than `stop`."""


@dataclass
class UserMessageEvent(Event):
    """A user message was sent to the agent (prompt or steer)."""

    text: str


@dataclass
class ReasoningEvent(Event):
    """A chunk of the model's reasoning/thinking output. `done` marks the last chunk."""

    text: str
    done: bool = False


@dataclass
class AgentMessageEvent(Event):
    """A chunk of the agent's visible reply text. `done` marks the last chunk."""

    text: str
    done: bool = False


@dataclass
class ToolUseEvent(Event):
    """The agent invoked a tool."""

    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultEvent(Event):
    """A tool finished executing."""

    call_id: str
    name: str
    output: str
    is_error: bool = False


def _translate(payload: dict[str, Any]) -> list[Event]:
    """Translate one raw protocol event into zero or more generalized events."""
    kind = payload.get("type")

    if kind == "turn_start":
        return [TurnStartEvent()]

    if kind == "turn_end":
        message = payload.get("message") or {}
        return [TurnEndEvent(stop_reason=message.get("stopReason"))]

    if kind == "agent_settled":
        return [AgentSettledEvent()]

    if kind == "message_update":
        delta = payload.get("assistantMessageEvent") or {}
        delta_kind = delta.get("type")
        if delta_kind == "text_delta":
            return [AgentMessageEvent(text=delta.get("delta", ""))]
        if delta_kind == "text_end":
            return [AgentMessageEvent(text="", done=True)]
        if delta_kind == "thinking_delta":
            return [ReasoningEvent(text=delta.get("delta", ""))]
        if delta_kind == "thinking_end":
            return [ReasoningEvent(text="", done=True)]
        return []

    if kind == "tool_execution_start":
        return [
            ToolUseEvent(
                call_id=payload.get("toolCallId", ""),
                name=payload.get("toolName", ""),
                arguments=payload.get("args") or {},
            )
        ]

    if kind == "tool_execution_end":
        result = payload.get("result") or {}
        content = result.get("content")
        text = "".join(
            block.get("text", "")
            for block in (content if isinstance(content, list) else [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return [
            ToolResultEvent(
                call_id=payload.get("toolCallId", ""),
                name=payload.get("toolName", ""),
                output=text,
                is_error=bool(payload.get("isError")),
            )
        ]

    # agent_start/agent_end, queue_update, compaction_*, auto_retry_*,
    # extension_error, etc. are ignored. extension_ui_request is handled in the
    # read loop (dialogs auto-dismissed), not here.
    return []


class RpcError(Exception):
    """Raised when a command receives a `success: false` response, or the
    process exits before responding."""

    def __init__(self, command: str, message: str):
        super().__init__(f"{command}: {message}")
        self.command = command
        self.message = message


class PiRpcClient:
    """Async wrapper around one `pi --mode rpc`-style subprocess."""

    def __init__(self, command: list[str], *, cwd: str | None = None):
        """`command` is passed straight to subprocess, e.g.
        `["pi", "--mode", "rpc", "--no-session"]`.
        """
        self._command = command
        self._cwd = cwd
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._events: asyncio.Queue[Event] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)
        # Pi does a weird thing on the message that's just before
        # waiting for a user response. It doesn't output the thinking_end
        # event at the end of the thinking- it goes straight into the
        # text_delta events for the written response, and then at the very
        # end emits thinking_end followed by text_end. These flags track
        # if we've seen the transition from thought -> answer without the
        # thinking_end event so we can patch the emitted stream to look
        # normal (end the thinking block before starting the response,
        # suppress the thinking_end at the very end)
        self._unfinished_reasoning = False
        self._saw_abrupt_transition = False

    async def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("already started")
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            cwd=self._cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=_STDOUT_LIMIT,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._events.put(SessionStartEvent())

    async def close(self) -> None:
        """Terminate the subprocess (if still running) and wait for the reader
        to finish. Safe to call more than once."""
        if self._process is not None and self._process.returncode is None:
            await self._terminate_process()
        if self._reader_task is not None:
            # Don't just await the reader task and trust it to unblock once
            # the process is dead -- readline() can still be sitting on a
            # pending low-level read with no guarantee anything wakes it (no
            # EOF, no new data). Cancel it explicitly; _read_loop catches the
            # cancellation right at the readline() await and falls through to
            # its own `finally`, so a SessionEndEvent is still delivered.
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

    async def _terminate_process(self) -> None:
        """SIGTERM, then SIGKILL after a grace period if it hasn't died.
        Bounded so nothing awaiting the process (or the reader task) can hang
        on a subprocess that ignores or is too wedged to handle SIGTERM."""
        assert self._process is not None
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()

    async def __aenter__(self) -> "PiRpcClient":
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -- event stream ------------------------------------------------------

    async def events(self) -> AsyncIterator[Event]:
        """Yield generalized events until the session ends. Terminates after
        yielding a `SessionEndEvent`."""
        while True:
            event = await self._events.get()
            yield event
            if isinstance(event, SessionEndEvent):
                return

    # -- commands ------------------------------------------------------

    async def send_message(
        self,
        message: str,
        *,
        images: list[dict[str, Any]] | None = None,
        streaming_behavior: str | None = None,
    ) -> None:
        """Send a user prompt. Also enqueues a `UserMessageEvent`, since the
        protocol itself does not echo prompts back as events."""
        command: dict[str, Any] = {"type": "prompt", "message": message}
        if images:
            command["images"] = images
        if streaming_behavior:
            command["streamingBehavior"] = streaming_behavior
        await self._events.put(UserMessageEvent(text=message))
        await self._send(command)

    async def steer(self, message: str) -> None:
        """Queue a steering message while the agent is running."""
        await self._events.put(UserMessageEvent(text=message))
        await self._send({"type": "steer", "message": message})

    async def abort(self) -> None:
        await self._send({"type": "abort"})

    async def set_model(self, provider: str, model_id: str) -> dict[str, Any]:
        return await self._send(
            {"type": "set_model", "provider": provider, "modelId": model_id}
        )

    async def set_thinking_level(self, level: str) -> None:
        """`level` is one of "off", "minimal", "low", "medium", "high",
        "xhigh", "max" (the last two only if the model supports them)."""
        await self._send({"type": "set_thinking_level", "level": level})

    async def get_state(self) -> dict[str, Any]:
        return await self._send({"type": "get_state"})

    async def bash(self, command: str) -> dict[str, Any]:
        return await self._send({"type": "bash", "command": command})

    # -- internals ------------------------------------------------------

    async def _send(self, command: dict[str, Any]) -> Any:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("client not started; call start() or use `async with`")
        req_id = str(next(self._ids))
        command = {"id": req_id, **command}
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        self._process.stdin.write((json.dumps(command) + "\n").encode("utf-8"))
        await self._process.stdin.drain()
        return await future

    async def _maybe_dismiss_dialog(self, payload: dict[str, Any]) -> None:
        """Answer a blocking extension UI dialog so pi doesn't wedge on stdin.

        Headless there is no UI to present, so a dialog is auto-dismissed
        (cancelled): pi unblocks and the extension proceeds with its
        no-selection default -- select/input/editor see `undefined`, confirm
        sees `false` -- exactly as it would when a dialog `timeout` elapses.
        Fire-and-forget requests (notify/setStatus/...) don't block and are left
        alone."""
        if payload.get("method") not in _UI_DIALOG_METHODS:
            return
        request_id = payload.get("id")
        if request_id is None or self._process is None or self._process.stdin is None:
            return
        response = {"type": "extension_ui_response", "id": request_id, "cancelled": True}
        self._process.stdin.write((json.dumps(response) + "\n").encode("utf-8"))
        try:
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def _handle_line(self, raw: bytes) -> None:
        """Decode, parse, and route one JSONL record. A malformed or empty line
        is skipped. An extension UI request is answered here (a blocking dialog
        would wedge pi on stdin forever); everything else goes to `_dispatch`."""
        line = raw.decode("utf-8", errors="replace").rstrip("\r")
        if not line:
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if payload.get("type") == "extension_ui_request":
            await self._maybe_dismiss_dialog(payload)
            return
        self._dispatch(payload)

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        buffer = bytearray()
        error: str | None = None
        try:
            while True:
                try:
                    chunk = await stream.read(_READ_CHUNK)
                except asyncio.CancelledError:
                    # close() cancels us to force an exit -- read() gives no
                    # other guaranteed way to unblock once the process is dead.
                    # Stop reading; fall through to the finally below to still
                    # deliver a SessionEndEvent.
                    break
                if not chunk:
                    # EOF. Flush any unterminated trailing line before stopping;
                    # pi terminates records with LF, but the reference client
                    # processes a trailing partial too, so match it.
                    if buffer:
                        await self._handle_line(bytes(buffer))
                    break
                buffer.extend(chunk)
                # Strict JSONL framing: records are delimited by LF only. Frame
                # lines here rather than via StreamReader.readline() so a single
                # oversized event can't raise and take the reader down.
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    raw = bytes(buffer[:newline])
                    del buffer[: newline + 1]
                    await self._handle_line(raw)
        except Exception as exc:
            # Record the reason so it rides out on SessionEndEvent below, then
            # re-raise so close() surfaces it too -- loud on the live event
            # stream *and* at the lifecycle boundary.
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            # If the reader died on an exception the process is still alive.
            # Terminate it (killing after a grace period if it won't die) so
            # we always reach the SessionEndEvent below and unblock events()
            # -- callers only reach close() once the events() iterator
            # returns, so an unbounded wait() here would hang them too.
            if self._process.returncode is None:
                await self._terminate_process()
            exit_code = self._process.returncode
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        RpcError("<process>", error or "process exited before responding")
                    )
            self._pending.clear()
            await self._events.put(SessionEndEvent(exit_code=exit_code, error=error))

    def _dispatch(self, payload: dict[str, Any]) -> None:
        if payload.get("type") == "response":
            future = self._pending.pop(payload.get("id"), None)
            if future is None or future.done():
                return
            if payload.get("success"):
                future.set_result(payload.get("data"))
            else:
                future.set_exception(
                    RpcError(payload.get("command", "?"), payload.get("error", "unknown error"))
                )
            return

        for event in _translate(payload):
            # Handle the dumb ordering thing mentioned in the ctor comment
            if isinstance(event, ReasoningEvent):
                if self._saw_abrupt_transition and event.done:
                    self._saw_abrupt_transition = False
                    continue
                self._unfinished_reasoning = not event.done
            if isinstance(event, AgentMessageEvent) and self._unfinished_reasoning:
                # Insert the event we eventually skip, but at the correct spot
                self._events.put_nowait(ReasoningEvent(text="", done=True))
                self._saw_abrupt_transition = True
                self._unfinished_reasoning = False

            self._events.put_nowait(event)
