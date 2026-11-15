"""Bounded control of one ephemeral Pi RPC child under the command owner.

Pi owns its session, events and automatic work. This adapter correlates the
requested observations, submits one frozen prompt and closes input only after
native settlement and a final identity check. It never adopts a saved session.
"""

from __future__ import annotations

import json
from collections import deque
from threading import RLock
from typing import Never

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.engine.command import MAX_COMMAND_INPUT_BYTES
from oh_goon.integrations.agent_native_execution import decode_native_agent_record


class PiRpcControl:
    """Nonblocking input source and LF-only output consumer for one native turn.

    The caller continuously consumes stdout and forwards only returned native event
    records. Control responses, including native entries, stay out of public output.
    Callbacks may run on the command owner's reader and supervisor threads.
    """

    def __init__(self, prompt: bytes) -> None:
        if type(prompt) is not bytes:
            raise AgentProviderError("Pi RPC requires its frozen prompt bytes")
        try:
            message = prompt.decode("utf-8")
        except UnicodeError:
            raise AgentProviderError("Pi RPC prompt must be valid UTF-8") from None
        self._lock = RLock()
        self._pending = bytearray()
        self._failure: str | None = None
        self._closed = False
        self._complete = False
        self._state_seen = False
        self._entries_seen = False
        self._session_id: str | None = None
        self._leaf_id: str | None = None
        self._prompt_sent = False
        self._prompt_accepted = False
        self._settled = False
        self._final_requested = False
        self._requests = {
            "oh-goon:state": "get_state",
            "oh-goon:entries": "get_entries",
        }
        self._answered: set[str] = set()
        self._prompt = self._encode({"id": "oh-goon:prompt", "type": "prompt", "message": message})
        self._final = self._encode({"id": "oh-goon:final-state", "type": "get_state"})
        initial = tuple(
            self._encode({"id": key, "type": value}) for key, value in self._requests.items()
        )
        if sum(map(len, (*initial, self._prompt, self._final))) > MAX_COMMAND_INPUT_BYTES:
            raise AgentProviderError("Pi RPC encoded input exceeds the command byte budget")
        self._outbox = deque(initial)

    @staticmethod
    def _encode(value: dict[str, object]) -> bytes:
        try:
            return (
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            )
        except UnicodeError:
            raise AgentProviderError("Pi RPC input must be valid UTF-8") from None

    def read_input(self, maximum: int) -> bytes | None:
        if type(maximum) is not int or maximum < 1:
            raise ValueError("Pi RPC input requires a positive chunk size")
        with self._lock:
            if self._outbox:
                value = self._outbox.popleft()
                chunk = value[:maximum]
                if len(value) > maximum:
                    self._outbox.appendleft(value[maximum:])
                return chunk
            return b"" if self._closed else None

    @property
    def session_identity(self) -> tuple[str, str | None] | None:
        """Native provenance only; this does not authorize cross-Run adoption."""
        with self._lock:
            return (
                (self._session_id, self._leaf_id)
                if self._state_seen and self._entries_seen and self._session_id is not None
                else None
            )

    def _fail(self, message: str) -> Never:
        self._failure = self._failure or message
        self._closed = True
        self._outbox.clear()
        raise AgentProviderError(self._failure)

    def feed_stdout(self, text: str) -> tuple[str, ...]:
        with self._lock:
            if self._failure is not None:
                raise AgentProviderError(self._failure)
            try:
                data = text.encode("utf-8")
            except (UnicodeError, AttributeError):
                self._fail("Pi RPC stdout must be valid UTF-8 text")
            forwarded: list[str] = []
            for index, part in enumerate(data.split(b"\n")):
                if index:
                    line = bytes(self._pending)
                    self._pending.clear()
                    event = self._consume(line)
                    if event is not None:
                        forwarded.append(event)
                if len(self._pending) + len(part) > MAX_COMMAND_INPUT_BYTES:
                    self._fail("Pi RPC record exceeds the native framing budget")
                self._pending.extend(part)
            return tuple(forwarded)

    def _consume(self, line: bytes) -> str | None:
        if not line.strip():
            return None
        try:
            value = decode_native_agent_record(line.removesuffix(b"\r").decode("utf-8"))
        except (AgentProviderError, UnicodeError):
            self._fail("Pi RPC stdout contains an invalid JSON record")
        if not isinstance(value, dict) or type(value.get("type")) is not str:
            self._fail("Pi RPC requires native object records")
        if value["type"] == "response":
            self._response(value)
            return None
        if value["type"] == "extension_ui_request":
            self._fail("Pi RPC extension interaction is not qualified in headless execution")
        if (
            value["type"]
            in {
                "agent_start",
                "turn_start",
                "message_start",
                "tool_execution_start",
                "agent_settled",
            }
            and not self._prompt_sent
        ):
            self._fail("Pi RPC activity preceded the admitted prompt")
        if value["type"] == "agent_settled":
            if type(value.get("aborted")) is not bool or self._settled:
                self._fail("Pi RPC settlement is missing or repeated")
            self._settled = True
            self._request_final_if_settled()
        elif self._settled and value["type"] in {
            "agent_start",
            "turn_start",
            "message_start",
            "tool_execution_start",
        }:
            self._fail("Pi RPC activity continued after native settlement")
        return line.decode("utf-8") + "\n"

    def _identity(self, data: dict[str, object]) -> str:
        session_id = data.get("sessionId")
        if (
            type(session_id) is not str
            or not session_id.strip()
            or len(session_id) > 256
            or "\x00" in session_id
        ):
            self._fail("Pi RPC did not report a bounded native session identity")
        if data.get("sessionFile") is not None:
            self._fail("Persistent Pi sessions remain unsupported")
        for name in ("isStreaming", "isCompacting"):
            if name in data and (type(data[name]) is not bool or data[name]):
                self._fail("Pi RPC did not report an idle native session")
        if "pendingMessageCount" in data and (
            type(data["pendingMessageCount"]) is not int or data["pendingMessageCount"] != 0
        ):
            self._fail("Pi RPC has unowned pending work")
        return session_id

    def _response(self, value: dict[str, object]) -> None:
        request_id = value.get("id")
        if (
            type(request_id) is not str
            or request_id not in self._requests
            or request_id in self._answered
        ):
            self._fail("Pi RPC response has an unknown or repeated request identity")
        command = self._requests[request_id]
        if value.get("command") != command or value.get("success") is not True:
            self._fail("Pi RPC command was refused or mismatched")
        data = value.get("data")
        if not isinstance(data, dict):
            self._fail("Pi RPC response omitted its native data")
        self._answered.add(request_id)
        if request_id == "oh-goon:state":
            self._session_id = self._identity(data)
            self._state_seen = True
        elif request_id == "oh-goon:entries":
            leaf = data.get("leafId")
            if (
                "leafId" not in data
                or not isinstance(data.get("entries"), list)
                or leaf is not None
                and (type(leaf) is not str or not leaf.strip() or len(leaf) > 256 or "\x00" in leaf)
            ):
                self._fail("Pi RPC omitted its bounded native leaf observation")
            self._leaf_id = leaf
            self._entries_seen = True
        elif request_id == "oh-goon:prompt":
            disposition = data.get("disposition")
            if type(disposition) is not str or disposition not in {"started", "queued"}:
                self._fail("Pi RPC did not accept a qualified native agent turn")
            self._prompt_accepted = True
            self._request_final_if_settled()
        else:
            if self._identity(data) != self._session_id:
                self._fail("Pi RPC replaced the observed native session identity")
            self._complete = True
            self._closed = True
        if self._state_seen and self._entries_seen and not self._prompt_sent:
            self._prompt_sent = True
            self._requests["oh-goon:prompt"] = "prompt"
            self._outbox.append(self._prompt)

    def _request_final_if_settled(self) -> None:
        if self._settled and self._prompt_accepted and not self._final_requested:
            self._final_requested = True
            self._requests["oh-goon:final-state"] = "get_state"
            self._outbox.append(self._final)

    def finish(self) -> None:
        with self._lock:
            if self._failure is not None:
                raise AgentProviderError(self._failure)
            if self._pending or not self._complete:
                self._fail("Pi RPC ended before complete correlated native settlement")
