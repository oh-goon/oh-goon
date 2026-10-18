from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import start_run


def test_journal_exposes_only_fleet_runs_touched_after_observer_watermark(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    journal = Journal(database)

    revision, details = journal.run_details_changed_after(0)
    assert revision == 0
    assert details == ()

    repository = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "run-1", OperationAction.INIT, [repository])

    revision, details = journal.run_details_changed_after(0)
    assert revision == 1
    assert [item.run.fleet_run_id for item in details] == ["run-1"]
    assert details[0].run.status is FleetRunStatus.QUEUED

    same_revision, unchanged = journal.run_details_changed_after(revision)
    assert same_revision == revision
    assert unchanged == ()

    start_run(journal, "run-1")
    next_revision, changed = journal.run_details_changed_after(revision)
    assert next_revision == 2
    assert [item.run.fleet_run_id for item in changed] == ["run-1"]
    assert changed[0].run.status is FleetRunStatus.RUNNING


def test_journal_observer_watermark_fast_path_does_not_build_read_view(
    tmp_path: Path, monkeypatch
) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    journal = Journal(database)
    repository = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "run-1", OperationAction.INIT, [repository])

    revision, _ = journal.run_details_changed_after(0)

    def fail_read_view(_connection):
        raise AssertionError(
            "unchanged observer watermark must not build the full Journal read view"
        )

    monkeypatch.setattr(journal, "read_view_from_connection", fail_read_view)
    same_revision, unchanged = journal.run_details_changed_after(revision)

    assert same_revision == revision
    assert unchanged == ()


def test_journal_observer_watermark_reports_rewind_without_building_read_view(
    tmp_path: Path, monkeypatch
) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    journal = Journal(database)

    def fail_read_view(_connection):
        raise AssertionError("rewind detection must not build the full Journal read view")

    monkeypatch.setattr(journal, "read_view_from_connection", fail_read_view)
    revision, unchanged = journal.run_details_changed_after(99)

    assert revision == 0
    assert unchanged == ()
