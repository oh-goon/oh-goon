from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from oh_goon.core.errors import InvalidRunTransition
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedNativeAgentWorkload
from oh_goon.core.models.run import CancellingFleetRun, FleetRunState, RunningFleetRun
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    FleetRunEvent,
    OutputAbandonedEvent,
    OutputFinalizedEvent,
    OutputQuarantinedEvent,
    PiSessionWriterObservedEvent,
    PiSessionWriterReservedEvent,
    ProcessExitedEvent,
    ProcessStartedEvent,
    ProcessStartingEvent,
    ProcessStateUnknownEvent,
    RepositoryStartedEvent,
    RunAdmittedEvent,
    StepAttemptCreatedEvent,
    StepCancelledEvent,
    StepFailedEvent,
    StepReachedEvent,
    StepSkippedEvent,
    StepStartedEvent,
    StepSucceededEvent,
    TerminationRequestedEvent,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import (
    fleet_step_attempt_id,
    pi_session_storage_id,
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


def validate_pi_session_writer(
    state: FleetRunState,
    event: PiSessionWriterReservedEvent | PiSessionWriterObservedEvent,
    history: Sequence[FleetRunEvent],
) -> None:
    if isinstance(event, PiSessionWriterReservedEvent):
        if not isinstance(state, RunningFleetRun):
            raise InvalidRunTransition("Pi writer reservation requires a running Fleet Run")
        _validate_pi_session_reservation(event, history)
        return
    if not isinstance(state, (RunningFleetRun, CancellingFleetRun)):
        raise InvalidRunTransition("Pi writer observation requires its current Fleet Run")
    reservation = _preceding_event(history, event.reservation_sequence)
    if not isinstance(reservation, PiSessionWriterReservedEvent):
        raise InvalidRunTransition("Pi writer observation requires the original reservation")
    if any(isinstance(item, PiSessionWriterObservedEvent) for item in history):
        raise InvalidRunTransition("Pi writer observation was already committed")
    quiet = _preceding_event(history, event.quiescent_sequence)
    processes = [
        item
        for item in history[reservation.sequence :]
        if type(item) is ProcessStartingEvent and _same_pi_attempt(item, reservation)
    ]
    if (
        not isinstance(quiet, DescendantsQuiescentEvent)
        or not _same_pi_attempt(quiet, reservation)
        or quiet.physical_execution_id != event.observation.physical_execution_id
        or len(processes) != 1
        or processes[0].physical_execution_id != quiet.physical_execution_id
        or processes[0].sequence >= quiet.sequence
    ):
        raise InvalidRunTransition("Pi writer requires exact original process-tree quiescence")
    if pi_writer_step_settled(reservation, history[reservation.sequence :]):
        raise InvalidRunTransition("Pi writer observation cannot follow Step settlement")
    if event.observation.persistence == "observed" and not any(
        isinstance(item, ProcessExitedEvent)
        and item.physical_execution_id == quiet.physical_execution_id
        for item in history[processes[0].sequence : quiet.sequence - 1]
    ):
        raise InvalidRunTransition("Pi file observation requires an observed original writer exit")


def _validate_pi_session_reservation(
    event: PiSessionWriterReservedEvent, history: Sequence[FleetRunEvent]
) -> None:
    admitted = history[0]
    if not isinstance(admitted, RunAdmittedEvent):
        raise InvalidRunTransition("Pi writer requires its original Fleet admission")
    target = admitted.target
    execution = admitted.execution
    if (
        not isinstance(execution, DirectFleetExecutionAdmission)
        or execution.rerun_from_run_id is not None
        or execution.concurrency is not None
        or not isinstance(target, FleetPlanTarget)
        or target.candidate_workspace is not None
        or target.agent_repair is not None
        or target.agent_continuation is not None
        or len(admitted.targets) != 1
        or admitted.targets[0].repository_id != event.repository_id
        or len(admitted.repository_admissions) != 1
        or event.step_id not in admitted.repository_admissions[0].active_step_ids
    ):
        raise InvalidRunTransition("Pi writer requires one fresh direct Fleet Agent attempt")
    steps = [step for step in target.plan.steps if isinstance(step, ResolvedAgentStep)]
    native = target.plan.native_agent
    if (
        len(steps) != 1
        or steps[0].step_id != event.step_id
        or steps[0].retry.max_attempts != 1
        or not isinstance(steps[0].workload, ResolvedNativeAgentWorkload)
        or native is None
        or steps[0].workload.native_agent != native
        or native.agent != "pi"
        or native.interactive
        or not native.profile.pi_rpc
        or native.connection is None
        or native.backend_configuration_id is None
        or native.session is None
        or native.session.mode != "new"
    ):
        raise InvalidRunTransition("Pi writer differs from the original admitted session and Step")
    attempt = _preceding_event(history, event.attempt_sequence)
    if not isinstance(attempt, StepAttemptCreatedEvent) or not _same_pi_attempt(attempt, event):
        raise InvalidRunTransition("Pi writer requires the exact original Step attempt")
    if event.storage_id != pi_session_storage_id(
        event.fleet_run_id, event.repository_id, event.step_id, event.attempt_number
    ):
        raise InvalidRunTransition("Pi writer storage differs from its original Step attempt")
    if any(isinstance(item, PiSessionWriterReservedEvent) for item in history):
        raise InvalidRunTransition("Pi writer cannot reserve its original lineage more than once")
    if pi_writer_step_settled(event, history[attempt.sequence :]):
        raise InvalidRunTransition("Pi writer cannot reserve a settled Step attempt")


def _preceding_event(history: Sequence[FleetRunEvent], sequence: int) -> FleetRunEvent | None:
    if 1 <= sequence <= len(history):
        return history[sequence - 1]
    return None


def _same_pi_attempt(
    event: StepAttemptCreatedEvent | ProcessStartingEvent, reservation: PiSessionWriterReservedEvent
) -> bool:
    return (
        event.repository_id == reservation.repository_id
        and event.operation_id == reservation.operation_id
        and event.step_id == reservation.step_id
        and event.attempt_number == reservation.attempt_number
    )


def pi_writer_step_settled(
    reservation: PiSessionWriterReservedEvent, events: Sequence[FleetRunEvent]
) -> bool:
    return any(
        isinstance(item, _STEP_TERMINAL)
        and item.repository_id == reservation.repository_id
        and item.operation_id == reservation.operation_id
        and item.step_id == reservation.step_id
        for item in events
    )


def validate_execution_lifecycle(
    state: FleetRunState, event: ExecutionLifecycleEvent, history: Sequence[FleetRunEvent]
) -> None:
    attempt = _attempt_scope(state, event, history)
    if isinstance(event, RepositoryStartedEvent):
        if any(isinstance(x, RepositoryStartedEvent) for x in attempt):
            raise InvalidRunTransition("Repository attempt cannot start more than once")
        return
    if isinstance(event, ProcessStartingEvent):
        for existing in history:
            if (
                isinstance(existing, ProcessStartingEvent)
                and existing.physical_execution_id == event.physical_execution_id
            ):
                if (
                    existing.repository_id,
                    existing.operation_id,
                    existing.step_id,
                    existing.step_order,
                    existing.attempt_number,
                    existing.step_attempt_id,
                ) != (
                    event.repository_id,
                    event.operation_id,
                    event.step_id,
                    event.step_order,
                    event.attempt_number,
                    event.step_attempt_id,
                ):
                    raise InvalidRunTransition(
                        "Physical execution identity cannot be rebound to another operation or Step scope"
                    )
        if event.step_id is None:
            # The Run and captured repository/operation identity above remain
            # authoritative during pre-effect preparation; no Step is invented.
            _physical_sequence(event, attempt)
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
    if isinstance(event, StepSucceededEvent):
        exited = {
            item.physical_execution_id for item in step if isinstance(item, ProcessExitedEvent)
        }
        if any(
            isinstance(item, ProcessStateUnknownEvent) and item.physical_execution_id not in exited
            for item in step
        ):
            raise InvalidRunTransition(
                "Unknown physical execution result cannot become Step success"
            )
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
    if (
        isinstance(event, StepCancelledEvent)
        and event.cancellation is not None
        and event.cancellation.settlement.observed_returncode is not None
        and not any(
            isinstance(item, ProcessExitedEvent)
            and item.physical_execution_id == event.cancellation.physical_execution_id
            and item.returncode == event.cancellation.settlement.observed_returncode
            for item in step
        )
    ):
        raise InvalidRunTransition(
            "Cancellation observed exit must match the original process exit"
        )
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
    if type(event) is ProcessStartingEvent:
        _require_single_pi_writer_launch(event, step)
    _physical_sequence(event, step)


def _require_single_pi_writer_launch(
    event: ProcessStartingEvent, step: Sequence[FleetRunEvent]
) -> None:
    reservation = next(
        (
            item
            for item in step
            if isinstance(item, PiSessionWriterReservedEvent) and _same_pi_attempt(event, item)
        ),
        None,
    )
    if reservation is not None and any(
        type(item) is ProcessStartingEvent
        and item.sequence > reservation.sequence
        and _same_pi_attempt(item, reservation)
        for item in step
    ):
        raise InvalidRunTransition("Original Pi writer cannot launch after crossing its launch cut")


def _physical_sequence(event: ProcessStartingEvent, history: Sequence[FleetRunEvent]) -> None:
    physical = [
        x
        for x in history
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
    owner = st["exited"] or st["unknown"]
    if owner is None:
        raise InvalidRunTransition(
            "DESCENDANTS_QUIESCENT requires matching PROCESS_EXITED or PROCESS_STATE_UNKNOWN"
        )
    if st["quiescent"] is not None:
        raise InvalidRunTransition(
            "Physical execution descendants cannot become quiescent more than once"
        )
    _same(event, owner, "DESCENDANTS_QUIESCENT process identity must match its original process")


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
