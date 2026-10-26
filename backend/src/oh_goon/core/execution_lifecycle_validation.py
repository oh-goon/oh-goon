from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from oh_goon.core.errors import InvalidRunTransition
from oh_goon.core.models.run import CancellingFleetRun, FleetRunState, RunningFleetRun
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
from oh_goon.core.product_identity import (
    fleet_step_attempt_id,
    validate_repository_attempt_operation_id,
)

type StepTerminalLifecycleEvent = (
    StepSucceededEvent | StepFailedEvent | StepSkippedEvent | StepCancelledEvent
)
type StepLifecycleEvent = (
    StepReachedEvent
    | StepStartedEvent
    | StepAttemptCreatedEvent
    | ProcessStartingEvent
    | StepTerminalLifecycleEvent
)
type ExecutionLifecycleEvent = RepositoryStartedEvent | StepLifecycleEvent


class _PhysicalState(TypedDict):
    starting: ProcessStartingEvent | None
    started: ProcessStartedEvent | None
    unknown: ProcessStateUnknownEvent | None
    termination: TerminationRequestedEvent | None
    exited: ProcessExitedEvent | None
    quiescent: DescendantsQuiescentEvent | None


_STEP_TERMINAL = (StepSucceededEvent, StepFailedEvent, StepSkippedEvent, StepCancelledEvent)
_OUTPUT = (OutputFinalizedEvent, OutputAbandonedEvent, OutputQuarantinedEvent)


def validate_execution_lifecycle(
    state: FleetRunState, event: ExecutionLifecycleEvent, history: Sequence[FleetRunEvent]
) -> None:
    attempt = _attempt_scope(state, event, history)
    if isinstance(event, RepositoryStartedEvent):
        if any(isinstance(x, RepositoryStartedEvent) for x in attempt):
            raise InvalidRunTransition("Repository attempt cannot start more than once")
        return
    if not any(isinstance(x, RepositoryStartedEvent) for x in attempt):
        raise InvalidRunTransition(f"{event.event_type.value} requires matching REPOSITORY_STARTED")
    step = [x for x in attempt if getattr(x, "step_id", None) == getattr(event, "step_id", None)]
    _validate_step(event, step)


def _attempt_scope(
    state: FleetRunState, event: ExecutionLifecycleEvent, history: Sequence[FleetRunEvent]
) -> list[FleetRunEvent]:
    if not isinstance(state, (RunningFleetRun, CancellingFleetRun)):
        raise InvalidRunTransition(
            f"Cannot apply {event.event_type.value} from {state.status.value}"
        )
    if not any(t.repository_id == event.repository_id for t in state.targets):
        raise InvalidRunTransition(
            "Execution lifecycle repository must belong to the owning fleet run"
        )
    try:
        validate_repository_attempt_operation_id(
            state.fleet_run_id, event.repository_id, event.operation_id
        )
    except ValueError as exc:
        raise InvalidRunTransition(str(exc)) from exc
    return [
        x
        for x in history
        if getattr(x, "repository_id", None) == event.repository_id
        and getattr(x, "operation_id", None) == event.operation_id
    ]


def _validate_step(event: StepLifecycleEvent, step: Sequence[FleetRunEvent]) -> None:
    if isinstance(event, StepReachedEvent):
        if any(isinstance(x, StepReachedEvent) for x in step):
            raise InvalidRunTransition(
                "Step cannot be reached more than once per repository attempt"
            )
        return
    reached = next((x for x in step if isinstance(x, StepReachedEvent)), None)
    if reached is None:
        raise InvalidRunTransition("STEP_STARTED requires matching STEP_REACHED")
    if reached.step_order != event.step_order:
        raise InvalidRunTransition(f"{event.event_type.value} step_order must match STEP_REACHED")
    if isinstance(event, _STEP_TERMINAL):
        return _settlement(event, step)
    if isinstance(event, StepAttemptCreatedEvent):
        return _attempt_created(event, step)
    if isinstance(event, ProcessStartingEvent):
        return _physical(event, step)
    if any(isinstance(x, StepStartedEvent) for x in step):
        raise InvalidRunTransition("Step cannot start more than once per repository attempt")


def _settlement(event: StepTerminalLifecycleEvent, step: Sequence[FleetRunEvent]) -> None:
    if any(isinstance(x, _STEP_TERMINAL) for x in step):
        raise InvalidRunTransition("Step cannot settle more than once per repository attempt")
    if (
        isinstance(event, StepCancelledEvent)
        and event.cancellation is not None
        and event.cancellation.physical_execution_id is not None
        and not any(
            type(item) is ProcessStartedEvent
            and item.physical_execution_id == event.cancellation.physical_execution_id
            for item in step
        )
    ):
        raise InvalidRunTransition("Cancellation evidence must belong to the original Step process")
    unquiet = [
        x
        for x in step
        if type(x) is ProcessStartedEvent
        and not any(
            isinstance(y, DescendantsQuiescentEvent)
            and y.physical_execution_id == x.physical_execution_id
            for y in step
        )
    ]
    if unquiet:
        raise InvalidRunTransition(
            f"{event.event_type.value} cannot settle while an owned physical process has not reached DESCENDANTS_QUIESCENT"
        )


def _attempt_created(event: StepAttemptCreatedEvent, step: Sequence[FleetRunEvent]) -> None:
    if not any(isinstance(x, StepStartedEvent) for x in step):
        raise InvalidRunTransition("STEP_ATTEMPT_CREATED requires matching STEP_STARTED")
    if event.step_attempt_id != fleet_step_attempt_id(
        event.operation_id, event.step_id, event.attempt_number
    ):
        raise InvalidRunTransition(
            "STEP_ATTEMPT_CREATED step_attempt_id must match repository/Step/attempt identity"
        )
    if event.attempt_number != sum(isinstance(x, StepAttemptCreatedEvent) for x in step) + 1:
        raise InvalidRunTransition("STEP_ATTEMPT_CREATED attempt_number must advance contiguously")


def _physical(event: ProcessStartingEvent, step: Sequence[FleetRunEvent]) -> None:
    attempt = next(
        (
            x
            for x in step
            if isinstance(x, StepAttemptCreatedEvent) and x.attempt_number == event.attempt_number
        ),
        None,
    )
    if attempt is None:
        raise InvalidRunTransition(
            f"{event.event_type.value} requires matching STEP_ATTEMPT_CREATED"
        )
    if attempt.step_attempt_id != event.step_attempt_id:
        raise InvalidRunTransition(
            f"{event.event_type.value} step_attempt_id must match its Step attempt"
        )
    physical = [
        x
        for x in step
        if isinstance(x, ProcessStartingEvent)
        and x.physical_execution_id == event.physical_execution_id
    ]
    state = _physical_state(physical)
    if type(event) is ProcessStartingEvent:
        return _starting(physical)
    if type(event) is ProcessStartedEvent:
        return _started(state)
    if isinstance(event, TerminationRequestedEvent):
        return _termination_requested(event, state)
    if isinstance(event, _OUTPUT):
        return _output(event, physical, state)
    _settle_physical(event, state)


def _settle_physical(event: ProcessStartingEvent, state: _PhysicalState) -> None:
    if isinstance(event, ProcessStateUnknownEvent):
        return _unknown(event, state)
    if isinstance(event, ProcessExitedEvent):
        return _exit(event, state)
    if isinstance(event, DescendantsQuiescentEvent):
        return _quiescent(event, state)


def _physical_state(items: Sequence[ProcessStartingEvent]) -> _PhysicalState:
    return {
        "starting": next((x for x in items if type(x) is ProcessStartingEvent), None),
        "started": next((x for x in items if type(x) is ProcessStartedEvent), None),
        "unknown": next((x for x in items if isinstance(x, ProcessStateUnknownEvent)), None),
        "termination": next((x for x in items if isinstance(x, TerminationRequestedEvent)), None),
        "exited": next((x for x in items if isinstance(x, ProcessExitedEvent)), None),
        "quiescent": next((x for x in items if isinstance(x, DescendantsQuiescentEvent)), None),
    }


def _starting(items: Sequence[ProcessStartingEvent]) -> None:
    if items:
        raise InvalidRunTransition(
            "Physical execution start preparation cannot be recorded more than once"
        )


def _started(st: _PhysicalState) -> None:
    if st["starting"] is None:
        raise InvalidRunTransition("PROCESS_STARTED requires matching PROCESS_STARTING")
    if st["started"] is not None or st["exited"] is not None or st["quiescent"] is not None:
        raise InvalidRunTransition("Physical execution cannot start more than once")


def _output(
    event: OutputFinalizedEvent | OutputAbandonedEvent | OutputQuarantinedEvent,
    items: Sequence[ProcessStartingEvent],
    st: _PhysicalState,
) -> None:
    if any(isinstance(x, _OUTPUT) for x in items):
        raise InvalidRunTransition(
            "Physical execution output disposition cannot be recorded more than once"
        )
    if isinstance(event, OutputQuarantinedEvent):
        if st["unknown"] is None:
            raise InvalidRunTransition("OUTPUT_QUARANTINED requires matching PROCESS_STATE_UNKNOWN")
        return
    if st["unknown"] is not None:
        if isinstance(event, OutputAbandonedEvent):
            raise InvalidRunTransition(
                "Unknown physical execution output must be quarantined, not abandoned"
            )
        raise InvalidRunTransition("Unknown physical execution cannot finalize output")
    if st["quiescent"] is None:
        raise InvalidRunTransition(
            f"{event.event_type.value} requires matching DESCENDANTS_QUIESCENT"
        )


def _unknown(event: ProcessStateUnknownEvent, st: _PhysicalState) -> None:
    if st["started"] is None:
        raise InvalidRunTransition("PROCESS_STATE_UNKNOWN requires matching PROCESS_STARTED")
    if st["unknown"] is not None or st["exited"] is not None or st["quiescent"] is not None:
        raise InvalidRunTransition(
            "Physical execution unknown state cannot be recorded after settlement"
        )
    _same(
        event,
        st["started"],
        "PROCESS_STATE_UNKNOWN physical process identity must match PROCESS_STARTED",
    )


def _exit(event: ProcessExitedEvent, st: _PhysicalState) -> None:
    if st["started"] is None:
        raise InvalidRunTransition("PROCESS_EXITED requires matching PROCESS_STARTED")
    if st["exited"] is not None:
        raise InvalidRunTransition("Physical execution cannot exit more than once")
    _same(
        event, st["started"], "PROCESS_EXITED physical process identity must match PROCESS_STARTED"
    )


def _quiescent(event: DescendantsQuiescentEvent, st: _PhysicalState) -> None:
    if st["exited"] is None:
        raise InvalidRunTransition("DESCENDANTS_QUIESCENT requires matching PROCESS_EXITED")
    if st["quiescent"] is not None:
        raise InvalidRunTransition(
            "Physical execution descendants cannot become quiescent more than once"
        )
    _same(event, st["exited"], "DESCENDANTS_QUIESCENT process identity must match PROCESS_EXITED")


def _same(event: ProcessStartedEvent, owner: ProcessStartedEvent, message: str) -> None:
    if event.pid != owner.pid or event.process_identity != owner.process_identity:
        raise InvalidRunTransition(message)


def _termination_requested(event: TerminationRequestedEvent, st: _PhysicalState) -> None:
    if st["started"] is None:
        raise InvalidRunTransition("TERMINATION_REQUESTED requires matching PROCESS_STARTED")
    if st.get("termination") is not None:
        raise InvalidRunTransition(
            "Physical execution termination cannot be requested more than once"
        )
    if st["exited"] is not None or st["quiescent"] is not None:
        raise InvalidRunTransition(
            "TERMINATION_REQUESTED cannot be recorded after process settlement"
        )
    _same(
        event,
        st["started"],
        "TERMINATION_REQUESTED physical process identity must match PROCESS_STARTED",
    )
