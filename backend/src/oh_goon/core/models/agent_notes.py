"""Bounded coordination data for one admitted parallel agent group."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.parallel_agent_work import ParallelAgentWorkReadback

NOTE_BODY_BYTES = 4096
NOTE_TASK_LIMIT = 100
NOTE_TASK_BYTES = 256 * 1024
NOTE_PAGE_LIMIT = 20
NOTE_PAGE_BYTES = 16 * 1024
NOTE_RETENTION_DAYS = 30

NoteKind = Literal["idea", "observation", "dead_end", "question"]
NoteRequestId = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")]
NoteSequence = Annotated[int, Field(strict=True, ge=1, le=NOTE_TASK_LIMIT)]
NoteCursor = Annotated[int, Field(strict=True, ge=0, le=NOTE_TASK_LIMIT)]


class AgentNotesError(Exception):
    """A scoped refusal for a note operation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AgentNoteInput(ImmutableModel):
    request_id: NoteRequestId
    kind: NoteKind
    body: str = Field(min_length=1, max_length=NOTE_BODY_BYTES)
    reply_to: NoteSequence | None = None
    event_sequence: int | None = Field(default=None, strict=True, ge=1, le=2**31 - 1)

    @field_validator("body")
    @classmethod
    def require_bounded_body(cls, value: str) -> str:
        if not value.strip() or "\x00" in value or len(value.encode("utf-8")) > NOTE_BODY_BYTES:
            raise ValueError("Agent note body must be nonblank bounded UTF-8")
        return value


class AgentNoteAuthor(ImmutableModel):
    fleet_run_id: str
    repository_id: str
    step_id: str
    attempt_id: str
    base_source_sha256: str
    workspace_id: str


class AgentNoteReceipt(ImmutableModel):
    note_id: NoteSequence
    request_id: NoteRequestId
    kind: NoteKind
    author: AgentNoteAuthor
    reply_to: NoteSequence | None = None
    event_sequence: int | None = None
    body_expired: bool = False


class AgentNote(ImmutableModel):
    receipt: AgentNoteReceipt
    body: str


class AgentNotePage(ImmutableModel):
    authority: Literal["UNTRUSTED_AGENT_CONTEXT"] = "UNTRUSTED_AGENT_CONTEXT"
    observed_candidate_bytes: Literal["unbound"] = "unbound"
    notes: tuple[AgentNote, ...]
    next_cursor: NoteCursor | None = None
    last_cursor: NoteCursor = 0


class AgentNoteDelivery(ImmutableModel):
    note_id: NoteSequence
    receiving_run_id: str
    step_id: str
    attempt_id: str
    harness: Literal["pi"] = "pi"
    recipient: str
    model: str
    outcome: Literal["prepared", "acknowledged"]


class ParallelAgentTape(ImmutableModel):
    """Task coordination metadata within the existing Run readback."""

    work: ParallelAgentWorkReadback
    publications: tuple[AgentNoteReceipt, ...] = ()
    deliveries: tuple[AgentNoteDelivery, ...] = ()


class AgentNoteRead(ImmutableModel):
    operation: Literal["read"]
    after: NoteCursor = 0


class AgentNotePublish(ImmutableModel):
    operation: Literal["publish"]
    note: AgentNoteInput


class AgentNoteAcknowledge(ImmutableModel):
    operation: Literal["acknowledge"]
    token: str = Field(pattern=r"^[0-9a-f]{32}$")


class AgentNoteReady(ImmutableModel):
    operation: Literal["ready"]


AgentNoteOperation = Annotated[
    AgentNoteRead | AgentNotePublish | AgentNoteAcknowledge | AgentNoteReady,
    Field(discriminator="operation"),
]
