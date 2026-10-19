"""CO-R2a: real original SQLite cut for scheduled child and RUN_ADMITTED.

Hosted SQLite regression only. These tests cannot qualify physical worker absence,
WSL crash durability, or native Enforcer Gate A.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from oh_goon.core.errors import ErrorKind, JournalConflict, ScheduleExecutionStale
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    ExplicitRepositoryScope,
    FleetRunAdmissionIntent,
)
from oh_goon.core.models.schedule import ScheduleFailure
from oh_goon.core.models.schedule_execution import RunningScheduleRun
from oh_goon.core.product_identity import schedule_repository_fleet_run_id
from oh_goon.core.schedule_transitions import fail_schedule_run
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunSource,
    ScheduleExecutionStatus,
    ScheduleFailureStage,
    ScheduleRepositoryRunStatus,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.schedule_execution import ScheduleExecutionEngine
from tests.factories.run_admission import repository_admissions_for
from tests.factories.schedules import persist_active_schedule, schedule_create


def _fixture(tmp_path: Path):
    path = tmp_path / "co-r2a.db"
    db = LocalDatabase(path)
    store = ScheduleExecutionStore(db)
    repo = RepositoryIdentity(path=tmp_path / "repo")
    definition = ScheduleDefinitionStore(db).create(
        "co-r2a-schedule",
        schedule_create(repository_ids=(repo.repository_id,)),
    )
    occurrence = persist_active_schedule(db, store, definition)
    run_id = schedule_repository_fleet_run_id(occurrence.schedule_run_id, repo.repository_id)
    intent = FleetRunAdmissionIntent(
        source=FleetRunSource.SCHEDULED,
        schedule_run_id=occurrence.schedule_run_id,
        capability=occurrence.intent.capability,
        target=occurrence.intent.target,
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(occurrence.intent.target, (repo,)),
    )
    scope = ExplicitRepositoryScope(repository_ids=(repo.repository_id,))
    return path, db, store, repo, occurrence, run_id, intent, scope


def _admit(db, run_id, intent, scope, repo, *, after=None):
    journal = Journal(db)
    with db.transaction(write=True) as connection:
        admitted = journal.admit_run_in(connection, run_id, intent, scope, [repo])
        if after:
            after()
    return admitted


def test_real_original_run_admission_commits_occurrence_in_same_revision(tmp_path: Path):
    _path, db, store, repo, occ, run_id, intent, scope = _fixture(tmp_path)
    previous = db.durable_revision()
    _admit(db, run_id, intent, scope, repo)
    after = store.get(occ.schedule_run_id)
    assert isinstance(after, RunningScheduleRun)
    assert after.record_version == occ.record_version + 1
    assert after.repository_runs[0].status is ScheduleRepositoryRunStatus.RUNNING
    assert after.repository_runs[0].fleet_run_id == run_id
    assert db.durable_revision() == previous + 1
    admitted = Journal(db).read_view().required_run_detail(run_id)
    assert admitted.run.source is FleetRunSource.SCHEDULED
    assert admitted.run.fleet_run_id == run_id


def test_original_cut_rollback_does_not_leave_phantom_running_child(tmp_path: Path):
    _path, db, store, repo, occ, run_id, intent, scope = _fixture(tmp_path)
    revision = db.durable_revision()
    with pytest.raises(RuntimeError, match="before commit"):
        _admit(
            db,
            run_id,
            intent,
            scope,
            repo,
            after=lambda: (_ for _ in ()).throw(RuntimeError("before commit")),
        )
    assert store.get(occ.schedule_run_id) == occ
    assert Journal(db).read_view().run_detail(run_id) is None
    assert db.durable_revision() == revision
    _admit(db, run_id, intent, scope, repo)
    assert store.get(occ.schedule_run_id).repository_runs[0].fleet_run_id == run_id


def test_cancelled_occurrence_rejects_run_before_mutating_journal(tmp_path: Path):
    _path, db, store, repo, occ, run_id, intent, scope = _fixture(tmp_path)
    cancelled = fail_schedule_run(
        occ,
        ScheduleFailure(
            code="CANCEL_BEFORE_FLEET",
            kind=ErrorKind.INTERNAL,
            stage=ScheduleFailureStage.ADVANCE,
            message="cancelled",
        ),
        finished_at=utc_now(),
    )
    store.compare_and_set(occ, cancelled)
    revision = db.durable_revision()
    with pytest.raises(ScheduleExecutionStale, match="cancelled"):
        _admit(db, run_id, intent, scope, repo)
    assert db.durable_revision() == revision
    assert Journal(db).read_view().run_detail(run_id) is None
    assert store.get(occ.schedule_run_id).status is ScheduleExecutionStatus.FAILED


def test_duplicate_admission_rejects_even_when_run_id_is_identical(tmp_path: Path):
    _path, db, store, repo, occ, run_id, intent, scope = _fixture(tmp_path)
    _admit(db, run_id, intent, scope, repo)
    revision = db.durable_revision()
    with pytest.raises(JournalConflict, match="already queued"):
        _admit(db, run_id, intent, scope, repo)
    assert db.durable_revision() == revision
    assert store.get(occ.schedule_run_id).repository_runs[0].fleet_run_id == run_id


def test_reopen_after_lost_ack_observes_committed_child_without_readmission(tmp_path: Path):
    path, db, _store, repo, occ, run_id, intent, scope = _fixture(tmp_path)
    _admit(db, run_id, intent, scope, repo)
    # Model crash after SQLite commit, before control-plane/worker return.
    reopened = LocalDatabase(path)
    store = ScheduleExecutionStore(reopened)

    class GuardedRuns:
        def get(self, _run_id):
            assert _run_id == run_id
            from oh_goon.core.errors import RunNotFound

            raise RunNotFound("worker not running after process death")

        def start_bound(self, *args, **kwargs):
            raise AssertionError("duplicate admission must not occur after restart")

    observed = ScheduleExecutionEngine(store, GuardedRuns()).advance(occ.schedule_run_id)
    assert observed.repository_runs[0].status is ScheduleRepositoryRunStatus.RUNNING
    assert observed.repository_runs[0].fleet_run_id == run_id
    assert Journal(reopened).read_view().run_detail(run_id) is not None


def test_cancel_during_incut_admission_rolls_back_without_phantom_run(tmp_path: Path):
    _path, db, store, repo, occ, run_id, intent, scope = _fixture(tmp_path)
    cancelled = fail_schedule_run(
        occ,
        ScheduleFailure(
            code="CANCEL_RACE",
            kind=ErrorKind.INTERNAL,
            stage=ScheduleFailureStage.ADVANCE,
            message="cancelled",
        ),
        finished_at=utc_now(),
    )
    store.compare_and_set(occ, cancelled)
    with pytest.raises(ScheduleExecutionStale):
        _admit(db, run_id, intent, scope, repo)
    assert Journal(db).read_view().run_detail(run_id) is None


def test_concurrent_connections_can_commit_only_one_occurrence_child(tmp_path: Path):
    path, _db, _store, repo, occ, run_id, intent, scope = _fixture(tmp_path)

    def attempt():
        try:
            _admit(LocalDatabase(path), run_id, intent, scope, repo)
            return "admitted"
        except Exception as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert results.count("admitted") == 1, results
    assert (
        ScheduleExecutionStore(LocalDatabase(path))
        .get(occ.schedule_run_id)
        .repository_runs[0]
        .fleet_run_id
        == run_id
    )


def test_original_fleet_lease_run_and_schedule_binding_share_one_write_cut(tmp_path: Path):
    from oh_goon.runtime.execution import ExecutionCoordinator

    _path, db, store, repo, occ, run_id, intent, scope = _fixture(tmp_path)
    journal = Journal(db)
    execution = ExecutionCoordinator(db)
    revision = db.durable_revision()
    ticket, state = execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=revision,
        mutate=lambda connection: journal.admit_run_in(
            connection,
            run_id,
            intent,
            scope,
            [repo],
        ),
    )
    assert state.fleet_run_id == run_id
    assert ticket._lease.fleet_run_id == run_id
    assert journal.read_view().required_run_detail(run_id).run.fleet_run_id == run_id
    assert store.get(occ.schedule_run_id).repository_runs[0].fleet_run_id == run_id
    assert db.durable_revision() == revision + 1


def test_original_lease_rejects_cancelled_child_and_rolls_back_its_reservation(tmp_path: Path):
    from oh_goon.runtime.execution import ExecutionCoordinator

    _path, db, store, repo, occ, run_id, intent, scope = _fixture(tmp_path)
    cancelled = fail_schedule_run(
        occ,
        ScheduleFailure(
            code="CANCELLED_BEFORE_CUT",
            kind=ErrorKind.INTERNAL,
            stage=ScheduleFailureStage.ADVANCE,
            message="cancelled before reservation",
        ),
        finished_at=utc_now(),
    )
    store.compare_and_set(occ, cancelled)
    revision = db.durable_revision()
    execution = ExecutionCoordinator(db)
    journal = Journal(db)
    with pytest.raises(ScheduleExecutionStale):
        execution.reserve_fleet_with(
            run_id,
            expected_durable_revision=revision,
            mutate=lambda connection: journal.admit_run_in(
                connection, run_id, intent, scope, [repo]
            ),
        )
    assert execution.active_lease() is None
    assert journal.read_view().run_detail(run_id) is None
    assert db.durable_revision() == revision
