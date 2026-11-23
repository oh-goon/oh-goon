"""Bounded control of a Pi RPC child under the original command owner.

Pi owns its session, events and automatic work. A continuation must match the
admitted prior session and native leaf before receiving the fresh frozen prompt.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from threading import RLock
from typing import Literal, Never

from pydantic import Field

from oh_goon.core.errors import PiSessionRefusalCode
from oh_goon.core.models.agent_providers import AgentProviderError, ProviderApi
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.pi_session_storage import (
    PI_SESSION_NEW_FILE,
    PI_SESSION_OBSERVATION_RECORD,
    PI_SESSION_REFUSAL_RECORD,
    PiSessionNativeObservation,
    PiSessionSaveObservation,
    encode_pi_session_entry,
)
from oh_goon.engine.command import MAX_COMMAND_INPUT_BYTES
from oh_goon.integrations.agent_native_execution import decode_native_agent_record

type PiBrokerApi = Literal["openai-completions", "openai-responses", "anthropic-messages"]

_FINAL_ENTRIES_ID = "oh-goon:final-entries"
# Pi 1.3.0 serializes this envelope in order before the unbounded entries payload.
_FINAL_ENTRIES_PREFIX = (
    b'{"id":"oh-goon:final-entries","type":"response","command":"get_entries",'
    b'"success":true,"data":{"entries":['
)


def pi_broker_api(api: ProviderApi) -> PiBrokerApi:
    if api == "openai-chat-completions":
        return "openai-completions"
    if api == "ollama":
        raise AgentProviderError("Pi requires a managed inference API supported by its transport")
    return api


class PiRpcContinuation(ImmutableModel):
    """Exact native reopen facts and fresh broker configuration for one child."""

    prior: PiSessionNativeObservation
    model: str = Field(min_length=1, max_length=240)
    api: PiBrokerApi
    base_url: str = Field(min_length=1, max_length=200)


class PiRpcControl:
    """Nonblocking input source and LF-only output consumer for one native turn.

    The caller continuously consumes stdout and forwards only returned native event
    records. Control responses, including native entries, stay out of public output.
    Callbacks may run on the command owner's reader and supervisor threads.
    """

    def __init__(
        self,
        prompt: bytes,
        *,
        persistent: bool = False,
        continuation: PiRpcContinuation | None = None,
    ) -> None:
        if type(prompt) is not bytes:
            raise AgentProviderError("Pi RPC requires its frozen prompt bytes")
        try:
            message = prompt.decode("utf-8")
        except UnicodeError:
            raise AgentProviderError("Pi RPC prompt must be valid UTF-8") from None
        self._lock = RLock()
        self._continuation = continuation
        self._persistent = persistent or continuation is not None
        self._pending = bytearray()
        self._discarding_final_entries = False
        self._failure: str | None = None
        self._refusal_code: PiSessionRefusalCode | None = None
        self._closed = False
        self._state_seen = False
        self._entries_seen = False
        self._session_id: str | None = None
        self._leaf_id: str | None = None
        self._save_observation: PiSessionSaveObservation | None = None
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
        self._final_entries = self._entries_request(_FINAL_ENTRIES_ID) if self._persistent else b""
        initial = (
            self._encode({"id": "oh-goon:state", "type": "get_state"}),
            self._entries_request("oh-goon:entries"),
        )
        if (
            sum(map(len, (*initial, self._prompt, self._final, self._final_entries)))
            > MAX_COMMAND_INPUT_BYTES
        ):
            raise AgentProviderError("Pi RPC encoded input exceeds the command byte budget")
        self._outbox = deque(initial)

    def _entries_request(self, request_id: str) -> bytes:
        request: dict[str, object] = {"id": request_id, "type": "get_entries"}
        if self._continuation is not None:
            request["since"] = self._continuation.prior.leaf_id
        return self._encode(request)

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

    @property
    def refusal_code(self) -> PiSessionRefusalCode | None:
        with self._lock:
            return self._refusal_code

    def _fail(self, message: str, *, refusal: PiSessionRefusalCode | None = None) -> Never:
        if self._failure is None:
            self._failure = message
            self._refusal_code = refusal
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
                    if self._discarding_final_entries:
                        self._discarding_final_entries = False
                        self._answered.add(_FINAL_ENTRIES_ID)
                        self._close_after_final_responses()
                    else:
                        line = bytes(self._pending)
                        self._pending.clear()
                        event = self._consume(line)
                        if event is not None:
                            forwarded.append(event)
                if self._discarding_final_entries:
                    continue
                if len(self._pending) + len(part) > MAX_COMMAND_INPUT_BYTES:
                    prefix = self._pending[: len(_FINAL_ENTRIES_PREFIX)]
                    prefix.extend(part[: len(_FINAL_ENTRIES_PREFIX) - len(prefix)])
                    if not self._is_outstanding_final_entries(prefix):
                        self._fail("Pi RPC record exceeds the native framing budget")
                    self._pending.clear()
                    self._discarding_final_entries = True
                    continue
                self._pending.extend(part)
            return tuple(forwarded)

    def _is_outstanding_final_entries(self, record: bytes | bytearray) -> bool:
        return (
            _FINAL_ENTRIES_ID in self._requests
            and _FINAL_ENTRIES_ID not in self._answered
            and record.startswith(_FINAL_ENTRIES_PREFIX)
        )

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
        if value["type"] in {PI_SESSION_OBSERVATION_RECORD, PI_SESSION_REFUSAL_RECORD}:
            self._fail("Pi RPC child emitted a relay-owned control record")
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
        if self._persistent:
            if data.get("sessionFile") != str(PI_SESSION_NEW_FILE):
                self._fail(
                    "Pi RPC did not report its exact selected private session file",
                    refusal=PiSessionRefusalCode.IDENTITY_MISMATCH,
                )
            if self._continuation is not None:
                self._require_continuation_state(session_id, data)
            elif not self._state_seen and (
                type(data.get("messageCount")) is not int or data["messageCount"] != 0
            ):
                self._fail("Fresh Pi RPC reported existing or unavailable conversation history")
        elif data.get("sessionFile") is not None:
            self._fail("Ephemeral Pi RPC reported a persistent session file")
        for name in ("isStreaming", "isCompacting"):
            if name in data and (type(data[name]) is not bool or data[name]):
                self._fail("Pi RPC did not report an idle native session")
        if "pendingMessageCount" in data and (
            type(data["pendingMessageCount"]) is not int or data["pendingMessageCount"] != 0
        ):
            self._fail("Pi RPC has unowned pending work")
        return session_id

    def _require_continuation_state(self, session_id: str, data: dict[str, object]) -> None:
        continuation = self._continuation
        assert continuation is not None
        count = data.get("messageCount")
        if session_id != continuation.prior.session_id:
            self._fail(
                "Pi RPC did not reopen the admitted native session",
                refusal=PiSessionRefusalCode.IDENTITY_MISMATCH,
            )
        if type(count) is not int or count < 1:
            self._fail(
                "Pi RPC did not report qualified retained conversation history",
                refusal=PiSessionRefusalCode.HISTORY_UNQUALIFIED,
            )
        model = data.get("model")
        if not isinstance(model, dict) or any(
            model.get(key) != expected
            for key, expected in (
                ("provider", "oh-goon"),
                ("id", continuation.model),
                ("api", continuation.api),
                ("baseUrl", continuation.base_url),
            )
        ):
            self._fail(
                "Pi RPC did not select the current managed inference model",
                refusal=PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH,
            )
        if (
            data.get("isStreaming") is not False
            or data.get("isCompacting") is not False
            or type(data.get("pendingMessageCount")) is not int
            or data["pendingMessageCount"] != 0
        ):
            self._fail("Pi RPC did not prove its admitted session was idle")

    def _response(self, value: dict[str, object]) -> None:
        request_id = value.get("id")
        if (
            type(request_id) is not str
            or request_id not in self._requests
            or request_id in self._answered
        ):
            self._fail("Pi RPC response has an unknown or repeated request identity")
        command = self._requests[request_id]
        final_entries = request_id == _FINAL_ENTRIES_ID
        if value.get("command") != command:
            self._fail("Pi RPC command was refused or mismatched")
        if not final_entries and value.get("success") is not True:
            refusal = None
            if (
                request_id == "oh-goon:entries"
                and self._continuation is not None
                and value.get("success") is False
            ):
                refusal = PiSessionRefusalCode.HISTORY_UNQUALIFIED
            self._fail("Pi RPC command was refused or mismatched", refusal=refusal)
        data = value.get("data")
        self._answered.add(request_id)
        if final_entries:
            # This extra query reports persistence evidence. Its refusal or an
            # absent leaf cannot replace the original conversation disposition.
            if value.get("success") is True and isinstance(data, dict):
                self._save_observation = self._observe_final_entries(data)
        elif not isinstance(data, dict):
            self._fail("Pi RPC response omitted its native data")
        elif request_id == "oh-goon:state":
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
            if self._continuation is not None:
                if leaf != self._continuation.prior.leaf_id or data["entries"]:
                    self._fail(
                        "Pi RPC did not reopen the admitted native leaf",
                        refusal=PiSessionRefusalCode.IDENTITY_MISMATCH,
                    )
            elif self._persistent and any(
                isinstance(entry, dict) and entry.get("type") == "message"
                for entry in data["entries"]
            ):
                self._fail("Fresh Pi RPC reported prior message entries")
            self._leaf_id = leaf
            self._entries_seen = True
        elif request_id == "oh-goon:prompt":
            disposition = data.get("disposition")
            if type(disposition) is not str or disposition not in {"started", "queued"}:
                self._fail("Pi RPC did not accept a qualified native agent turn")
            self._prompt_accepted = True
            self._request_final_if_settled()
        elif request_id == "oh-goon:final-state":
            if self._identity(data) != self._session_id:
                self._fail(
                    "Pi RPC replaced the observed native session identity",
                    refusal=PiSessionRefusalCode.IDENTITY_MISMATCH if self._persistent else None,
                )
        self._close_after_final_responses()
        if self._state_seen and self._entries_seen and not self._prompt_sent:
            self._prompt_sent = True
            self._requests["oh-goon:prompt"] = "prompt"
            self._outbox.append(self._prompt)

    def _close_after_final_responses(self) -> None:
        if "oh-goon:final-state" in self._answered and (
            not self._persistent or _FINAL_ENTRIES_ID in self._answered
        ):
            self._closed = True

    def _observe_final_entries(self, data: dict[str, object]) -> PiSessionSaveObservation | None:
        leaf = data.get("leafId")
        entries = data.get("entries")
        if (
            not isinstance(leaf, str)
            or leaf == self._leaf_id
            or not isinstance(entries, list)
            or not entries
            or not isinstance(entries[-1], dict)
            or entries[-1].get("id") != leaf
        ):
            return None
        assert self._session_id is not None
        digest = hashlib.sha256()
        try:
            for entry in entries:
                digest.update(encode_pi_session_entry(entry))
            return PiSessionSaveObservation(
                session_id=self._session_id,
                leaf_id=leaf,
                entry_count=len(entries),
                entries_sha256=digest.hexdigest(),
            )
        except (TypeError, ValueError, RecursionError):
            return None

    def _request_final_if_settled(self) -> None:
        if self._settled and self._prompt_accepted and not self._final_requested:
            self._final_requested = True
            self._requests["oh-goon:final-state"] = "get_state"
            self._outbox.append(self._final)
            if self._persistent:
                self._requests[_FINAL_ENTRIES_ID] = "get_entries"
                self._outbox.append(self._final_entries)

    def finish(self) -> PiSessionSaveObservation | None:
        with self._lock:
            if self._failure is not None:
                raise AgentProviderError(self._failure)
            if "oh-goon:final-state" not in self._answered or (
                self._pending and not self._is_outstanding_final_entries(self._pending)
            ):
                self._fail("Pi RPC ended before complete correlated native settlement")
            # The final entries query qualifies a save independently of the
            # final native state. At physical EOF its reply may be absent or torn.
            return self._save_observation
