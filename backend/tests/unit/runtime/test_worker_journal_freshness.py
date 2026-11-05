from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRunTransition
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import FleetRunTerminalIntent, OperationAction, ResultFreshness
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.journal_facts import FleetOperationCommitted, JournalEntry, RunEventCommitted
from oh_goon.runtime.journal_index import JournalIndex
from oh_goon.runtime.journal_store import JournalFactStore
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import commit_operation, start_repository, start_run, terminalize_run
from tests.factories.operation_reports import make_operation_report


def _completed_worker_report(
    tmp_path: Path,
) -> tuple[LocalDatabase, Journal, LocalDatabase, Journal, OperationReport]:
    parent_database = LocalDatabase(tmp_path / "state.db")
    parent = Journal(parent_database)
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repository"})
    admit_test_run(parent, "worker-run", OperationAction.INIT, [repository])
    worker_database = LocalDatabase.open_worker(parent_database.capture_worker_context())
    worker = Journal(worker_database)
    start_run(worker, "worker-run")
    operation_id = start_repository(worker, "worker-run", repository)
    report = make_operation_report(
        repository, OperationAction.INIT, fleet_run_id="worker-run", operation_id=operation_id
    )
    commit_operation(worker, report)
    terminalize_run(worker, "worker-run", FleetRunTerminalIntent.COMPLETE)
    return parent_database, parent, worker_database, worker, report


def _entries(database: LocalDatabase) -> tuple[JournalEntry, ...]:
    with database.transaction() as connection:
        return tuple(JournalFactStore.load_entries(connection))


def test_worker_report_is_current_for_original_controller_and_writer_without_relabeling_facts(
    tmp_path: Path,
) -> None:
    parent_database, parent, worker_database, worker, report = _completed_worker_report(tmp_path)
    assert worker_database.session_id != parent_database.session_id
    for journal in (parent, worker):
        current = journal.current_capability(report.repository.path, "initialize")
        assert current is not None
        assert current.report == report
        assert current.freshness is ResultFreshness.CURRENT
    committed = [
        entry for entry in _entries(parent_database) if isinstance(entry, FleetOperationCommitted)
    ]
    assert len(committed) == 1
    assert committed[0].session_id == worker_database.session_id
    reopened = Journal(LocalDatabase(parent_database.path))
    after_restart = reopened.current_capability(report.repository.path, "initialize")
    assert after_restart is not None
    assert after_restart.freshness is ResultFreshness.STALE
    assert reopened.require_operation(report.provenance.operation_id) == report


def test_worker_freshness_keeps_later_invalidation_and_direct_operation_rules(
    tmp_path: Path,
) -> None:
    _database, parent, _worker_database, _worker, report = _completed_worker_report(tmp_path)
    direct = make_operation_report(report.repository, OperationAction.UPDATE, fleet_run_id=None)
    commit_operation(parent, direct)
    initialization = parent.current_capability(report.repository.path, "initialize")
    updated = parent.current_capability(report.repository.path, "update")
    assert initialization is not None and initialization.freshness is ResultFreshness.STALE
    assert updated is not None and updated.freshness is ResultFreshness.CURRENT


@pytest.mark.parametrize("attack", ("run", "repository", "capability", "operation"))
def test_controller_freshness_cannot_be_inferred_from_an_inexact_run_report_binding(
    tmp_path: Path, attack: str
) -> None:
    database, _parent, _worker_database, _worker, report = _completed_worker_report(tmp_path)
    entries = _entries(database)
    valid = JournalIndex.build(entries)
    detail = valid.run_detail("worker-run")
    assert detail is not None and detail.operations == (report,)
    changed: list[JournalEntry] = []
    for entry in entries:
        if not isinstance(entry, FleetOperationCommitted):
            changed.append(entry)
            continue
        if attack == "run":
            report = report.model_copy(
                update={
                    "provenance": report.provenance.model_copy(
                        update={"fleet_run_id": "another-run"}
                    )
                }
            )
        elif attack == "repository":
            report = report.model_copy(
                update={
                    "repository": RepositoryIdentity.model_validate({"path": tmp_path / "another"})
                }
            )
        elif attack == "capability":
            report = report.model_copy(
                update={
                    "capability": make_operation_report(
                        report.repository, OperationAction.UPDATE, fleet_run_id=None
                    ).capability
                }
            )
        else:
            report = report.model_copy(
                update={
                    "provenance": report.provenance.model_copy(
                        update={"operation_id": "another-operation"}
                    )
                }
            )
        changed.append(replace(entry, report=report))
    with pytest.raises(InvalidRunTransition):
        JournalIndex.build(tuple(changed))


def test_unaccepted_fleet_report_does_not_inherit_the_controller_freshness_session(
    tmp_path: Path,
) -> None:
    database, _parent, worker_database, _worker, report = _completed_worker_report(tmp_path)
    admitted, started, *_rest = _entries(database)
    assert isinstance(admitted, RunEventCommitted)
    assert isinstance(started, RunEventCommitted)
    # A report attached to a non-completion fact is not the original accepting
    # Fleet operation. It cannot gain freshness from merely naming the Run.
    unaccepted = FleetOperationCommitted(
        revision=started.revision,
        session_id=worker_database.session_id,
        report=report,
        event=started.event,
    )
    index = JournalIndex.build((admitted, unaccepted))
    viewed = index.current_capability(
        database.session_id, report.repository.path, "initialize", None
    )
    assert viewed is not None
    assert viewed.freshness is ResultFreshness.STALE
