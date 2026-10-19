from __future__ import annotations

from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    FleetRunEvent,
    OutputAbandonedEvent,
    OutputFinalizedEvent,
    OutputQuarantinedEvent,
    ProcessExitedEvent,
    ProcessStartedEvent,
    ProcessStartingEvent,
    ProcessStateUnknownEvent,
    RepositoryStartedEvent,
    StepAttemptCreatedEvent,
    StepCancelledEvent,
    StepFailedEvent,
    StepReachedEvent,
    StepSkippedEvent,
    StepStartedEvent,
    StepSucceededEvent,
    TerminationRequestedEvent,
)
from oh_goon.core.product_identity import fleet_step_attempt_id
from oh_goon.core.vocabulary import FleetRunEventType


def _execution_lifecycle_event(
    event_type,
    common,
    step_id,
    step_order,
    attempt_number,
    physical_execution_id,
    pid,
    process_identity,
    returncode,
    termination_reason=None,
    output_count=None,
) -> FleetRunEvent:
    if event_type is FleetRunEventType.REPOSITORY_STARTED:
        return RepositoryStartedEvent(event_type=event_type, **common)
    step_common = {**common, "step_id": step_id or "", "step_order": step_order or 0}
    step_event = _step_event(event_type, step_common, attempt_number, returncode)
    if step_event is not None:
        return step_event
    return _physical_event(
        event_type,
        common,
        step_common,
        attempt_number,
        physical_execution_id,
        pid,
        process_identity,
        returncode,
        termination_reason,
        output_count,
    )


def _step_event(event_type, common, attempt_number, returncode):
    if event_type is FleetRunEventType.STEP_REACHED:
        return StepReachedEvent(event_type=event_type, **common)
    if event_type is FleetRunEventType.STEP_STARTED:
        return StepStartedEvent(event_type=event_type, **common)
    if event_type is FleetRunEventType.STEP_ATTEMPT_CREATED:
        if attempt_number is None:
            raise ValueError("STEP_ATTEMPT_CREATED requires attempt_number")
        return StepAttemptCreatedEvent(
            event_type=event_type,
            attempt_number=attempt_number,
            step_attempt_id=fleet_step_attempt_id(
                common["operation_id"], common["step_id"], attempt_number
            ),
            **common,
        )
    if event_type in {
        FleetRunEventType.STEP_SUCCEEDED,
        FleetRunEventType.STEP_FAILED,
        FleetRunEventType.STEP_SKIPPED,
        FleetRunEventType.STEP_CANCELLED,
    }:
        return _step_terminal(event_type, common, returncode)
    return None


def _step_terminal(event_type, common, returncode):
    if event_type is FleetRunEventType.STEP_SUCCEEDED:
        if returncode is None:
            raise ValueError("STEP_SUCCEEDED requires returncode")
        return StepSucceededEvent(event_type=event_type, returncode=returncode, **common)
    if event_type is FleetRunEventType.STEP_FAILED:
        if returncode is None:
            raise ValueError("STEP_FAILED requires returncode")
        return StepFailedEvent(event_type=event_type, returncode=returncode, **common)
    if event_type is FleetRunEventType.STEP_SKIPPED:
        return StepSkippedEvent(event_type=event_type, **common)
    return StepCancelledEvent(event_type=event_type, **common)


def _physical_event(
    event_type,
    root,
    step,
    attempt_number,
    physical_execution_id,
    pid,
    process_identity,
    returncode,
    termination_reason,
    output_count,
):
    if attempt_number is None:
        raise ValueError(f"{event_type.value} requires attempt_number")
    if physical_execution_id is None:
        raise ValueError(f"{event_type.value} requires physical_execution_id")
    common = {
        **step,
        "attempt_number": attempt_number,
        "step_attempt_id": fleet_step_attempt_id(
            root["operation_id"], step["step_id"], attempt_number
        ),
        "physical_execution_id": physical_execution_id,
    }
    if event_type is FleetRunEventType.PROCESS_STARTING:
        return ProcessStartingEvent(event_type=event_type, **common)
    output = _output_event(event_type, common, output_count)
    if output is not None:
        return output
    if pid is None:
        raise ValueError(f"{event_type.value} requires pid")
    return _started_process_event(
        event_type,
        {**common, "pid": pid, "process_identity": process_identity},
        returncode,
        termination_reason,
    )


def _output_event(event_type, common, output_count):
    if event_type is FleetRunEventType.OUTPUT_FINALIZED:
        if output_count is None or output_count < 1:
            raise ValueError("OUTPUT_FINALIZED requires output_count")
        return OutputFinalizedEvent(event_type=event_type, output_count=output_count, **common)
    if event_type is FleetRunEventType.OUTPUT_ABANDONED:
        return OutputAbandonedEvent(
            event_type=event_type, reason="Physical attempt output was not finalized", **common
        )
    if event_type is FleetRunEventType.OUTPUT_QUARANTINED:
        return OutputQuarantinedEvent(
            event_type=event_type,
            reason="Physical attempt state is unknown; output cannot be trusted",
            **common,
        )
    return None


def _started_process_event(event_type, common, returncode, termination_reason):
    if event_type is FleetRunEventType.PROCESS_STARTED:
        return ProcessStartedEvent(event_type=event_type, **common)
    if event_type is FleetRunEventType.TERMINATION_REQUESTED:
        if not termination_reason:
            raise ValueError("TERMINATION_REQUESTED requires termination_reason")
        return TerminationRequestedEvent(event_type=event_type, reason=termination_reason, **common)
    if event_type is FleetRunEventType.PROCESS_STATE_UNKNOWN:
        return ProcessStateUnknownEvent(
            event_type=event_type,
            reason="Controller restart cannot prove the state of the previously started process",
            **common,
        )
    if event_type is FleetRunEventType.PROCESS_EXITED:
        if returncode is None:
            raise ValueError("PROCESS_EXITED requires returncode")
        return ProcessExitedEvent(event_type=event_type, returncode=returncode, **common)
    if event_type is FleetRunEventType.DESCENDANTS_QUIESCENT:
        return DescendantsQuiescentEvent(event_type=event_type, **common)
    raise ValueError(f"Unsupported live execution lifecycle event: {event_type}")


def _execution_lifecycle_identity(event: FleetRunEvent) -> tuple[object, ...] | None:
    if isinstance(event, RepositoryStartedEvent):
        return (event.event_type, event.operation_id)
    if isinstance(event, (StepReachedEvent, StepStartedEvent)):
        return (event.event_type, event.operation_id, event.step_id)
    if isinstance(event, StepAttemptCreatedEvent):
        return (event.event_type, event.operation_id, event.step_id, event.attempt_number)
    if isinstance(
        event, (StepSucceededEvent, StepFailedEvent, StepSkippedEvent, StepCancelledEvent)
    ):
        return (event.event_type, event.operation_id, event.step_id)
    if isinstance(
        event,
        (
            ProcessStartingEvent,
            ProcessStartedEvent,
            TerminationRequestedEvent,
            ProcessStateUnknownEvent,
            ProcessExitedEvent,
            DescendantsQuiescentEvent,
            OutputFinalizedEvent,
            OutputAbandonedEvent,
            OutputQuarantinedEvent,
        ),
    ):
        return (event.event_type, event.step_attempt_id, event.physical_execution_id)
    return None


def _execution_lifecycle_payload(event: FleetRunEvent) -> dict[str, object]:
    return event.model_dump(
        mode="python",
        exclude={"sequence", "generated_at"},
    )


def reconciliation_lifecycle_event(event_type, common, operation_ids=(), reason=None):
    if event_type is FleetRunEventType.RECONCILIATION_REQUIRED:
        if not reason:
            raise ValueError("RECONCILIATION_REQUIRED requires a reason")
        from oh_goon.core.models.run_events import ReconciliationRequiredEvent

        return ReconciliationRequiredEvent(
            **common, event_type=event_type, operation_ids=operation_ids, reason=reason
        )
    if event_type is FleetRunEventType.RECONCILIATION_STARTED:
        from oh_goon.core.models.run_events import ReconciliationStartedEvent

        return ReconciliationStartedEvent(**common, event_type=event_type)
    if event_type is FleetRunEventType.RECONCILIATION_COMPLETED:
        from oh_goon.core.models.run_events import ReconciliationCompletedEvent

        return ReconciliationCompletedEvent(**common, event_type=event_type)
    if event_type is FleetRunEventType.RECONCILIATION_FAILED:
        if not reason:
            raise ValueError("RECONCILIATION_FAILED requires a reason")
        from oh_goon.core.models.run_events import ReconciliationFailedEvent

        return ReconciliationFailedEvent(**common, event_type=event_type, reason=reason)
    raise ValueError(f"Unsupported reconciliation lifecycle event: {event_type.value}")
