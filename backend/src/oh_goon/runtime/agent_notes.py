"""Persist bounded parallel-work notes under the same cut as Fleet cancellation."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from oh_goon.core.diagnostic_redaction import redact_diagnostic_text
from oh_goon.core.models.agent_notes import (
    NOTE_PAGE_BYTES,
    NOTE_PAGE_LIMIT,
    NOTE_RETENTION_DAYS,
    NOTE_TASK_BYTES,
    NOTE_TASK_LIMIT,
    AgentNote,
    AgentNoteAuthor,
    AgentNoteDelivery,
    AgentNoteInput,
    AgentNotePage,
    AgentNoteReceipt,
    AgentNotesError,
    ParallelAgentTape,
)
from oh_goon.core.models.fleet_plan import ResolvedAgentStep
from oh_goon.core.models.run_events import (
    RunAdmittedEvent,
    StepAttemptCreatedEvent,
    StepCancelledEvent,
    StepFailedEvent,
    StepSkippedEvent,
    StepSucceededEvent,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now

if TYPE_CHECKING:
    from pathlib import Path

    from oh_goon.core.models.agent_providers import ResolvedAgentModel
    from oh_goon.core.models.parallel_agent_work import ParallelAgentWorkIntent
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.execution.coordinator import ExecutionSession
    from oh_goon.runtime.journal import Journal
    from oh_goon.runtime.journal_view import JournalReadView


@dataclass(frozen=True, slots=True)
class SelectedAgentNotes:
    intent: ParallelAgentWorkIntent
    source_path: Path
    notes: tuple[AgentNote, ...]


class AgentNotesStore:
    def __init__(self, database: LocalDatabase, journal: Journal) -> None:
        self._database = database
        self._journal = journal

    def bind(
        self,
        session: ExecutionSession,
        work_id: str,
        repository_id: str,
        operation_id: str,
        step_id: str,
        attempt_number: int,
        model: ResolvedAgentModel,
    ) -> BoundAgentNotes:
        reader = BoundAgentNotes(
            self,
            session,
            work_id,
            repository_id,
            operation_id,
            step_id,
            attempt_number,
            model.provider_name,
            model.model,
        )
        reader.require_active()
        return reader

    def read_page(self, work_id: str, after: int = 0) -> AgentNotePage:
        """Operator projection. The caller owns group authorization."""
        with self._database.transaction() as connection:
            return _page_in(connection, work_id, after, redact=True)

    def select_for_export(self, work_id: str, note_ids: tuple[int, ...]) -> SelectedAgentNotes:
        """Read exact operator-redacted notes and their source from one database cut."""
        from oh_goon.runtime.parallel_agent_work import _work_in

        if (
            not 1 <= len(note_ids) <= NOTE_PAGE_LIMIT
            or any(type(item) is not int or not 1 <= item <= NOTE_TASK_LIMIT for item in note_ids)
            or len(set(note_ids)) != len(note_ids)
        ):
            raise AgentNotesError("invalid_note_selection")
        with self._database.transaction() as connection:
            work = _work_in(connection, work_id)
            if work is None or work.intent.work_id != work_id or not work.intent.notes_enabled:
                raise AgentNotesError("notes_not_authorized")
            if _expired_in(connection, work_id):
                raise AgentNotesError("notes_expired")
            placeholders = ",".join("?" for _ in note_ids)
            rows = connection.execute(
                "SELECT note_id, receipt_json, body FROM parallel_agent_notes "
                f"WHERE work_id=? AND note_id IN ({placeholders}) ORDER BY note_id",
                (work_id, *note_ids),
            ).fetchall()
            if len(rows) != len(note_ids):
                raise AgentNotesError("note_selection_unavailable")
            notes = []
            for note_id, receipt_json, body in rows:
                receipt = AgentNoteReceipt.model_validate_json(receipt_json)
                if body is None or receipt.body_expired:
                    raise AgentNotesError("notes_expired")
                if receipt.note_id != note_id:
                    raise AgentNotesError("note_selection_unavailable")
                notes.append(AgentNote(receipt=receipt, body=redact_diagnostic_text(body)))
            source_path = _selected_note_source(
                self._journal.read_view_from_connection(connection), work.intent, notes
            )
            return SelectedAgentNotes(work.intent, source_path, tuple(notes))

    def receipt(self, work_id: str, run_id: str, request_id: str) -> AgentNoteReceipt | None:
        """Resolve an original writer request without retaining its write capability."""
        with self._database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT receipt_json FROM parallel_agent_notes
                WHERE work_id=? AND writer_run_id=? AND request_id=?
                """,
                (work_id, run_id, request_id),
            ).fetchmany(2)
            if len(rows) > 1:
                raise AgentNotesError("ambiguous_request")
            if not rows:
                return None
            receipt = AgentNoteReceipt.model_validate_json(rows[0][0])
            if _expired_in(connection, work_id):
                return receipt.model_copy(update={"body_expired": True})
            return receipt

    def deliveries(self, work_id: str) -> tuple[AgentNoteDelivery, ...]:
        """Read bounded transfer observations without exposing note bodies."""
        with self._database.transaction() as connection:
            return _deliveries_in(connection, work_id)

    def tape(self, work_id: str) -> ParallelAgentTape:
        from oh_goon.core.errors import StateConflict
        from oh_goon.runtime.parallel_agent_work import _readback_in, _work_in

        with self._database.transaction() as connection:
            work = _work_in(connection, work_id)
            if work is None:
                raise StateConflict("Run coordination refers to an unavailable parallel task")
            expired = _expired_in(connection, work_id)
            publications = tuple(
                AgentNoteReceipt.model_validate_json(row[0]).model_copy(
                    update={"body_expired": expired or row[1] is None}
                )
                for row in connection.execute(
                    "SELECT receipt_json, body FROM parallel_agent_notes "
                    "WHERE work_id=? ORDER BY note_id",
                    (work_id,),
                )
            )
            return ParallelAgentTape(
                work=_readback_in(self._database, connection, work),
                publications=publications,
                deliveries=_deliveries_in(connection, work_id),
            )

    def settle_in(
        self, connection: sqlite3.Connection, work_id: str, *, settled_at: datetime
    ) -> None:
        """Called on the original authority cut once dispatch and child custody settle."""
        self._database.require_atomic_write_cut(connection)
        expires_at = (settled_at + timedelta(days=NOTE_RETENTION_DAYS)).isoformat()
        connection.execute(
            "INSERT OR IGNORE INTO parallel_agent_note_expiry VALUES (?, ?)",
            (work_id, expires_at),
        )

    def expire_in(self, connection: sqlite3.Connection) -> None:
        """Erase elapsed bodies on the retention owner's write cut; keep receipts."""
        self._database.require_atomic_write_cut(connection)
        connection.execute(
            """
            UPDATE parallel_agent_notes SET body=NULL
            WHERE (work_id, note_id) IN (
                SELECT note.work_id, note.note_id FROM parallel_agent_notes AS note
                JOIN parallel_agent_note_expiry AS expiry USING (work_id)
                WHERE note.body IS NOT NULL AND expiry.expires_at<=?
                ORDER BY expiry.expires_at, note.work_id, note.note_id LIMIT 256
            )
            """,
            (utc_now().isoformat(),),
        )


def _deliveries_in(connection: sqlite3.Connection, work_id: str) -> tuple[AgentNoteDelivery, ...]:
    rows = connection.execute(
        """
        SELECT note_id, receiving_run_id, step_id, attempt_id, recipient, model, outcome
        FROM parallel_agent_note_deliveries WHERE work_id=?
        ORDER BY note_id, receiving_run_id, attempt_id
        """,
        (work_id,),
    )
    return tuple(AgentNoteDelivery.model_validate(dict(row)) for row in rows)


@dataclass(frozen=True, slots=True)
class BoundAgentNotes:
    store: AgentNotesStore
    session: ExecutionSession
    work_id: str
    repository_id: str
    operation_id: str
    step_id: str
    attempt_number: int
    recipient: str
    model: str

    def require_active(self) -> None:
        self.session.observe(self.author_in)

    def author_in(self, connection: sqlite3.Connection) -> AgentNoteAuthor:
        return self._author_and_journal_in(connection)[0]

    def _author_and_journal_in(
        self, connection: sqlite3.Connection
    ) -> tuple[AgentNoteAuthor, JournalReadView]:
        from oh_goon.runtime.parallel_agent_work import parallel_notes_enabled_in

        run_id = self.session.require_fleet_run_id()
        if self.session.cancel_requested_in(connection) or not parallel_notes_enabled_in(
            connection, self.work_id, run_id
        ):
            raise AgentNotesError("notes_not_authorized")
        view = self.store._journal.read_view_from_connection(connection)
        events = view.run_events(run_id).events
        admission = events[0]
        assert isinstance(admission, RunAdmittedEvent)
        target = admission.target
        candidate = target.candidate_workspace if isinstance(target, FleetPlanTarget) else None
        step = (
            next((item for item in target.plan.steps if item.step_id == self.step_id), None)
            if isinstance(target, FleetPlanTarget)
            else None
        )
        native = (
            step.workload.native_agent
            if isinstance(step, ResolvedAgentStep) and step.workload.harness == "native"
            else None
        )
        attempt = next(
            (event for event in reversed(events) if isinstance(event, StepAttemptCreatedEvent)),
            None,
        )
        if (
            candidate is None
            or candidate.parallel_work_id != self.work_id
            or candidate.fleet_run_id != run_id
            or candidate.workspace.repository_id != self.repository_id
            or native is None
            or native.agent != "pi"
            or native.interactive
            or native.profile.backend != "bubblewrap"
            or native.uses_persistent_pi_session
            or native.connection is None
            or native.disclosure_recipients != (self.recipient,)
            or native.connection.selection.model != self.model
            or attempt is None
            or attempt.repository_id != self.repository_id
            or attempt.operation_id != self.operation_id
            or attempt.step_id != self.step_id
            or (attempt.attempt_number, self.attempt_number) != (1, 1)
        ):
            raise AgentNotesError("notes_not_authorized")
        for event in events[attempt.sequence :]:
            if (
                isinstance(
                    event,
                    (StepSucceededEvent, StepFailedEvent, StepCancelledEvent, StepSkippedEvent),
                )
                and event.step_id == self.step_id
            ):
                raise AgentNotesError("notes_not_authorized")
        return (
            AgentNoteAuthor(
                fleet_run_id=run_id,
                repository_id=self.repository_id,
                step_id=self.step_id,
                attempt_id=attempt.step_attempt_id,
                base_source_sha256=candidate.source.source_snapshot.sha256,
                workspace_id=candidate.workspace.workspace_id,
            ),
            view,
        )

    def publish(self, note: AgentNoteInput) -> AgentNoteReceipt:
        def publish_in(connection: sqlite3.Connection) -> AgentNoteReceipt:
            author, view = self._author_and_journal_in(connection)
            if _expired_in(connection, self.work_id):
                raise AgentNotesError("notes_expired")
            if note.event_sequence is not None:
                events = view.run_events(author.fleet_run_id).events
                if note.event_sequence > len(events):
                    raise AgentNotesError("unknown_run_evidence")
            return _append_in(connection, self.work_id, author, note)

        return self.session.mutate(publish_in)

    def read(self, after: int) -> AgentNotePage:
        def read_in(connection: sqlite3.Connection) -> AgentNotePage:
            author = self.author_in(connection)
            page = _page_in(connection, self.work_id, after, redact=False)
            connection.executemany(
                """
                INSERT OR IGNORE INTO parallel_agent_note_deliveries
                VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared')
                """,
                (
                    (
                        self.work_id,
                        note.receipt.note_id,
                        author.fleet_run_id,
                        author.step_id,
                        author.attempt_id,
                        self.recipient,
                        self.model,
                    )
                    for note in page.notes
                ),
            )
            return page

        return self.session.mutate(read_in)

    def acknowledge(self, note_ids: tuple[int, ...]) -> None:
        def acknowledge_in(connection: sqlite3.Connection) -> None:
            author = self.author_in(connection)
            connection.executemany(
                """
                UPDATE parallel_agent_note_deliveries SET outcome='acknowledged'
                WHERE work_id=? AND note_id=? AND receiving_run_id=? AND attempt_id=?
                """,
                (
                    (self.work_id, note_id, author.fleet_run_id, author.attempt_id)
                    for note_id in note_ids
                ),
            )

        self.session.mutate(acknowledge_in)


def _selected_note_source(
    view: JournalReadView, intent: ParallelAgentWorkIntent, notes: list[AgentNote]
) -> Path:
    authors = {note.receipt.author for note in notes}
    members = {child.fleet_run_id for child in intent.children}
    sources: set[Path] = set()
    for author in authors:
        if author.fleet_run_id not in members or author.repository_id != intent.repository_id:
            raise AgentNotesError("note_source_unavailable")
        detail = view.run_detail(author.fleet_run_id)
        if detail is None or len(detail.run.targets) != 1:
            raise AgentNotesError("note_source_unavailable")
        repository = detail.run.targets[0]
        target = detail.run.target
        candidate = target.candidate_workspace if isinstance(target, FleetPlanTarget) else None
        if (
            candidate is None
            or candidate.parallel_work_id != intent.work_id
            or candidate.workspace.workspace_id != author.workspace_id
            or candidate.workspace.base_revision != intent.base_revision
            or candidate.source != intent.source
            or repository.repository_id != intent.repository_id
            or author.base_source_sha256 != intent.source.source_snapshot.sha256
        ):
            raise AgentNotesError("note_source_unavailable")
        sources.add(repository.path)
    if len(sources) != 1:
        raise AgentNotesError("note_source_unavailable")
    return sources.pop()


def _expired_in(connection: sqlite3.Connection, work_id: str) -> bool:
    return (
        connection.execute(
            """
        SELECT 1 FROM parallel_agent_note_expiry WHERE work_id=? AND expires_at<=?
        """,
            (work_id, utc_now().isoformat()),
        ).fetchone()
        is not None
    )


def _append_in(
    connection: sqlite3.Connection, work_id: str, author: AgentNoteAuthor, note: AgentNoteInput
) -> AgentNoteReceipt:
    digest = hashlib.sha256(note.model_dump_json().encode("utf-8")).hexdigest()
    row = connection.execute(
        """
        SELECT payload_sha256, receipt_json FROM parallel_agent_notes
        WHERE work_id=? AND writer_run_id=? AND writer_attempt_id=? AND request_id=?
        """,
        (work_id, author.fleet_run_id, author.attempt_id, note.request_id),
    ).fetchone()
    if row is not None:
        if row[0] != digest:
            raise AgentNotesError("note_request_conflict")
        return AgentNoteReceipt.model_validate_json(row[1])
    count, used_bytes = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(body_bytes), 0) FROM parallel_agent_notes WHERE work_id=?",
        (work_id,),
    ).fetchone()
    size = len(note.body.encode("utf-8"))
    if count >= NOTE_TASK_LIMIT or used_bytes + size > NOTE_TASK_BYTES:
        raise AgentNotesError("note_quota_exceeded")
    if note.reply_to is not None and note.reply_to > count:
        raise AgentNotesError("unknown_reply")
    receipt = AgentNoteReceipt(
        note_id=count + 1,
        request_id=note.request_id,
        kind=note.kind,
        author=author,
        reply_to=note.reply_to,
        event_sequence=note.event_sequence,
    )
    connection.execute(
        "INSERT INTO parallel_agent_notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            work_id,
            receipt.note_id,
            author.fleet_run_id,
            author.attempt_id,
            note.request_id,
            digest,
            receipt.model_dump_json(),
            note.body,
            size,
        ),
    )
    return receipt


def _page_in(
    connection: sqlite3.Connection, work_id: str, after: int, *, redact: bool
) -> AgentNotePage:
    if type(after) is not int or not 0 <= after <= NOTE_TASK_LIMIT:
        raise AgentNotesError("invalid_note_cursor")
    if _expired_in(connection, work_id):
        raise AgentNotesError("notes_expired")
    notes: list[AgentNote] = []
    used_bytes = 0
    cursor = after
    continuation = None
    for row in connection.execute(
        """
        SELECT note_id, receipt_json, body, body_bytes FROM parallel_agent_notes
        WHERE work_id=? AND note_id>? ORDER BY note_id LIMIT ?
        """,
        (work_id, after, NOTE_PAGE_LIMIT + 1),
    ):
        note_id, receipt, body, size = row
        if body is None:
            raise AgentNotesError("notes_expired")
        if redact:
            body = redact_diagnostic_text(body)
            size = len(body.encode("utf-8"))
        if len(notes) == NOTE_PAGE_LIMIT or used_bytes + size > NOTE_PAGE_BYTES:
            continuation = cursor
            break
        notes.append(AgentNote(receipt=AgentNoteReceipt.model_validate_json(receipt), body=body))
        used_bytes += size
        cursor = note_id
    return AgentNotePage(notes=tuple(notes), last_cursor=cursor, next_cursor=continuation)
