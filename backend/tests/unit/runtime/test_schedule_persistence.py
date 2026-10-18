from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from oh_goon.core.errors import ExecutionBusy, ScheduleDefinitionStale
from oh_goon.core.models.schedule import ScheduleDeleteRequest, ScheduleFailure, ScheduleReplace
from oh_goon.core.models.schedule_execution import FailedScheduleRun
from oh_goon.core.schedule_transitions import fail_schedule_run
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import ScheduleFailureStage
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from tests.factories.schedules import persist_active_schedule, schedule_create


def _lose_next_write_commit_response(database: LocalDatabase) -> None:
    original = database.transaction
    armed = True

    @contextmanager
    def ambiguous_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal armed
        with original(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and armed:
            armed = False
            raise RuntimeError("Schedule definition commit response lost after commit")

    database.transaction = ambiguous_transaction  # type: ignore[method-assign]


def _fail_next_write_before_commit(database: LocalDatabase) -> None:
    original = database.transaction
    armed = True

    @contextmanager
    def failing_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal armed
        if write and armed:
            armed = False
            raise RuntimeError("Schedule definition write failed before commit")
        with original(write=write, track_revision=track_revision) as connection:
            yield connection

    database.transaction = failing_transaction  # type: ignore[method-assign]


def _replace(definition, *, name: str) -> ScheduleReplace:
    return ScheduleReplace(
        name=name,
        repository_ids=definition.repository_ids,
        repository_instance_ids=definition.repository_instance_ids,
        target=definition.target,
        interval_minutes=definition.interval_minutes,
        state=definition.state,
        generation_id=definition.generation_id,
        record_version=definition.record_version,
    )


def test_schedule_failure_round_trips_as_structured_fact(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "schedule.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create("schedule-health", schedule_create(name="Health"))
    execution = persist_active_schedule(database, executions, definition)
    failure = ScheduleFailure.from_exception(
        ExecutionBusy("busy"),
        stage=ScheduleFailureStage.ADMISSION,
    )
    executions.compare_and_set(
        execution,
        fail_schedule_run(execution, failure, finished_at=utc_now()),
    )

    stored = executions.executions(definition.schedule_id, limit=1)[0]
    assert isinstance(stored, FailedScheduleRun)
    assert stored.failure.code == "EXECUTION_BUSY"
    assert stored.failure.retryable is True
    assert stored.failure.stage is ScheduleFailureStage.ADMISSION


def test_schedule_definition_replace_rejects_stale_writer(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "schedule-stale.db")
    definitions = ScheduleDefinitionStore(database)
    original = definitions.create("schedule-health", schedule_create(name="Health"))

    first = definitions.replace(original.schedule_id, _replace(original, name="Health v2"))

    try:
        definitions.replace(original.schedule_id, _replace(original, name="Stale update"))
    except ScheduleDefinitionStale:
        pass
    else:
        raise AssertionError("stale Schedule definition update must fail")

    stored = definitions.get(original.schedule_id)
    assert stored.name == "Health v2"
    assert stored.record_version == first.record_version == original.record_version + 1


def test_schedule_definition_replace_replays_same_committed_command(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "schedule-replace-replay.db")
    definitions = ScheduleDefinitionStore(database)
    original = definitions.create("schedule-health", schedule_create(name="Health"))
    request = _replace(original, name="Health v2")

    first = definitions.replace(original.schedule_id, request)
    replay = definitions.replace(original.schedule_id, request)

    assert replay == first
    assert replay.record_version == original.record_version + 1


def test_schedule_definition_delete_is_version_bound_replay_safe_and_aba_safe(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "schedule-delete-replay.db")
    definitions = ScheduleDefinitionStore(database)
    original = definitions.create("schedule-health", schedule_create(name="Health"))
    request = ScheduleDeleteRequest(
        generation_id=original.generation_id,
        record_version=original.record_version,
    )

    definitions.delete(original.schedule_id, request)
    definitions.delete(original.schedule_id, request)

    recreated = definitions.create(original.schedule_id, schedule_create(name="Health"))
    assert recreated.generation_id != original.generation_id

    try:
        definitions.delete(original.schedule_id, request)
    except ScheduleDefinitionStale:
        pass
    else:
        raise AssertionError("an old delete command must not delete a recreated Schedule")


def test_schedule_definition_delete_rejects_stale_version(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "schedule-delete-stale.db")
    definitions = ScheduleDefinitionStore(database)
    original = definitions.create("schedule-health", schedule_create(name="Health"))
    updated = definitions.replace(original.schedule_id, _replace(original, name="Health v2"))

    try:
        definitions.delete(
            original.schedule_id,
            ScheduleDeleteRequest(
                generation_id=original.generation_id,
                record_version=original.record_version,
            ),
        )
    except ScheduleDefinitionStale:
        pass
    else:
        raise AssertionError("stale Schedule delete must fail")

    assert definitions.get(original.schedule_id) == updated


def test_schedule_definition_create_replays_after_ambiguous_commit_response(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "schedule-create-ambiguous.db")
    definitions = ScheduleDefinitionStore(database)
    request = schedule_create(name="Health")
    _lose_next_write_commit_response(database)

    created = definitions.create("schedule-health", request)

    assert definitions.get(created.schedule_id) == created
    assert created.name == "Health"


def test_schedule_definition_replace_replays_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "schedule-replace-ambiguous.db")
    definitions = ScheduleDefinitionStore(database)
    original = definitions.create("schedule-health", schedule_create(name="Health"))
    request = _replace(original, name="Health v2")
    _lose_next_write_commit_response(database)

    replaced = definitions.replace(original.schedule_id, request)

    assert replaced == definitions.get(original.schedule_id)
    assert replaced.record_version == original.record_version + 1
    assert replaced.name == "Health v2"


def test_schedule_definition_delete_replays_after_ambiguous_commit_response(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "schedule-delete-ambiguous.db")
    definitions = ScheduleDefinitionStore(database)
    original = definitions.create("schedule-health", schedule_create(name="Health"))
    request = ScheduleDeleteRequest(
        generation_id=original.generation_id,
        record_version=original.record_version,
    )
    _lose_next_write_commit_response(database)

    definitions.delete(original.schedule_id, request)

    assert definitions.definitions() == []


def test_schedule_definition_replace_preserves_precommit_failure_without_canonical_replay(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "schedule-replace-precommit.db")
    definitions = ScheduleDefinitionStore(database)
    original = definitions.create("schedule-health", schedule_create(name="Health"))
    request = _replace(original, name="Health v2")
    _fail_next_write_before_commit(database)

    try:
        definitions.replace(original.schedule_id, request)
    except RuntimeError as exc:
        assert "before commit" in str(exc)
    else:
        raise AssertionError("pre-commit Schedule definition failure must remain visible")

    assert definitions.get(original.schedule_id) == original


def test_schedule_definition_restart_preserves_exact_verified_execution_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "schedule-restart-identity.db"
    first_database = LocalDatabase(path)
    first_store = ScheduleDefinitionStore(first_database)
    request = schedule_create(name="Restart identity")
    created = first_store.create("schedule-restart-identity", request)

    reopened = ScheduleDefinitionStore(LocalDatabase(path)).get(created.schedule_id)

    assert reopened.target == created.target
    assert reopened.target.identity == created.target.identity
    assert reopened.target.execution_digest == created.target.execution_digest
