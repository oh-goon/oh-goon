from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import InvalidRunTransition
from oh_goon.core.execution_lifecycle_ledger import lifecycle_events
from oh_goon.core.execution_lifecycle_validation import validate_execution_lifecycle
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_events import ProcessExitedEvent, ProcessStartingEvent
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import start_repository, start_run, terminalize_run
from tests.factories.operation_reports import make_operation_report


class _StepScope(TypedDict):
    fleet_run_id: str
    repository_id: str
    operation_id: str
    step_id: str
    step_order: int


def _setup(tmp_path: Path) -> tuple[Journal, RepositoryIdentity, str, str]:
    journal = Journal(LocalDatabase(tmp_path / "events.db"))
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repository"})
    run_id = "generic-process"
    admit_test_run(journal, run_id, OperationAction.INIT, [repository])
    start_run(journal, run_id)
    return (
        journal,
        repository,
        run_id,
        repository_attempt_operation_id(run_id, repository.repository_id),
    )


def _record(
    journal: Journal,
    repository: RepositoryIdentity,
    run_id: str,
    operation_id: str,
    kind: FleetRunEventType,
    **fields: Any,
) -> None:
    journal.record_execution_lifecycle(
        fleet_run_id=run_id,
        repository_id=repository.repository_id,
        operation_id=operation_id,
        event_type=kind,
        physical_execution_id="native-command",
        **fields,
    )


def _start_process(
    journal: Journal, repository: RepositoryIdentity, run_id: str, operation_id: str
) -> None:
    _record(journal, repository, run_id, operation_id, FleetRunEventType.PROCESS_STARTING)
    _record(
        journal,
        repository,
        run_id,
        operation_id,
        FleetRunEventType.PROCESS_STARTED,
        pid=12345,
        process_identity="native-birth",
    )


def test_operation_process_preparation_has_real_scope_without_an_invented_step(
    tmp_path: Path,
) -> None:
    journal, repository, run_id, operation_id = _setup(tmp_path)
    _start_process(journal, repository, run_id, operation_id)
    _record(
        journal,
        repository,
        run_id,
        operation_id,
        FleetRunEventType.PROCESS_EXITED,
        pid=12345,
        process_identity="native-birth",
        returncode=0,
    )
    _record(
        journal,
        repository,
        run_id,
        operation_id,
        FleetRunEventType.DESCENDANTS_QUIESCENT,
        pid=12345,
        process_identity="native-birth",
    )
    # Repository effect entry is distinct from its native preparation.
    start_repository(journal, run_id, repository)
    detail = journal.read_view().required_run_detail(run_id)
    physical = [
        event for event in detail.lifecycle_events if isinstance(event, ProcessStartingEvent)
    ]
    assert len(physical) == 4
    assert all(
        event.step_id is None
        and event.step_order is None
        and event.attempt_number is None
        and event.step_attempt_id is None
        for event in physical
    )
    tape = detail.game_tape
    assert tape.repositories[0].steps == ()
    assert tape.repositories[0].runtime_progress is not None
    assert tape.repositories[0].runtime_progress.step_id is None
    observed = [event for event in lifecycle_events(detail) if event.physical_execution_id]
    assert len(observed) == 4 and all(event.step_id is None for event in observed)


@pytest.mark.parametrize(
    "field,value",
    (("step_id", "fake"), ("step_order", 1), ("attempt_number", 1), ("step_attempt_id", "fake")),
)
def test_partial_step_scope_is_rejected_at_the_process_model_boundary(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError, match="operation-only or one complete Step"):
        ProcessStartingEvent.model_validate(
            {
                "event_type": "PROCESS_STARTING",
                "sequence": 1,
                "fleet_run_id": "run",
                "repository_id": "repository",
                "operation_id": "operation",
                "physical_execution_id": "physical",
                field: value,
            }
        )


@pytest.mark.parametrize("scope", ("repository", "operation", "step-rebind"))
def test_operation_process_identity_cannot_escape_or_rebind_its_admitted_scope(
    tmp_path: Path, scope: str
) -> None:
    journal, repository, run_id, operation_id = _setup(tmp_path)
    _start_process(journal, repository, run_id, operation_id)
    detail = journal.read_view().required_run_detail(run_id)
    history = journal.read_view().run_events(run_id).events
    original = ProcessExitedEvent(
        sequence=5,
        fleet_run_id=run_id,
        repository_id=repository.repository_id,
        operation_id=operation_id,
        event_type=FleetRunEventType.PROCESS_EXITED,
        physical_execution_id="native-command",
        pid=12345,
        process_identity="native-birth",
        returncode=0,
    )
    validate_execution_lifecycle(detail.run, original, history)
    candidate: dict[str, Any] = {}
    if scope == "repository":
        candidate["repository_id"] = "foreign-repository"
    elif scope == "operation":
        candidate["operation_id"] = "foreign-operation"
    else:
        candidate.update(step_id="fake-step", step_order=1, attempt_number=1)
    with pytest.raises(InvalidRunTransition):
        validate_execution_lifecycle(detail.run, original.model_copy(update=candidate), history)
    assert len(journal.read_view().run_events(run_id).events) == 4


def test_native_quiescence_after_unknown_does_not_invent_exit_or_success(tmp_path: Path) -> None:
    journal, repository, run_id, operation_id = _setup(tmp_path)
    _start_process(journal, repository, run_id, operation_id)
    _record(
        journal,
        repository,
        run_id,
        operation_id,
        FleetRunEventType.PROCESS_STATE_UNKNOWN,
        pid=12345,
        process_identity="native-birth",
    )
    _record(
        journal,
        repository,
        run_id,
        operation_id,
        FleetRunEventType.DESCENDANTS_QUIESCENT,
        pid=12345,
        process_identity="native-birth",
    )
    with pytest.raises(
        InvalidRunTransition, match="Unknown physical execution cannot finalize output"
    ):
        _record(
            journal,
            repository,
            run_id,
            operation_id,
            FleetRunEventType.OUTPUT_FINALIZED,
            output_count=1,
        )
    start_repository(journal, run_id, repository)
    report = make_operation_report(
        repository.path, OperationAction.INIT, fleet_run_id=run_id, operation_id=operation_id
    )
    with pytest.raises(InvalidRunTransition, match="Unknown physical execution result"):
        with journal._database.transaction(write=True) as connection:
            journal.commit_operation_in(connection, report)
    events = journal.read_view().run_events(run_id).events
    assert not any(event.event_type is FleetRunEventType.PROCESS_EXITED for event in events)
    assert any(event.event_type is FleetRunEventType.PROCESS_STATE_UNKNOWN for event in events)


def test_unknown_step_result_cannot_become_success_after_resource_quiescence(
    tmp_path: Path,
) -> None:
    journal, repository, run_id, operation_id = _setup(tmp_path)
    start_repository(journal, run_id, repository)
    common = _StepScope(
        fleet_run_id=run_id,
        repository_id=repository.repository_id,
        operation_id=operation_id,
        step_id="actual-step",
        step_order=1,
    )
    for kind in (
        FleetRunEventType.STEP_REACHED,
        FleetRunEventType.STEP_STARTED,
        FleetRunEventType.STEP_ATTEMPT_CREATED,
    ):
        journal.record_execution_lifecycle(**common, event_type=kind, attempt_number=1)
    for kind in (
        FleetRunEventType.PROCESS_STARTING,
        FleetRunEventType.PROCESS_STARTED,
        FleetRunEventType.PROCESS_STATE_UNKNOWN,
        FleetRunEventType.DESCENDANTS_QUIESCENT,
    ):
        journal.record_execution_lifecycle(
            **common,
            event_type=kind,
            attempt_number=1,
            physical_execution_id="unknown-step",
            pid=12345,
            process_identity="native-birth",
        )
    with pytest.raises(InvalidRunTransition, match="Unknown physical execution result"):
        journal.record_execution_lifecycle(
            **common, event_type=FleetRunEventType.STEP_SUCCEEDED, returncode=0
        )


def test_prior_settled_report_is_not_downgraded_by_missing_observation_tail(tmp_path: Path) -> None:
    journal, repository, run_id, operation_id = _setup(tmp_path)
    _start_process(journal, repository, run_id, operation_id)
    start_repository(journal, run_id, repository)
    report = make_operation_report(
        repository.path, OperationAction.INIT, fleet_run_id=run_id, operation_id=operation_id
    )
    assert report.resource_settlement == "SETTLED"
    with journal._database.transaction(write=True) as connection:
        journal.commit_operation_in(connection, report)
    terminalize_run(journal, run_id, FleetRunTerminalIntent.COMPLETE)
    assert journal.read_view().required_run_detail(run_id).run.status is FleetRunStatus.COMPLETED
