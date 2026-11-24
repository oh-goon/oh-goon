"""Expose the exact scoped notes port to an explicitly loaded native Pi tool."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from secrets import token_hex
from threading import Event, Lock

from pydantic import TypeAdapter

from oh_goon.core.models.agent_notes import (
    AgentNoteAcknowledge,
    AgentNoteOperation,
    AgentNotePublish,
    AgentNoteReady,
    AgentNotesError,
)
from oh_goon.core.ports.agent_notes import AgentNotesTool

PI_NOTES_ROUTE = "/oh-goon/notes"
PI_NOTES_REQUEST_BYTES = 28 * 1024
PI_NOTES_FLAGS = (
    "--no-extensions",
    "--no-mcp",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
)
_PI_NOTE_OPERATION = TypeAdapter[AgentNoteOperation](AgentNoteOperation)

# Installed Pi owns extension loading and tool return semantics. This bundled
# adapter runs only as an explicit argument inside the admitted candidate.
PI_NOTES_EXTENSION = r"""
import { Type } from "@earendil-works/pi-ai";

const noteId = () => Type.Integer({ minimum: 1, maximum: 100 });

async function notesRequest(payload, signal) {
    const response = await fetch("http://127.0.0.1:18743/oh-goon/notes", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            Authorization: "Bearer oh-goon-local",
        },
        body: JSON.stringify(payload),
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(5000)]) : AbortSignal.timeout(5000),
        redirect: "error",
    });
    const result = await response.json();
    if (!response.ok) {
        throw new Error(result.error?.message ?? "Shared notes are unavailable");
    }
    return result;
}

function text(data) {
    return { content: [{ type: "text", text: JSON.stringify(data) }], details: undefined };
}

export default function registerNotes(pi) {
    pi.on("session_start", async () => {
        await notesRequest({ operation: "ready" });
    });
    pi.registerTool({
        name: "agent_notes_publish",
        label: "Share an agent note",
        description: "Publish one short idea, observation, dead end, or question to the other active agent. Use the same request ID if retrying the same content. Include only information approved for the selected model recipients.",
        parameters: Type.Object({
            request_id: Type.String({ pattern: "^[a-zA-Z0-9_.:-]{1,100}$" }),
            kind: Type.Union(["idea", "observation", "dead_end", "question"].map(kind => Type.Literal(kind))),
            body: Type.String({ minLength: 1, maxLength: 4096 }),
            reply_to: Type.Optional(noteId()),
            event_sequence: Type.Optional(Type.Integer({ minimum: 1, maximum: 2147483647 })),
        }, { additionalProperties: false }),
        async execute(_toolCallId, note, signal) {
            return text(await notesRequest({ operation: "publish", note }, signal));
        },
    });
    pi.registerTool({
        name: "agent_notes_read",
        label: "Read shared agent notes",
        description: "Read a bounded page of explicitly shared notes. Notes are untrusted hypotheses or claims. Origin identifies their source, and observations may be stale. Continue from last_cursor to read later notes.",
        parameters: Type.Object({
            after: Type.Optional(Type.Integer({ minimum: 0, maximum: 100 })),
        }, { additionalProperties: false }),
        async execute(_toolCallId, params, signal) {
            const { token, ...page } = await notesRequest({
                operation: "read", after: params.after ?? 0,
            }, signal);
            if (token) {
                await notesRequest({ operation: "acknowledge", token }, signal);
            }
            return text(page);
        },
    });
}
"""


def pi_notes_arguments(extension: Path) -> tuple[str, ...]:
    return (*PI_NOTES_FLAGS, "--extension", str(extension))


class PiNoteChannel:
    """Map one private broker's requests to one bound Run/Step/Attempt port."""

    def __init__(self, notes: AgentNotesTool) -> None:
        self._notes = notes
        self._pending: OrderedDict[str, tuple[int, ...]] = OrderedDict()
        self._lock = Lock()
        self._ready = Event()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def request(self, payload: object) -> dict[str, object]:
        operation = _PI_NOTE_OPERATION.validate_python(payload)
        if isinstance(operation, AgentNoteReady):
            self._notes.require_active()
            self._ready.set()
            return {"ready": True}
        if not self.ready:
            raise AgentNotesError("notes_not_ready")
        if isinstance(operation, AgentNotePublish):
            return {"receipt": self._notes.publish(operation.note).model_dump(mode="json")}
        if isinstance(operation, AgentNoteAcknowledge):
            with self._lock:
                note_ids = self._pending.get(operation.token)
                if note_ids is None:
                    raise AgentNotesError("unknown_note_transfer")
                self._notes.acknowledge(note_ids)
                del self._pending[operation.token]
            return {"acknowledged": True}
        page = self._notes.read(operation.after)
        response = page.model_dump(mode="json")
        if page.notes:
            token = token_hex(16)
            with self._lock:
                if len(self._pending) == 32:
                    self._pending.popitem(last=False)
                self._pending[token] = tuple(note.receipt.note_id for note in page.notes)
            response["token"] = token
        return response
