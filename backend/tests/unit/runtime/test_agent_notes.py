from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.core.models.agent_notes import (
    NOTE_RETENTION_DAYS,
    AgentNoteAuthor,
    AgentNoteInput,
    AgentNotesError,
)
from oh_goon.core.time import utc_now
from oh_goon.runtime.agent_notes import AgentNotesStore, _append_in
from oh_goon.runtime.database import LocalDatabase


@pytest.fixture
def notes_database(tmp_path: Path) -> LocalDatabase:
    database = LocalDatabase(tmp_path / "notes.sqlite3")
    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO parallel_agent_work "
            "(work_id, request_hash, intent_json, verifier_json, controller_json) "
            "VALUES ('group', 'request', '{}', '{}', '{}')"
        )
        connection.executemany(
            "INSERT INTO parallel_agent_children (fleet_run_id, work_id, ordinal) "
            "VALUES (?, 'group', ?)",
            (("a", 0), ("b", 1)),
        )
    return database


def _author(run_id: str) -> AgentNoteAuthor:
    return AgentNoteAuthor(
        fleet_run_id=run_id,
        step_id="agent",
        attempt_id=f"attempt-{run_id}",
        repository_id="registered",
        base_source_sha256="a" * 64,
        workspace_id=f"worktree-{run_id}",
    )


def test_committed_retries_and_concurrent_authors_retain_one_ordered_history(
    notes_database: LocalDatabase,
) -> None:
    a = _author("a")
    b = _author("b")
    hypothesis = AgentNoteInput(request_id="same-id", kind="idea", body="Check the parser.")
    alternate = AgentNoteInput(request_id="same-id", kind="idea", body="Check the compiler.")

    def append(author: AgentNoteAuthor, note: AgentNoteInput) -> int:
        with notes_database.transaction(write=True) as connection:
            return _append_in(connection, "group", author, note).note_id

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(append, a, hypothesis)
        second = workers.submit(append, b, alternate)
        assert {first.result(), second.result()} == {1, 2}

    assert append(a, hypothesis) == first.result()
    with pytest.raises(AgentNotesError, match="note_request_conflict"):
        append(a, alternate)
    reply_id = append(
        b,
        AgentNoteInput(
            request_id="reply", kind="observation", body="Read.", reply_to=first.result()
        ),
    )
    assert reply_id == 3

    reopened = AgentNotesStore(notes_database, Mock())
    page = reopened.read_page("group")
    assert page.last_cursor == 3
    assert [note.receipt.note_id for note in page.notes] == [1, 2, 3]
    assert page.notes[2].receipt.reply_to == first.result()
    assert reopened.read_page("group", after=2).notes == (page.notes[2],)
    assert reopened.receipt("group", "a", "same-id") == page.notes[first.result() - 1].receipt


def test_page_and_retention_bounds_exclude_expired_bodies(notes_database: LocalDatabase) -> None:
    author = _author("a")
    with notes_database.transaction(write=True) as connection:
        for number in range(5):
            _append_in(
                connection,
                "group",
                author,
                AgentNoteInput(request_id=f"n-{number}", kind="idea", body="x" * 4096),
            )
    store = AgentNotesStore(notes_database, Mock())
    page = store.read_page("group")
    assert len(page.notes) == 4 and page.last_cursor == page.next_cursor == 4
    assert len(store.read_page("group", after=4).notes) == 1
    with notes_database.transaction(write=True) as connection:
        closed_at = utc_now() - timedelta(days=NOTE_RETENTION_DAYS, seconds=1)
        store.settle_in(connection, "group", settled_at=closed_at)
        store.settle_in(connection, "group", settled_at=utc_now())
        store.expire_in(connection)
    with pytest.raises(AgentNotesError, match="notes_expired"):
        store.read_page("group", after=4)
    receipt = store.receipt("group", "a", "n-0")
    assert receipt is not None and receipt.body_expired
    with notes_database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM parallel_agent_notes WHERE body IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
