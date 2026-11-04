from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRunTransition
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_events import RunStartedEvent
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunOutcome,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
    Verdict,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.journal_facts import FleetOperationCommitted, JournalEntry, RunEventCommitted
from oh_goon.runtime.journal_index import JournalIndex
from oh_goon.runtime.journal_store import JournalFactStore
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import commit_operation, start_repository, start_run, terminalize_run
from tests.factories.operation_reports import make_operation_report


def _running_journal(tmp_path: Path) -> tuple[LocalDatabase, Journal, RepositoryIdentity]:
    database = LocalDatabase(tmp_path / "journal.sqlite")
    journal = Journal(database)
    identity = RepositoryIdentity(path=tmp_path / "repository")
    admit_test_run(journal, "recorded-run", OperationAction.INIT, [identity])
    start_run(journal, "recorded-run")
    return database, journal, identity


def _completed_entries(tmp_path: Path) -> tuple[JournalEntry, ...]:
    database, journal, identity = _running_journal(tmp_path)
    operation_id = start_repository(journal, "recorded-run", identity)
    commit_operation(
        journal,
        make_operation_report(
            identity,
            OperationAction.INIT,
            operation_id=operation_id,
            fleet_run_id="recorded-run",
        ),
    )
    terminalize_run(journal, "recorded-run", FleetRunTerminalIntent.COMPLETE)
    with database.transaction() as connection:
        return tuple(JournalFactStore.load_entries(connection))


def test_read_inside_rolled_back_write_cannot_replace_committed_run_projection(
    tmp_path: Path,
) -> None:
    database, journal, _identity = _running_journal(tmp_path)
    pinned = journal.read_view()
    original = pinned.required_run_detail("recorded-run")
    assert original.run.status is FleetRunStatus.RUNNING

    with pytest.raises(RuntimeError, match="rollback the staged cancellation"):
        with database.transaction(write=True) as connection:
            journal.request_cancel_in(connection, "recorded-run")
            staged = journal.read_view_from_connection(connection)
            assert (
                staged.required_run_detail("recorded-run").run.status is FleetRunStatus.CANCELLING
            )
            assert staged.revision > pinned.revision
            raise RuntimeError("rollback the staged cancellation")

    current = journal.read_view()
    assert current.revision == pinned.revision
    assert current.required_run_detail("recorded-run") == original
    assert pinned.required_run_detail("recorded-run") == original
    with database.transaction(write=True) as connection:
        journal.request_cancel_in(connection, "recorded-run")
    assert (
        journal.read_view().required_run_detail("recorded-run").run.status
        is FleetRunStatus.CANCELLING
    )


@pytest.mark.parametrize("sequence", [2, 3])
def test_cached_run_does_not_hide_hash_valid_duplicate_or_invalid_events(
    tmp_path: Path, sequence: int
) -> None:
    database, journal, _identity = _running_journal(tmp_path)
    pinned = journal.read_view()
    original = pinned.required_run_detail("recorded-run")
    duplicate_start = RunStartedEvent(
        event_type=FleetRunEventType.RUN_STARTED,
        sequence=sequence,
        fleet_run_id="recorded-run",
    )
    # Use the original fact writer to retain a valid SQL hash chain while
    # injecting an invalid lifecycle that normal Journal mutation would refuse.
    with database.transaction(write=True) as connection:
        JournalFactStore(database).append_run_event(connection, duplicate_start)

    with pytest.raises(InvalidRunTransition):
        journal.read_view()
    assert pinned.required_run_detail("recorded-run") == original


def test_same_operation_identity_with_changed_report_refreshes_cached_run(
    tmp_path: Path,
) -> None:
    entries = _completed_entries(tmp_path)
    previous = JournalIndex.build(entries)
    original = previous.run_detail("recorded-run")
    assert original is not None and original.outcome is FleetRunOutcome.SUCCESS
    operation = next(entry for entry in entries if isinstance(entry, FleetOperationCommitted))
    failed_report = make_operation_report(
        operation.report.repository,
        OperationAction.INIT,
        operation_id=operation.report.provenance.operation_id,
        fleet_run_id="recorded-run",
        verdict=Verdict.FAILED,
        reason_code=ReasonCode.OPERATION_FAILED,
    )
    changed = tuple(
        replace(entry, report=failed_report) if entry is operation else entry for entry in entries
    )

    current = JournalIndex.build(changed, previous=previous)
    refreshed = current.run_detail("recorded-run")

    assert refreshed is not None
    assert refreshed.outcome is FleetRunOutcome.ATTENTION_REQUIRED
    assert refreshed.operations == (failed_report,)
    assert previous.run_detail("recorded-run") == original
    assert original.outcome is FleetRunOutcome.SUCCESS


def test_unchanged_events_cannot_reuse_a_run_when_its_operation_report_is_missing(
    tmp_path: Path,
) -> None:
    entries = _completed_entries(tmp_path)
    previous = JournalIndex.build(entries)
    original = previous.run_detail("recorded-run")
    assert original is not None and len(original.operations) == 1
    # Retain the exact completion event and omit only its resolving report.
    missing = tuple(
        RunEventCommitted(entry.revision, entry.session_id, entry.event)
        if isinstance(entry, FleetOperationCommitted)
        else entry
        for entry in entries
    )

    with pytest.raises(InvalidRunTransition, match="unknown operation"):
        JournalIndex.build(missing, previous=previous)
    assert previous.run_detail("recorded-run") == original
