from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import ErrorKind
from oh_goon.core.product_identity import schedule_repository_fleet_run_id
from oh_goon.core.models.schedule import ScheduleFailure
from oh_goon.core.models.schedule_execution import (
    AttentionRequiredScheduleRun,
    CompletedScheduleRun,
    RunningScheduleRun,
    parse_schedule_run,
)
from oh_goon.core.schedule_transitions import begin_schedule_run, fail_schedule_run
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    ScheduleExecutionStatus,
    ScheduleFailureStage,
    ScheduleRepositoryRunStatus,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from tests.factories.schedules import (
    persist_active_schedule,
    schedule_create,
    scheduled_fleet_run_intent,
)


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
            raise RuntimeError("Schedule commit response lost after commit")

    database.transaction = ambiguous_transaction  # type: ignore[method-assign]


def _fail_next_write_before_commit(database: LocalDatabase) -> None:
    original = database.transaction
    armed = True

    @contextmanager
    def failing_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal armed
        if write and armed:
            armed = False
            raise RuntimeError("Schedule write failed before commit")
        with original(write=write, track_revision=track_revision) as connection:
            yield connection

    database.transaction = failing_transaction  # type: ignore[method-assign]


def _execution(tmp_path: Path):
    database = LocalDatabase(tmp_path / "schedule-lifecycle.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create("schedule-lifecycle", schedule_create(name="Lifecycle"))
    execution = persist_active_schedule(database, executions, definition)
    return database, executions, execution


def test_schedule_state_variants_make_terminal_and_failure_shapes_structural(
    tmp_path: Path,
) -> None:
    _, _, execution = _execution(tmp_path)
    terminal_without_finish = execution.model_dump(mode="python", exclude_computed_fields=True)
    terminal_without_finish["status"] = "COMPLETED"
    with pytest.raises(ValidationError, match="finished_at"):
        parse_schedule_run(terminal_without_finish)

    failed_without_failure = execution.model_dump(mode="python", exclude_computed_fields=True)
    failed_without_failure.update(
        status="FAILED",
        finished_at=execution.started_at,
        repository_runs=[
            {
                "repository_id": item.repository_id,
                "status": "NOT_STARTED",
                "failure": {
                    "code": "NO_START",
                    "kind": "INTERNAL",
                    "stage": "ADMISSION",
                    "message": "did not start",
                },
            }
            for item in execution.repository_runs
        ],
    )
    with pytest.raises(ValidationError, match="failure"):
        parse_schedule_run(failed_without_failure)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CompletedScheduleRun(
            status=ScheduleExecutionStatus.COMPLETED,
            schedule_run_id=execution.schedule_run_id,
            schedule_id=execution.schedule_id,
            intent=execution.intent,
            repository_runs=tuple(
                item.model_copy(
                    update={
                        "status": ScheduleRepositoryRunStatus.SUCCESSFUL,
                        "fleet_run_id": schedule_repository_fleet_run_id(
                            execution.schedule_run_id, item.repository_id
                        ),
                    }
                )
                for item in execution.repository_runs
            ),
            started_at=execution.started_at,
            finished_at=execution.started_at,
            failure={
                "code": "NOPE",
                "kind": "INTERNAL",
                "stage": "ADVANCE",
                "message": "not legal on completed state",
            },
        )


def test_schedule_repository_fleet_run_identity_is_derived_from_occurrence_and_repository(
    tmp_path: Path,
) -> None:
    _, _, execution = _execution(tmp_path)
    assert schedule_repository_fleet_run_id(
        execution.schedule_run_id, execution.intent.repository_scope.repository_ids[0]
    ).startswith("fleet-")
    payload = execution.model_dump(mode="python", exclude_computed_fields=True)
    assert "step_index" not in payload
    assert "steps" not in payload


def test_sqlite_rejects_status_timestamp_disagreement_even_without_model_validation(
    tmp_path: Path,
) -> None:
    database, _, execution = _execution(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE schedule_executions SET status='COMPLETED' WHERE schedule_run_id=?",
                (execution.schedule_run_id,),
            )


def test_schedule_attention_required_variant_requires_exact_attention_evidence(
    tmp_path: Path,
) -> None:
    _, _, execution = _execution(tmp_path)
    with pytest.raises(ValidationError, match="need exact attention evidence"):
        AttentionRequiredScheduleRun(
            status=ScheduleExecutionStatus.ATTENTION_REQUIRED,
            schedule_run_id=execution.schedule_run_id,
            schedule_id=execution.schedule_id,
            intent=execution.intent,
            repository_runs=tuple(
                item.model_copy(
                    update={
                        "status": ScheduleRepositoryRunStatus.ATTENTION_REQUIRED,
                        "fleet_run_id": schedule_repository_fleet_run_id(
                            execution.schedule_run_id, item.repository_id
                        ),
                        "attention_count": 1,
                    }
                )
                for item in execution.repository_runs
            ),
            attention=(),
            record_version=execution.record_version,
            started_at=execution.started_at,
            finished_at=utc_now(),
        )


def test_sqlite_rejects_attention_status_without_attention_evidence(tmp_path: Path) -> None:
    database, _, execution = _execution(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE schedule_executions SET status='ATTENTION_REQUIRED', finished_at=? "
                "WHERE schedule_run_id=?",
                (execution.started_at.isoformat(), execution.schedule_run_id),
            )


def test_schedule_parser_returns_the_single_active_state_variant(tmp_path: Path) -> None:
    _, _, running = _execution(tmp_path)
    reparsed = parse_schedule_run(running.model_dump(mode="python", exclude_computed_fields=True))
    assert isinstance(reparsed, RunningScheduleRun)


def test_schedule_insert_replays_exact_execution_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "schedule-insert-ambiguous.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create(
        "schedule-insert-ambiguous", schedule_create(name="Insert ambiguity")
    )
    execution = begin_schedule_run(
        schedule_run_id="schedule-run-insert-ambiguous",
        schedule_id=definition.schedule_id,
        intent=scheduled_fleet_run_intent(definition),
        started_at=utc_now(),
    )
    expected_revision = database.durable_revision()
    _lose_next_write_commit_response(database)

    saved = executions.insert(execution, expected_durable_revision=expected_revision)

    assert saved == execution
    assert executions.get(execution.schedule_run_id) == execution


def test_schedule_compare_and_set_replays_exact_transition_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database, executions, execution = _execution(tmp_path)
    failure = ScheduleFailure(
        code="AMBIGUOUS_COMMIT",
        kind=ErrorKind.INTERNAL,
        retryable=False,
        stage=ScheduleFailureStage.ADVANCE,
        message="exact Schedule transition",
    )
    intended = fail_schedule_run(execution, failure, finished_at=utc_now())
    _lose_next_write_commit_response(database)

    saved = executions.compare_and_set(execution, intended)

    assert saved.record_version == execution.record_version + 1
    assert executions.get(execution.schedule_run_id) == saved


def test_schedule_compare_and_set_preserves_precommit_failure_without_exact_replay(
    tmp_path: Path,
) -> None:
    database, executions, execution = _execution(tmp_path)
    failure = ScheduleFailure(
        code="PRECOMMIT_FAILURE",
        kind=ErrorKind.INTERNAL,
        retryable=False,
        stage=ScheduleFailureStage.ADVANCE,
        message="must not be swallowed",
    )
    intended = fail_schedule_run(execution, failure, finished_at=utc_now())
    _fail_next_write_before_commit(database)

    with pytest.raises(RuntimeError, match="failed before commit"):
        executions.compare_and_set(execution, intended)

    assert executions.get(execution.schedule_run_id) == execution
