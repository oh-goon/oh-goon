from pathlib import Path

import pytest

from oh_goon.core.errors import ErrorKind, InvalidRunTransition
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.models.run_events import (
    RepositoryStartedEvent,
    RunAdmittedEvent,
    RunCancelledEvent,
    RunCancelRequestedEvent,
    RunFailedEvent,
    RunStartedEvent,
)
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.run_projection import project_fleet_run_detail
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunFailureStage,
    FleetRunStatus,
    OperationAction,
)
from tests.factories.capabilities import capability_snapshot


def _admitted(run_id: str, identities: list[RepositoryIdentity], *, generated_at):
    return RunAdmittedEvent(
        event_type=FleetRunEventType.RUN_ADMITTED,
        sequence=1,
        fleet_run_id=run_id,
        capability=capability_snapshot(OperationAction.INIT),
        repository_scope=fleet_repository_scope(None),
        targets=identities,
        execution=DirectFleetExecutionAdmission(),
        generated_at=generated_at,
        repository_admissions=(),
    )


def test_step_terminal_rejects_started_process_before_descendants_quiesce(tmp_path: Path) -> None:
    from oh_goon.core.models.run_events import (
        ProcessStartedEvent,
        ProcessStartingEvent,
        StepAttemptCreatedEvent,
        StepReachedEvent,
        StepStartedEvent,
        StepSucceededEvent,
    )
    from oh_goon.core.product_identity import fleet_step_attempt_id

    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    run_id = "fleet-process-quiescence"
    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    step_attempt_id = fleet_step_attempt_id(operation_id, "step-1", 1)
    now = utc_now()
    common = dict(
        fleet_run_id=run_id,
        repository_id=identity.repository_id,
        operation_id=operation_id,
        step_id="step-1",
        step_order=1,
        generated_at=now,
    )
    events = [
        _admitted(run_id, [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id=run_id,
            generated_at=now,
        ),
        RepositoryStartedEvent(
            event_type=FleetRunEventType.REPOSITORY_STARTED,
            sequence=3,
            fleet_run_id=run_id,
            repository_id=identity.repository_id,
            operation_id=operation_id,
            generated_at=now,
        ),
        StepReachedEvent(event_type=FleetRunEventType.STEP_REACHED, sequence=4, **common),
        StepStartedEvent(event_type=FleetRunEventType.STEP_STARTED, sequence=5, **common),
        StepAttemptCreatedEvent(
            event_type=FleetRunEventType.STEP_ATTEMPT_CREATED,
            sequence=6,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            **common,
        ),
        ProcessStartingEvent(
            event_type=FleetRunEventType.PROCESS_STARTING,
            sequence=7,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            **common,
        ),
        ProcessStartedEvent(
            event_type=FleetRunEventType.PROCESS_STARTED,
            sequence=8,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            pid=123,
            process_identity="pid-123",
            **common,
        ),
        StepSucceededEvent(
            event_type=FleetRunEventType.STEP_SUCCEEDED, sequence=9, returncode=0, **common
        ),
    ]

    with pytest.raises(InvalidRunTransition, match="DESCENDANTS_QUIESCENT"):
        project_fleet_run_detail(events, lambda _: None)


def test_run_cancelled_requires_canonical_cancel_request(tmp_path: Path) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    now = utc_now()
    events = [
        _admitted("fleet-cancel-no-request", [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id="fleet-cancel-no-request",
            generated_at=now,
        ),
        RunCancelledEvent(
            event_type=FleetRunEventType.RUN_CANCELLED,
            sequence=3,
            fleet_run_id="fleet-cancel-no-request",
            generated_at=now,
        ),
    ]
    with pytest.raises(InvalidRunTransition):
        project_fleet_run_detail(events, lambda _: None)


def test_run_cancelled_rejects_live_owned_process_before_descendants_quiesce(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.run_events import (
        ProcessStartedEvent,
        ProcessStartingEvent,
        StepAttemptCreatedEvent,
        StepReachedEvent,
        StepStartedEvent,
    )
    from oh_goon.core.product_identity import fleet_step_attempt_id

    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    run_id = "fleet-cancel-live-process"
    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    step_attempt_id = fleet_step_attempt_id(operation_id, "step-1", 1)
    now = utc_now()
    common = dict(
        fleet_run_id=run_id,
        repository_id=identity.repository_id,
        operation_id=operation_id,
        step_id="step-1",
        step_order=1,
        generated_at=now,
    )
    events = [
        _admitted(run_id, [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id=run_id,
            generated_at=now,
        ),
        RepositoryStartedEvent(
            event_type=FleetRunEventType.REPOSITORY_STARTED,
            sequence=3,
            fleet_run_id=run_id,
            repository_id=identity.repository_id,
            operation_id=operation_id,
            generated_at=now,
        ),
        StepReachedEvent(event_type=FleetRunEventType.STEP_REACHED, sequence=4, **common),
        StepStartedEvent(event_type=FleetRunEventType.STEP_STARTED, sequence=5, **common),
        StepAttemptCreatedEvent(
            event_type=FleetRunEventType.STEP_ATTEMPT_CREATED,
            sequence=6,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            **common,
        ),
        ProcessStartingEvent(
            event_type=FleetRunEventType.PROCESS_STARTING,
            sequence=7,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            **common,
        ),
        ProcessStartedEvent(
            event_type=FleetRunEventType.PROCESS_STARTED,
            sequence=8,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            pid=123,
            process_identity="pid-123",
            **common,
        ),
        RunCancelRequestedEvent(
            event_type=FleetRunEventType.RUN_CANCEL_REQUESTED,
            sequence=9,
            fleet_run_id=run_id,
            generated_at=now,
        ),
        RunCancelledEvent(
            event_type=FleetRunEventType.RUN_CANCELLED,
            sequence=10,
            fleet_run_id=run_id,
            generated_at=now,
        ),
    ]

    with pytest.raises(InvalidRunTransition, match="DESCENDANTS_QUIESCENT"):
        project_fleet_run_detail(events, lambda _: None)


@pytest.mark.parametrize(
    "operation_ids",
    [
        ("foreign-operation",),
        ("foreign-operation", "foreign-operation"),
    ],
)
def test_reconciliation_rejects_foreign_or_repeated_operation_authority(
    tmp_path: Path, operation_ids: tuple[str, ...]
) -> None:
    from oh_goon.core.models.run_events import ReconciliationRequiredEvent

    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    run_id = "recovery-lineage"
    now = utc_now()
    events = [
        _admitted(run_id, [identity], generated_at=now),
        ReconciliationRequiredEvent(
            event_type=FleetRunEventType.RECONCILIATION_REQUIRED,
            sequence=2,
            fleet_run_id=run_id,
            generated_at=now,
            reason="controller restart",
            operation_ids=operation_ids,
        ),
    ]
    with pytest.raises(InvalidRunTransition, match="operation identities"):
        project_fleet_run_detail(events, lambda _: None)


def test_run_failed_requires_quiescent_or_explicitly_unknown_started_process(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.run_events import (
        ProcessStartedEvent,
        ProcessStartingEvent,
        ProcessStateUnknownEvent,
        StepAttemptCreatedEvent,
        StepReachedEvent,
        StepStartedEvent,
    )
    from oh_goon.core.product_identity import fleet_step_attempt_id

    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    run_id = "fleet-fail-live-process"
    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    step_attempt_id = fleet_step_attempt_id(operation_id, "step-1", 1)
    now = utc_now()
    common = dict(
        fleet_run_id=run_id,
        repository_id=identity.repository_id,
        operation_id=operation_id,
        step_id="step-1",
        step_order=1,
        generated_at=now,
    )
    prefix = [
        _admitted(run_id, [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id=run_id,
            generated_at=now,
        ),
        RepositoryStartedEvent(
            event_type=FleetRunEventType.REPOSITORY_STARTED,
            sequence=3,
            fleet_run_id=run_id,
            repository_id=identity.repository_id,
            operation_id=operation_id,
            generated_at=now,
        ),
        StepReachedEvent(event_type=FleetRunEventType.STEP_REACHED, sequence=4, **common),
        StepStartedEvent(event_type=FleetRunEventType.STEP_STARTED, sequence=5, **common),
        StepAttemptCreatedEvent(
            event_type=FleetRunEventType.STEP_ATTEMPT_CREATED,
            sequence=6,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            **common,
        ),
        ProcessStartingEvent(
            event_type=FleetRunEventType.PROCESS_STARTING,
            sequence=7,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            **common,
        ),
        ProcessStartedEvent(
            event_type=FleetRunEventType.PROCESS_STARTED,
            sequence=8,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            pid=123,
            process_identity="pid-123",
            **common,
        ),
    ]
    failure = FleetRunFailure(
        code="TEST_FAILURE",
        kind=ErrorKind.INTERNAL,
        stage=FleetRunFailureStage.EXECUTION,
        message="boom",
    )
    terminal = RunFailedEvent(
        event_type=FleetRunEventType.RUN_FAILED,
        sequence=9,
        fleet_run_id=run_id,
        failure=failure,
        generated_at=now,
    )
    with pytest.raises(InvalidRunTransition, match="neither quiescent nor explicitly"):
        project_fleet_run_detail([*prefix, terminal], lambda _: None)

    unknown = ProcessStateUnknownEvent(
        event_type=FleetRunEventType.PROCESS_STATE_UNKNOWN,
        sequence=9,
        attempt_number=1,
        step_attempt_id=step_attempt_id,
        physical_execution_id="physical-1",
        pid=123,
        process_identity="pid-123",
        reason="controller restart lost supervision",
        **common,
    )
    settled = terminal.model_copy(update={"sequence": 10})
    detail = project_fleet_run_detail([*prefix, unknown, settled], lambda _: None)
    assert detail.run.status is FleetRunStatus.FAILED


def test_output_finalization_requires_quiescent_known_physical_execution(tmp_path: Path) -> None:
    from oh_goon.core.models.run_events import (
        OutputFinalizedEvent,
        ProcessStartedEvent,
        ProcessStartingEvent,
        ProcessStateUnknownEvent,
        StepAttemptCreatedEvent,
        StepReachedEvent,
        StepStartedEvent,
    )
    from oh_goon.core.product_identity import fleet_step_attempt_id

    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    run_id = "fleet-output-finalization"
    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    step_attempt_id = fleet_step_attempt_id(operation_id, "step-1", 1)
    now = utc_now()
    common = dict(
        fleet_run_id=run_id,
        repository_id=identity.repository_id,
        operation_id=operation_id,
        step_id="step-1",
        step_order=1,
        generated_at=now,
    )
    prefix = [
        _admitted(run_id, [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id=run_id,
            generated_at=now,
        ),
        RepositoryStartedEvent(
            event_type=FleetRunEventType.REPOSITORY_STARTED,
            sequence=3,
            fleet_run_id=run_id,
            repository_id=identity.repository_id,
            operation_id=operation_id,
            generated_at=now,
        ),
        StepReachedEvent(event_type=FleetRunEventType.STEP_REACHED, sequence=4, **common),
        StepStartedEvent(event_type=FleetRunEventType.STEP_STARTED, sequence=5, **common),
        StepAttemptCreatedEvent(
            event_type=FleetRunEventType.STEP_ATTEMPT_CREATED,
            sequence=6,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            **common,
        ),
        ProcessStartingEvent(
            event_type=FleetRunEventType.PROCESS_STARTING,
            sequence=7,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            **common,
        ),
        ProcessStartedEvent(
            event_type=FleetRunEventType.PROCESS_STARTED,
            sequence=8,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            pid=123,
            process_identity="pid-123",
            **common,
        ),
    ]
    finalized = OutputFinalizedEvent(
        event_type=FleetRunEventType.OUTPUT_FINALIZED,
        sequence=9,
        attempt_number=1,
        step_attempt_id=step_attempt_id,
        physical_execution_id="physical-1",
        output_count=1,
        **common,
    )

    with pytest.raises(InvalidRunTransition, match="DESCENDANTS_QUIESCENT"):
        project_fleet_run_detail([*prefix, finalized], lambda _: None)

    unknown = ProcessStateUnknownEvent(
        event_type=FleetRunEventType.PROCESS_STATE_UNKNOWN,
        sequence=9,
        attempt_number=1,
        step_attempt_id=step_attempt_id,
        physical_execution_id="physical-1",
        pid=123,
        process_identity="pid-123",
        reason="restart ambiguity",
        **common,
    )
    finalized_after_unknown = finalized.model_copy(update={"sequence": 10})
    with pytest.raises(InvalidRunTransition, match="Unknown physical execution"):
        project_fleet_run_detail([*prefix, unknown, finalized_after_unknown], lambda _: None)


def test_output_disposition_distinguishes_abandoned_from_unknown_quarantine(tmp_path: Path) -> None:
    from oh_goon.core.models.run_events import (
        DescendantsQuiescentEvent,
        OutputAbandonedEvent,
        OutputQuarantinedEvent,
        ProcessExitedEvent,
        ProcessStartedEvent,
        ProcessStartingEvent,
        ProcessStateUnknownEvent,
        StepAttemptCreatedEvent,
        StepReachedEvent,
        StepStartedEvent,
    )
    from oh_goon.core.product_identity import fleet_step_attempt_id

    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    run_id = "fleet-output-disposition"
    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    step_attempt_id = fleet_step_attempt_id(operation_id, "step-1", 1)
    now = utc_now()
    common = dict(
        fleet_run_id=run_id,
        repository_id=identity.repository_id,
        operation_id=operation_id,
        step_id="step-1",
        step_order=1,
        generated_at=now,
    )
    prefix = [
        _admitted(run_id, [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id=run_id,
            generated_at=now,
        ),
        RepositoryStartedEvent(
            event_type=FleetRunEventType.REPOSITORY_STARTED,
            sequence=3,
            fleet_run_id=run_id,
            repository_id=identity.repository_id,
            operation_id=operation_id,
            generated_at=now,
        ),
        StepReachedEvent(event_type=FleetRunEventType.STEP_REACHED, sequence=4, **common),
        StepStartedEvent(event_type=FleetRunEventType.STEP_STARTED, sequence=5, **common),
        StepAttemptCreatedEvent(
            event_type=FleetRunEventType.STEP_ATTEMPT_CREATED,
            sequence=6,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            **common,
        ),
        ProcessStartingEvent(
            event_type=FleetRunEventType.PROCESS_STARTING,
            sequence=7,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            **common,
        ),
        ProcessStartedEvent(
            event_type=FleetRunEventType.PROCESS_STARTED,
            sequence=8,
            attempt_number=1,
            step_attempt_id=step_attempt_id,
            physical_execution_id="physical-1",
            pid=123,
            process_identity="pid-123",
            **common,
        ),
    ]
    abandoned = OutputAbandonedEvent(
        event_type=FleetRunEventType.OUTPUT_ABANDONED,
        sequence=9,
        attempt_number=1,
        step_attempt_id=step_attempt_id,
        physical_execution_id="physical-1",
        reason="attempt failed",
        **common,
    )
    with pytest.raises(InvalidRunTransition, match="DESCENDANTS_QUIESCENT"):
        project_fleet_run_detail([*prefix, abandoned], lambda _: None)

    exited = ProcessExitedEvent(
        event_type=FleetRunEventType.PROCESS_EXITED,
        sequence=9,
        attempt_number=1,
        step_attempt_id=step_attempt_id,
        physical_execution_id="physical-1",
        pid=123,
        process_identity="pid-123",
        returncode=1,
        **common,
    )
    quiescent = DescendantsQuiescentEvent(
        event_type=FleetRunEventType.DESCENDANTS_QUIESCENT,
        sequence=10,
        attempt_number=1,
        step_attempt_id=step_attempt_id,
        physical_execution_id="physical-1",
        pid=123,
        process_identity="pid-123",
        **common,
    )
    project_fleet_run_detail(
        [*prefix, exited, quiescent, abandoned.model_copy(update={"sequence": 11})], lambda _: None
    )

    unknown = ProcessStateUnknownEvent(
        event_type=FleetRunEventType.PROCESS_STATE_UNKNOWN,
        sequence=9,
        attempt_number=1,
        step_attempt_id=step_attempt_id,
        physical_execution_id="physical-1",
        pid=123,
        process_identity="pid-123",
        reason="restart ambiguity",
        **common,
    )
    quarantined = OutputQuarantinedEvent(
        event_type=FleetRunEventType.OUTPUT_QUARANTINED,
        sequence=10,
        attempt_number=1,
        step_attempt_id=step_attempt_id,
        physical_execution_id="physical-1",
        reason="cannot trust output",
        **common,
    )
    project_fleet_run_detail([*prefix, unknown, quarantined], lambda _: None)
    with pytest.raises(InvalidRunTransition, match="quarantined, not abandoned"):
        project_fleet_run_detail(
            [*prefix, unknown, abandoned.model_copy(update={"sequence": 10})], lambda _: None
        )
