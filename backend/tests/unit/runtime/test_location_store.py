from contextlib import contextmanager
from pathlib import Path

import pytest

from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.runtime.state import LocalState
from oh_goon.runtime.state import locations_revision
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import commit_operation, start_run
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_effect_runtime import effect_recovery


def test_locations_round_trip_in_sqlite(tmp_path: Path) -> None:
    location = tmp_path / "repos"
    location.mkdir()
    state = local_state(LocalDatabase(tmp_path / "state.db"))

    assert state.add_location(location) == [location.resolve()]
    assert list(state.read_view().locations) == [location.resolve()]
    assert state.remove_location(location, expected_revision=locations_revision(state.read_view().locations)) == []


def test_concurrent_location_adds_preserve_both_serialized_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from threading import Event, Thread

    database_path = tmp_path / "concurrent-locations.db"
    first_state = local_state(LocalDatabase(database_path))
    second_state = local_state(LocalDatabase(database_path))
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_read = Event()
    release_first = Event()
    second_finished = Event()
    errors: list[BaseException] = []
    original_list = first_state._league_folders.list

    def paused_list() -> tuple[Path, ...]:
        current = original_list()
        first_read.set()
        assert release_first.wait(timeout=5)
        return current

    monkeypatch.setattr(first_state._league_folders, "list", paused_list)

    def add_first() -> None:
        try:
            first_state.add_location(first)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    def add_second() -> None:
        try:
            second_state.add_location(second)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            second_finished.set()

    first_thread = Thread(target=add_first)
    first_thread.start()
    assert first_read.wait(timeout=5)
    second_thread = Thread(target=add_second)
    second_thread.start()
    assert not second_finished.wait(timeout=0.2)
    release_first.set()
    first_thread.join(timeout=10)
    second_thread.join(timeout=10)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not errors
    assert set(local_state(LocalDatabase(database_path)).read_view().locations) == {
        first.resolve(),
        second.resolve(),
    }


def test_locations_persist_across_application_restart(tmp_path: Path) -> None:
    location = tmp_path / "repos"
    location.mkdir()
    database = tmp_path / "state.db"

    local_state(LocalDatabase(database)).add_location(location)
    restarted = local_state(LocalDatabase(database))

    assert list(restarted.read_view().locations) == [location.resolve()]


def test_state_read_view_pins_scope_locations_and_journal_to_one_cut(tmp_path: Path) -> None:
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.vocabulary import OperationAction, ResultFreshness, Verdict

    database = LocalDatabase(tmp_path / "state-cut.db")
    state = local_state(database)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    repository = first_root / "api"
    repository.mkdir()
    identity = RepositoryIdentity(name="api", path=repository)
    state.add_location(first_root)
    state.exclude(
        repository, "Pinned exclusion", expected_durable_revision=state.read_view().durable_revision
    )
    commit_operation(
        state.journal,
        make_operation_report(
            identity, OperationAction.INIT, operation_id="op-init", fleet_run_id=None
        ),
    )

    pinned = state.read_view()
    state.add_location(second_root)
    state.include(repository, expected_durable_revision=state.read_view().durable_revision)
    commit_operation(
        state.journal,
        make_operation_report(
            identity,
            OperationAction.UPDATE,
            verdict=Verdict.UPDATED,
            operation_id="op-update",
            fleet_run_id=None,
        ),
    )
    live = state.read_view()

    pinned_init = pinned.journal.current_capability(repository, "initialize")
    live_init = live.journal.current_capability(repository, "initialize")
    assert pinned.locations == (first_root.resolve(),)
    assert str(repository.resolve()) in pinned.exclusions
    assert pinned_init is not None and pinned_init.freshness is ResultFreshness.CURRENT
    assert live.locations == (first_root.resolve(), second_root.resolve())
    assert str(repository.resolve()) not in live.exclusions
    assert live_init is not None and live_init.freshness is ResultFreshness.STALE
    assert pinned.durable_revision < live.durable_revision
    assert pinned.journal.revision < live.journal.revision


def test_application_restart_fails_interrupted_fleet_run(tmp_path: Path) -> None:
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.vocabulary import FleetRunStatus, OperationAction

    database_path = tmp_path / "state.db"
    first = local_state(LocalDatabase(database_path))
    target = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(first.journal, "run-interrupted", OperationAction.INIT, [target])
    start_run(first.journal, "run-interrupted")

    restarted = local_state(LocalDatabase(database_path))
    effect_recovery(
        restarted.database,
        RepositoryEffectAttemptStore(restarted.database),
        restarted.journal,
        ExecutionCoordinator(restarted.database),
        GitService(),
    ).recover()
    detail = restarted.journal.read_view().run_detail("run-interrupted")

    assert detail is not None
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure is not None
    assert detail.run.failure.code == "RUN_INTERRUPTED_BY_RESTART"
    assert (
        detail.run.failure.message
        == "Application restarted before any repository operation durably completed"
    )
    assert restarted.journal.active_run() is None


def test_restart_does_not_fail_run_still_owned_by_live_execution_lease(tmp_path: Path) -> None:
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
    from oh_goon.runtime.execution.sqlite_lease import SQLiteExecutionLeaseStore

    database_path = tmp_path / "live-owner.db"
    database = LocalDatabase(database_path)
    first = local_state(database)
    leases = SQLiteExecutionLeaseStore(database)
    lease = leases.acquire_fleet_with(
        "run-live",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    target = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(first.journal, "run-live", OperationAction.INIT, [target])
    start_run(first.journal, "run-live")

    restarted = local_state(LocalDatabase(database_path))
    effect_recovery(
        restarted.database,
        RepositoryEffectAttemptStore(restarted.database),
        restarted.journal,
        ExecutionCoordinator(restarted.database),
        GitService(),
    ).recover()
    detail = restarted.journal.read_view().run_detail("run-live")

    assert detail is not None
    assert detail.run.status is FleetRunStatus.RUNNING
    leases.finish_as_owner(lease, lambda _connection: None)


def test_restart_finalizes_abandoned_cancellation_as_cancelled(tmp_path: Path) -> None:
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.vocabulary import FleetRunStatus, OperationAction

    database_path = tmp_path / "cancel-recovery.db"
    first = local_state(LocalDatabase(database_path))
    target = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(first.journal, "run-cancel", OperationAction.INIT, [target])
    start_run(first.journal, "run-cancel")
    with first.database.transaction(write=True) as connection:
        first.journal.request_cancel_in(connection, "run-cancel")

    restarted = local_state(LocalDatabase(database_path))
    effect_recovery(
        restarted.database,
        RepositoryEffectAttemptStore(restarted.database),
        restarted.journal,
        ExecutionCoordinator(restarted.database),
        GitService(),
    ).recover()
    detail = restarted.journal.read_view().run_detail("run-cancel")

    assert detail is not None
    assert detail.run.status is FleetRunStatus.CANCELLED
    assert restarted.journal.active_run() is None


def test_durable_revision_tracks_scope_writes_without_journal_writes(tmp_path: Path) -> None:
    state = local_state(LocalDatabase(tmp_path / "state-revision.db"))
    repository = tmp_path / "repo"
    repository.mkdir()

    initial = state.read_view()
    state.exclude(
        repository, "scope change", expected_durable_revision=state.read_view().durable_revision
    )
    changed = state.read_view()

    assert initial.journal.revision == changed.journal.revision == 0
    assert changed.durable_revision > initial.durable_revision


def test_exclusion_write_rejects_a_stale_managed_scope_revision(tmp_path: Path) -> None:
    from oh_goon.core.errors import RuntimeSafetyStale

    state = local_state(LocalDatabase(tmp_path / "scope-cas.db"))
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    repository = first_root / "api"
    repository.mkdir(parents=True)
    second_root.mkdir()
    state.add_location(first_root)
    observed_revision = state.read_view().durable_revision

    # A concurrent durable scope change invalidates the managed-repository proof.
    state.add_location(second_root)

    with pytest.raises(RuntimeSafetyStale, match="expected revision"):
        state.exclude(
            repository,
            "must not commit from stale scope",
            expected_durable_revision=observed_revision,
        )

    assert state.exclusions() == []


def test_state_read_view_uses_one_sqlite_snapshot_during_concurrent_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from threading import Event, Thread

    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.vocabulary import OperationAction, ResultFreshness, Verdict

    state = local_state(LocalDatabase(tmp_path / "concurrent-cut.db"))
    root = tmp_path / "repos"
    root.mkdir()
    repository = root / "api"
    repository.mkdir()
    identity = RepositoryIdentity(name="api", path=repository)
    state.add_location(root)
    commit_operation(
        state.journal,
        make_operation_report(
            identity, OperationAction.INIT, operation_id="op-init", fleet_run_id=None
        ),
    )

    original_locations = LocalState._locations
    errors: list[BaseException] = []
    writer_started = Event()
    writer: Thread | None = None

    def write_after_reader_started() -> None:
        writer_started.set()
        try:
            commit_operation(
                state.journal,
                make_operation_report(
                    identity,
                    OperationAction.UPDATE,
                    verdict=Verdict.UPDATED,
                    operation_id="op-update",
                    fleet_run_id=None,
                ),
            )
            state.exclude(
                repository,
                "committed after read transaction began",
                expected_durable_revision=state.database.durable_revision(),
            )
        except Exception as exc:  # pragma: no cover - surfaced by assertion below
            errors.append(exc)

    def locations_with_concurrent_write(connection):
        nonlocal writer
        result = original_locations(connection)
        writer = Thread(target=write_after_reader_started)
        writer.start()
        assert writer_started.wait(timeout=2)
        # Do not join here: rollback-DELETE correctly keeps the writer from committing
        # while this read transaction owns its snapshot. The writer must finish only
        # after read_view() closes the pinned transaction.
        return result

    monkeypatch.setattr(LocalState, "_locations", staticmethod(locations_with_concurrent_write))
    pinned = state.read_view()
    assert writer is not None
    writer.join(timeout=10)
    assert not writer.is_alive()
    monkeypatch.setattr(LocalState, "_locations", staticmethod(original_locations))
    assert not errors

    pinned_init = pinned.journal.current_capability(repository, "initialize")
    assert pinned_init is not None and pinned_init.freshness is ResultFreshness.CURRENT
    assert pinned.journal.current_capability(repository, "update") is None
    assert str(repository.resolve()) not in pinned.exclusions

    live = state.read_view()
    live_init = live.journal.current_capability(repository, "initialize")
    assert live_init is not None and live_init.freshness is ResultFreshness.STALE
    assert live.journal.current_capability(repository, "update") is not None
    assert str(repository.resolve()) in live.exclusions
    assert live.durable_revision > pinned.durable_revision


def test_location_projection_failure_does_not_publish_league_preferences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = LocalDatabase(tmp_path / "projection-failure.db")
    state = local_state(database)
    location = tmp_path / "repos"
    location.mkdir()

    def fail_projection(_connection, _folders) -> None:
        raise RuntimeError("simulated SQLite projection failure")

    monkeypatch.setattr(LocalState, "_replace_locations", staticmethod(fail_projection))

    with pytest.raises(RuntimeError, match="projection failure"):
        state.add_location(location)

    assert LeagueFolderStore(database.path.with_name("league.yml")).list() == ()
    assert state.read_view().locations == ()


def test_location_transaction_failure_restores_league_preferences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = LocalDatabase(tmp_path / "commit-failure.db")
    state = local_state(database)
    location = tmp_path / "repos"
    location.mkdir()
    original_transaction = database.transaction

    @contextmanager
    def fail_write_transaction(*, write: bool = False, track_revision: bool = True):
        with original_transaction(write=write, track_revision=track_revision) as connection:
            yield connection
            if write:
                raise RuntimeError("simulated transaction commit failure")

    monkeypatch.setattr(database, "transaction", fail_write_transaction)
    with pytest.raises(RuntimeError, match="commit failure"):
        state.add_location(location)
    monkeypatch.setattr(database, "transaction", original_transaction)

    assert LeagueFolderStore(database.path.with_name("league.yml")).list() == ()
    assert state.read_view().locations == ()


def test_location_ambiguous_committed_add_keeps_preference_and_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = LocalDatabase(tmp_path / "ambiguous-add.db")
    state = local_state(database)
    location = tmp_path / "repos"
    location.mkdir()
    original_transaction = database.transaction
    raised = False

    @contextmanager
    def lose_first_write_response(*, write: bool = False, track_revision: bool = True):
        nonlocal raised
        with original_transaction(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and not raised:
            raised = True
            raise RuntimeError("simulated transaction response loss after commit")

    monkeypatch.setattr(database, "transaction", lose_first_write_response)

    assert state.add_location(location) == [location.resolve()]
    assert LeagueFolderStore(database.path.with_name("league.yml")).list() == (location.resolve(),)
    assert state.read_view().locations == (location.resolve(),)


def test_location_ambiguous_committed_remove_keeps_preference_and_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = LocalDatabase(tmp_path / "ambiguous-remove.db")
    state = local_state(database)
    location = tmp_path / "repos"
    location.mkdir()
    state.add_location(location)
    original_transaction = database.transaction
    raised = False

    @contextmanager
    def lose_first_write_response(*, write: bool = False, track_revision: bool = True):
        nonlocal raised
        with original_transaction(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and not raised:
            raised = True
            raise RuntimeError("simulated transaction response loss after commit")

    monkeypatch.setattr(database, "transaction", lose_first_write_response)

    assert state.remove_location(location, expected_revision=locations_revision(state.read_view().locations)) == []
    assert LeagueFolderStore(database.path.with_name("league.yml")).list() == ()
    assert state.read_view().locations == ()


def test_reconcile_league_folders_replays_exact_projection_after_ambiguous_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "reconcile-ambiguous.db"
    initial = local_state(LocalDatabase(database_path))
    location = tmp_path / "repos"
    location.mkdir()
    initial.add_location(location)

    # Simulate a stale/disposable runtime projection that startup must repair.
    database = LocalDatabase(database_path)
    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM locations")

    state = local_state(database)
    original_transaction = database.transaction
    raised = False

    @contextmanager
    def lose_reconcile_response(*, write: bool = False, track_revision: bool = True):
        nonlocal raised
        with original_transaction(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and not raised:
            raised = True
            raise RuntimeError("simulated reconcile response loss after commit")

    monkeypatch.setattr(database, "transaction", lose_reconcile_response)

    assert state.reconcile_league_folders() == [location.resolve()]
    assert LeagueFolderStore(database.path.with_name("league.yml")).list() == (location.resolve(),)
    assert state.read_view().locations == (location.resolve(),)


def test_reconcile_league_folders_preserves_failure_when_projection_did_not_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "reconcile-precommit.db"
    initial = local_state(LocalDatabase(database_path))
    location = tmp_path / "repos"
    location.mkdir()
    initial.add_location(location)

    database = LocalDatabase(database_path)
    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM locations")

    state = local_state(database)
    original_transaction = database.transaction

    @contextmanager
    def fail_reconcile_before_commit(*, write: bool = False, track_revision: bool = True):
        if write:
            raise RuntimeError("simulated reconcile failure before commit")
        with original_transaction(write=write, track_revision=track_revision) as connection:
            yield connection

    monkeypatch.setattr(database, "transaction", fail_reconcile_before_commit)

    with pytest.raises(RuntimeError, match="before commit"):
        state.reconcile_league_folders()
    assert LeagueFolderStore(database.path.with_name("league.yml")).list() == (location.resolve(),)


def test_successful_location_write_reloads_to_same_preference_and_runtime_state(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "restart-parity.db"
    location = tmp_path / "repos"
    location.mkdir()
    first = local_state(LocalDatabase(database_path))

    first.add_location(location)
    assert LeagueFolderStore(database_path.with_name("league.yml")).list() == (location.resolve(),)
    assert first.read_view().locations == (location.resolve(),)

    restarted = local_state(LocalDatabase(database_path))
    restarted.reconcile_league_folders()
    assert LeagueFolderStore(database_path.with_name("league.yml")).list() == (location.resolve(),)
    assert restarted.read_view().locations == (location.resolve(),)

    restarted.remove_location(location, expected_revision=locations_revision(restarted.read_view().locations))
    assert LeagueFolderStore(database_path.with_name("league.yml")).list() == ()
    assert restarted.read_view().locations == ()

    reloaded = local_state(LocalDatabase(database_path))
    reloaded.reconcile_league_folders()
    assert LeagueFolderStore(database_path.with_name("league.yml")).list() == ()
    assert reloaded.read_view().locations == ()
