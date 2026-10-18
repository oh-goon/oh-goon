from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from oh_goon.core.game_tape_identity import repository_execution_id
from oh_goon.core.game_tape_lifecycle import run_status
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_events import (DescendantsQuiescentEvent,ProcessExitedEvent,OutputFinalizedEvent,OutputAbandonedEvent,OutputQuarantinedEvent,ProcessStartedEvent,TerminationRequestedEvent,ProcessStateUnknownEvent,ProcessStartingEvent,RepositoryStartedEvent,ReconciliationCompletedEvent,ReconciliationFailedEvent,ReconciliationRequiredEvent,ReconciliationStartedEvent,RunAdmittedEvent,RunCancelledEvent,RunCancelRequestedEvent,RunCompletedEvent,RunFailedEvent,RunStartedEvent,StepAttemptCreatedEvent,StepCancelledEvent,StepFailedEvent,StepReachedEvent,StepSkippedEvent,StepStartedEvent,StepSucceededEvent)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunEventType,GameTapeEventType,GameTapeStatus

@dataclass(frozen=True, slots=True)
class _PendingEvent:
    authority_sequence: int
    occurred_at: datetime
    event_type: GameTapeEventType
    status: GameTapeStatus | None
    source: Literal["run_lifecycle", "admitted_plan", "operation_report", "step_execution", "lifecycle_event"]
    message: str
    repository_index: int = -1
    repository: RepositoryIdentity | None = None
    repository_execution_id: str | None = None
    step_id: str | None = None
    step_order: int | None = None
    step_attempt_id: str | None = None
    attempt_number: int | None = None
    physical_execution_id: str | None = None
    pid: int | None = None
    process_identity: str | None = None
    returncode: int | None = None


def _live_lifecycle_events(detail: FleetRunDetail) -> list[_PendingEvent]:
    repositories={item.repository_id:item for item in detail.run.targets}
    events=[]
    for event in detail.lifecycle_events:
        repository=repositories.get(getattr(event,"repository_id",""))
        if repository is None:
            continue
        projected=_live_event(detail,event,repository)
        if projected is not None:
            events.append(projected)
    return events


def _live_event(detail,event,repository):
    execution_id=repository_execution_id(detail.run.fleet_run_id,repository.repository_id)
    index=detail.run.targets.index(repository)
    if isinstance(event,RepositoryStartedEvent):
        return _repo_event(event.sequence,event.generated_at,GameTapeEventType.REPOSITORY_STARTED,GameTapeStatus.RUNNING,"lifecycle_event","Repository execution started.",repository,index,execution_id)
    if isinstance(event,(StepReachedEvent,StepStartedEvent,StepAttemptCreatedEvent)):
        return _step_lifecycle_event(event,repository,index,execution_id)
    if isinstance(event,(StepSucceededEvent,StepFailedEvent,StepSkippedEvent,StepCancelledEvent)):
        return _step_terminal_event(event,repository,index,execution_id)
    if isinstance(event,(ProcessStartingEvent,ProcessStartedEvent,TerminationRequestedEvent,ProcessStateUnknownEvent,ProcessExitedEvent,DescendantsQuiescentEvent,OutputFinalizedEvent,OutputAbandonedEvent,OutputQuarantinedEvent)):
        return _physical_lifecycle_event(event,repository,index,execution_id)
    return None


def _step_lifecycle_event(event,repository,index,execution_id):
    if isinstance(event,StepAttemptCreatedEvent):
        return _step_pending(event.sequence,event.generated_at,GameTapeEventType.STEP_ATTEMPT_CREATED,GameTapeStatus.RUNNING,"lifecycle_event",f"Step attempt {event.attempt_number} created.",repository,index,execution_id,event.step_id,event.step_order,step_attempt_id=event.step_attempt_id,attempt_number=event.attempt_number)
    event_type=GameTapeEventType.STEP_REACHED if isinstance(event,StepReachedEvent) else GameTapeEventType.STEP_STARTED
    message="Sequential execution reached this admitted step." if isinstance(event,StepReachedEvent) else "Step execution started and may now cross the physical process boundary."
    return _step_pending(event.sequence,event.generated_at,event_type,GameTapeStatus.RUNNING,"lifecycle_event",message,repository,index,execution_id,event.step_id,event.step_order)


def _step_terminal_event(event,repository,index,execution_id):
    types={FleetRunEventType.STEP_SUCCEEDED:GameTapeEventType.STEP_PASSED,FleetRunEventType.STEP_FAILED:GameTapeEventType.STEP_FAILED,FleetRunEventType.STEP_SKIPPED:GameTapeEventType.STEP_SKIPPED,FleetRunEventType.STEP_CANCELLED:GameTapeEventType.STEP_CANCELLED}
    statuses={FleetRunEventType.STEP_SUCCEEDED:GameTapeStatus.PASSED,FleetRunEventType.STEP_FAILED:GameTapeStatus.FAILED,FleetRunEventType.STEP_SKIPPED:GameTapeStatus.SKIPPED,FleetRunEventType.STEP_CANCELLED:GameTapeStatus.CANCELLED}
    messages={FleetRunEventType.STEP_SUCCEEDED:"Step settled successfully.",FleetRunEventType.STEP_FAILED:f"Step settled with exit code {getattr(event,'returncode',1)}.",FleetRunEventType.STEP_SKIPPED:"Step settled without physical execution.",FleetRunEventType.STEP_CANCELLED:"Step execution was cancelled."}
    return _step_pending(event.sequence,event.generated_at,types[event.event_type],statuses[event.event_type],"lifecycle_event",messages[event.event_type],repository,index,execution_id,event.step_id,event.step_order,returncode=getattr(event,"returncode",None))


def _physical_lifecycle_event(event,repository,index,execution_id):
    types={FleetRunEventType.PROCESS_STARTING:GameTapeEventType.PROCESS_STARTING,FleetRunEventType.PROCESS_STARTED:GameTapeEventType.PROCESS_STARTED,FleetRunEventType.TERMINATION_REQUESTED:GameTapeEventType.TERMINATION_REQUESTED,FleetRunEventType.PROCESS_STATE_UNKNOWN:GameTapeEventType.PROCESS_STATE_UNKNOWN,FleetRunEventType.PROCESS_EXITED:GameTapeEventType.PROCESS_EXITED,FleetRunEventType.DESCENDANTS_QUIESCENT:GameTapeEventType.DESCENDANTS_QUIESCENT,FleetRunEventType.OUTPUT_FINALIZED:GameTapeEventType.OUTPUT_FINALIZED,FleetRunEventType.OUTPUT_ABANDONED:GameTapeEventType.OUTPUT_ABANDONED,FleetRunEventType.OUTPUT_QUARANTINED:GameTapeEventType.OUTPUT_QUARANTINED}
    messages={FleetRunEventType.PROCESS_STARTING:"Owned physical process launch is starting.",FleetRunEventType.PROCESS_STARTED:"Owned physical process started.",FleetRunEventType.TERMINATION_REQUESTED:getattr(event,"reason","Termination requested."),FleetRunEventType.PROCESS_STATE_UNKNOWN:getattr(event,"reason","Physical process state is unknown."),FleetRunEventType.PROCESS_EXITED:"Owned physical process exited.",FleetRunEventType.DESCENDANTS_QUIESCENT:"Owned physical process descendants are quiescent.",FleetRunEventType.OUTPUT_FINALIZED:f"{getattr(event,'output_count',0)} strict output(s) finalized for this physical execution.",FleetRunEventType.OUTPUT_ABANDONED:getattr(event,"reason","Physical attempt output was abandoned."),FleetRunEventType.OUTPUT_QUARANTINED:getattr(event,"reason","Physical attempt output was quarantined.")}
    return _step_pending(event.sequence,event.generated_at,types[event.event_type],GameTapeStatus.RUNNING,"lifecycle_event",messages[event.event_type],repository,index,execution_id,event.step_id,event.step_order,step_attempt_id=event.step_attempt_id,attempt_number=event.attempt_number,physical_execution_id=event.physical_execution_id,pid=getattr(event,"pid",None),process_identity=getattr(event,"process_identity",None),returncode=getattr(event,"returncode",None))


def _run_events(detail: FleetRunDetail) -> list[_PendingEvent]:
    events=[]
    for event in detail.lifecycle_events:
        events.extend(_run_event(detail,event))
    return events


def _run_event(detail,event):
    if isinstance(event,RunAdmittedEvent):
        admitted=[_PendingEvent(event.sequence,event.generated_at,GameTapeEventType.RUN_ADMITTED,GameTapeStatus.ADMITTED,"run_lifecycle","Run admitted with one immutable ordered execution plan.")]
        if isinstance(detail.run.target,FleetPlanTarget) and detail.run.target.plan.requirements:
            admitted.append(_PendingEvent(event.sequence,event.generated_at,GameTapeEventType.REQUIREMENTS_ADMITTED,GameTapeStatus.PASSED,"admitted_plan",f"{len(detail.run.target.plan.requirements)} repository requirements admitted."))
        return admitted
    simple={RunStartedEvent:(GameTapeEventType.RUN_STARTED,GameTapeStatus.RUNNING,"Sequential execution started."),RunCancelRequestedEvent:(GameTapeEventType.RUN_CANCEL_REQUESTED,GameTapeStatus.RUNNING,"Cancellation was explicitly requested."),ReconciliationStartedEvent:(GameTapeEventType.RECONCILIATION_STARTED,GameTapeStatus.RUNNING,"Controller recovery reconciliation started."),ReconciliationCompletedEvent:(GameTapeEventType.RECONCILIATION_COMPLETED,GameTapeStatus.PASSED,"Controller recovery reconciliation completed.")}
    for cls,(kind,status,message) in simple.items():
        if isinstance(event,cls): return [_PendingEvent(event.sequence,event.generated_at,kind,status,"run_lifecycle",message)]
    if isinstance(event,ReconciliationRequiredEvent): return [_PendingEvent(event.sequence,event.generated_at,GameTapeEventType.RECONCILIATION_REQUIRED,GameTapeStatus.ATTENTION_REQUIRED,"run_lifecycle",event.reason)]
    if isinstance(event,ReconciliationFailedEvent): return [_PendingEvent(event.sequence,event.generated_at,GameTapeEventType.RECONCILIATION_FAILED,GameTapeStatus.FAILED,"run_lifecycle",event.reason)]
    if isinstance(event,(RunCompletedEvent,RunCancelledEvent,RunFailedEvent)):
        status=run_status(detail.run); return [_PendingEvent(event.sequence,event.generated_at,_run_terminal_type(status),status,"run_lifecycle",_run_terminal_message(status))]
    return []


def _repo_event(
    authority_sequence: int,
    occurred_at: datetime,
    event_type: GameTapeEventType,
    status: GameTapeStatus,
    source: Literal["run_lifecycle", "admitted_plan", "operation_report", "step_execution", "lifecycle_event"],
    message: str,
    repository: RepositoryIdentity,
    repository_index: int,
    repository_execution_id: str,
) -> _PendingEvent:
    return _PendingEvent(
        authority_sequence=authority_sequence,
        occurred_at=occurred_at,
        event_type=event_type,
        status=status,
        source=source,
        message=message,
        repository_index=repository_index,
        repository=repository,
        repository_execution_id=repository_execution_id,
    )


def _step_pending(
    authority_sequence: int,
    occurred_at: datetime,
    event_type: GameTapeEventType,
    status: GameTapeStatus,
    source: Literal["run_lifecycle", "admitted_plan", "operation_report", "step_execution", "lifecycle_event"],
    message: str,
    repository: RepositoryIdentity,
    repository_index: int,
    repository_execution_id: str,
    step_id: str,
    step_order: int,
    *,
    step_attempt_id: str | None = None,
    attempt_number: int | None = None,
    physical_execution_id: str | None = None,
    pid: int | None = None,
    process_identity: str | None = None,
    returncode: int | None = None,
) -> _PendingEvent:
    return _PendingEvent(
        authority_sequence=authority_sequence,
        occurred_at=occurred_at,
        event_type=event_type,
        status=status,
        source=source,
        message=message,
        repository_index=repository_index,
        repository=repository,
        repository_execution_id=repository_execution_id,
        step_id=step_id,
        step_order=step_order,
        step_attempt_id=step_attempt_id,
        attempt_number=attempt_number,
        physical_execution_id=physical_execution_id,
        pid=pid,
        process_identity=process_identity,
        returncode=returncode,
    )



def _run_terminal_type(status: GameTapeStatus) -> GameTapeEventType:
    event_type = {
        GameTapeStatus.PASSED: GameTapeEventType.RUN_PASSED,
        GameTapeStatus.ATTENTION_REQUIRED: GameTapeEventType.RUN_ATTENTION_REQUIRED,
        GameTapeStatus.FAILED: GameTapeEventType.RUN_FAILED,
        GameTapeStatus.CANCELLED: GameTapeEventType.RUN_CANCELLED,
        GameTapeStatus.INTERRUPTED: GameTapeEventType.RUN_INTERRUPTED,
    }.get(status)
    if event_type is None:
        raise AssertionError(f"Non-terminal Run status reached terminal ledger projection: {status.value}")
    return event_type

def _run_terminal_message(status: GameTapeStatus) -> str:
    message = {
        GameTapeStatus.PASSED: "Run completed successfully.",
        GameTapeStatus.ATTENTION_REQUIRED: "Run completed with attention required.",
        GameTapeStatus.FAILED: "Run failed.",
        GameTapeStatus.CANCELLED: "Run was cancelled.",
        GameTapeStatus.INTERRUPTED: "Run was interrupted without a normal terminal execution outcome.",
    }.get(status)
    if message is None:
        raise AssertionError(f"Non-terminal Run status reached terminal ledger projection: {status.value}")
    return message

def lifecycle_events(detail: FleetRunDetail) -> list[_PendingEvent]:
    events=_run_events(detail)
    events.extend(_live_lifecycle_events(detail))
    return events

PendingEvent=_PendingEvent
