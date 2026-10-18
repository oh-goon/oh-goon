from __future__ import annotations

from datetime import datetime

from oh_goon.core.execution_lifecycle_ledger import PendingEvent as _PendingEvent
from oh_goon.core.execution_lifecycle_ledger import _repo_event, _step_pending, lifecycle_events
from oh_goon.core.game_tape_identity import repository_execution_id, step_execution_id
from oh_goon.core.game_tape_lifecycle import repository_status
from oh_goon.core.game_tape_timing import repository_time_bounds
from oh_goon.core.models.fleet_plan_evidence import (
    FleetPlanResult,
    PlanStepExecution,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
    PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
    PLAN_TIMEOUT_RETURN_CODE,
)
from oh_goon.core.models.game_tape import GameTapeEvent
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_events import (
    RepositoryCompletedEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary import GameTapeEventType, GameTapeStatus

_EVENT_PRIORITY = {
    GameTapeEventType.RUN_ADMITTED: 10,
    GameTapeEventType.REQUIREMENTS_ADMITTED: 20,
    GameTapeEventType.RUN_STARTED: 30,
    GameTapeEventType.RUN_CANCEL_REQUESTED: 40,
    GameTapeEventType.RECONCILIATION_REQUIRED: 50,
    GameTapeEventType.RECONCILIATION_STARTED: 60,
    GameTapeEventType.RECONCILIATION_COMPLETED: 10_100,
    GameTapeEventType.RECONCILIATION_FAILED: 10_100,
    GameTapeEventType.REPOSITORY_STARTED: 100,
    GameTapeEventType.REPOSITORY_PASSED: 9_000,
    GameTapeEventType.REPOSITORY_ATTENTION_REQUIRED: 9_000,
    GameTapeEventType.REPOSITORY_FAILED: 9_000,
    GameTapeEventType.REPOSITORY_NOT_RUN: 9_000,
    GameTapeEventType.RUN_PASSED: 10_000,
    GameTapeEventType.RUN_ATTENTION_REQUIRED: 10_000,
    GameTapeEventType.RUN_FAILED: 10_000,
    GameTapeEventType.RUN_CANCELLED: 10_000,
    GameTapeEventType.RUN_INTERRUPTED: 10_000,
}

_STEP_EVENT_STAGE = {
    GameTapeEventType.STEP_REACHED: 1,
    GameTapeEventType.STEP_STARTED: 2,
    GameTapeEventType.STEP_ATTEMPT_CREATED: 3,
    GameTapeEventType.PROCESS_STARTING: 4,
    GameTapeEventType.PROCESS_STARTED: 5,
    GameTapeEventType.TERMINATION_REQUESTED: 6,
    GameTapeEventType.PROCESS_STATE_UNKNOWN: 6,
    GameTapeEventType.OUTPUT_QUARANTINED: 7,
    GameTapeEventType.OUTPUT_ABANDONED: 8,
    GameTapeEventType.OUTPUT_FINALIZED: 8,
    GameTapeEventType.PROCESS_EXITED: 6,
    GameTapeEventType.DESCENDANTS_QUIESCENT: 7,
    GameTapeEventType.STEP_PASSED: 8,
    GameTapeEventType.STEP_FAILED: 8,
    GameTapeEventType.STEP_SKIPPED: 8,
    GameTapeEventType.STEP_CANCELLED: 8,
    GameTapeEventType.STEP_NOT_REACHED: 8,
}


def project_execution_ledger(detail: FleetRunDetail) -> tuple[GameTapeEvent, ...]:
    """Project Game Tape order from canonical journal sequence, never wall-clock order."""

    pending = lifecycle_events(detail)
    target = detail.run.target
    if isinstance(target, FleetPlanTarget):
        reports = {report.repository.repository_id: report for report in detail.operations}
        for index, repository in enumerate(detail.run.targets):
            pending.extend(
                _repository_events(
                    detail,
                    target,
                    repository,
                    reports.get(repository.repository_id),
                    index,
                )
            )
    pending.sort(key=_event_sort_key)
    return tuple(
        _materialize(detail.run.fleet_run_id, event, sequence)
        for sequence, event in enumerate(pending, 1)
    )


def _repository_events(
    detail: FleetRunDetail,
    target: FleetPlanTarget,
    repository: RepositoryIdentity,
    report: OperationReport | None,
    repository_index: int,
) -> list[_PendingEvent]:
    execution_id = repository_execution_id(detail.run.fleet_run_id, repository.repository_id)
    result = _plan_result(report)
    if report is None:
        return _not_run_repository_events(
            detail,
            target,
            repository,
            repository_index,
            execution_id,
        )
    completion = next(
        (
            event
            for event in detail.lifecycle_events
            if isinstance(event, RepositoryCompletedEvent)
            and event.operation_id == report.provenance.operation_id
        ),
        None,
    )
    if completion is None:
        # A terminal result is not authority for missing historical lifecycle facts.
        return []
    _, finished_at = repository_time_bounds(report, result)
    events = []
    executions = {step.step_id: step for step in result.steps} if result is not None else {}
    for order, step in enumerate(target.plan.steps, 1):
        execution = executions.get(step.step_id)
        events.extend(
            _step_events(
                detail,
                repository,
                repository_index,
                execution_id,
                step.step_id,
                order,
                execution,
                finished_at,
                completion.sequence,
            )
        )
    status = repository_status(report, terminal=detail.run.status.terminal)
    events.append(
        _repo_event(
            completion.sequence,
            finished_at,
            _repository_terminal_type(status),
            status,
            "operation_report",
            _repository_terminal_message(status),
            repository,
            repository_index,
            execution_id,
        )
    )
    return events


def _not_run_repository_events(
    detail: FleetRunDetail,
    target: FleetPlanTarget,
    repository: RepositoryIdentity,
    repository_index: int,
    execution_id: str,
) -> list[_PendingEvent]:
    terminal = next(
        (
            event
            for event in reversed(detail.lifecycle_events)
            if isinstance(event, (RunCompletedEvent, RunCancelledEvent, RunFailedEvent))
        ),
        None,
    )
    if terminal is None:
        return []
    events = [
        _step_pending(
            terminal.sequence,
            terminal.generated_at,
            GameTapeEventType.STEP_NOT_REACHED,
            GameTapeStatus.NOT_RUN,
            "admitted_plan",
            "Step was admitted but execution ended before it was reached.",
            repository,
            repository_index,
            execution_id,
            step.step_id,
            order,
        )
        for order, step in enumerate(target.plan.steps, 1)
    ]
    events.append(
        _repo_event(
            terminal.sequence,
            terminal.generated_at,
            GameTapeEventType.REPOSITORY_NOT_RUN,
            GameTapeStatus.NOT_RUN,
            "admitted_plan",
            "Repository was admitted but no canonical operation result was recorded.",
            repository,
            repository_index,
            execution_id,
        )
    )
    return events


def _step_events(
    detail: FleetRunDetail,
    repository: RepositoryIdentity,
    repository_index: int,
    repository_execution_id: str,
    step_id: str,
    order: int,
    execution: PlanStepExecution | None,
    repository_finished_at: datetime,
    authority_sequence: int,
) -> list[_PendingEvent]:
    if execution is None:
        if not detail.run.status.terminal:
            return []
        return [
            _step_pending(
                authority_sequence,
                repository_finished_at,
                GameTapeEventType.STEP_NOT_REACHED,
                GameTapeStatus.NOT_RUN,
                "admitted_plan",
                "Step was admitted but no canonical step execution result exists.",
                repository,
                repository_index,
                repository_execution_id,
                step_id,
                order,
            )
        ]
    return _executed_step_events(
        detail,
        repository,
        repository_index,
        repository_execution_id,
        step_id,
        order,
        execution,
        repository_finished_at,
        authority_sequence,
    )


def _executed_step_events(
    detail: FleetRunDetail,
    repository: RepositoryIdentity,
    repository_index: int,
    repository_execution_id: str,
    step_id: str,
    order: int,
    execution: PlanStepExecution,
    repository_finished_at: datetime,
    authority_sequence: int,
) -> list[_PendingEvent]:
    # Terminal Step lifecycle is canonical journal authority. Operation evidence describes
    # the result but must never reconstruct a missing STEP_* terminal transition.
    return []


def _plan_result(report: OperationReport | None) -> FleetPlanResult | None:
    if report is None or not isinstance(report.evidence, FleetPlanOperationEvidence):
        return None
    return report.evidence.plan


def _run_terminal_type(status: GameTapeStatus) -> GameTapeEventType:
    event_type = {
        GameTapeStatus.PASSED: GameTapeEventType.RUN_PASSED,
        GameTapeStatus.ATTENTION_REQUIRED: GameTapeEventType.RUN_ATTENTION_REQUIRED,
        GameTapeStatus.FAILED: GameTapeEventType.RUN_FAILED,
        GameTapeStatus.CANCELLED: GameTapeEventType.RUN_CANCELLED,
        GameTapeStatus.INTERRUPTED: GameTapeEventType.RUN_INTERRUPTED,
    }.get(status)
    if event_type is None:
        raise AssertionError(
            f"Non-terminal Run status reached terminal ledger projection: {status.value}"
        )
    return event_type


def _repository_terminal_type(status: GameTapeStatus) -> GameTapeEventType:
    event_type = {
        GameTapeStatus.PASSED: GameTapeEventType.REPOSITORY_PASSED,
        GameTapeStatus.ATTENTION_REQUIRED: GameTapeEventType.REPOSITORY_ATTENTION_REQUIRED,
        GameTapeStatus.FAILED: GameTapeEventType.REPOSITORY_FAILED,
    }.get(status)
    if event_type is None:
        raise AssertionError(
            f"Non-terminal Repository status reached terminal ledger projection: {status.value}"
        )
    return event_type


def _step_terminal_type(status: GameTapeStatus) -> GameTapeEventType:
    event_type = {
        GameTapeStatus.PASSED: GameTapeEventType.STEP_PASSED,
        GameTapeStatus.FAILED: GameTapeEventType.STEP_FAILED,
        GameTapeStatus.SKIPPED: GameTapeEventType.STEP_SKIPPED,
    }.get(status)
    if event_type is None:
        raise AssertionError(
            f"Non-terminal Step status reached terminal ledger projection: {status.value}"
        )
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
        raise AssertionError(
            f"Non-terminal Run status reached terminal ledger projection: {status.value}"
        )
    return message


def _repository_terminal_message(status: GameTapeStatus) -> str:
    if status is GameTapeStatus.PASSED:
        return "Repository execution passed."
    if status is GameTapeStatus.ATTENTION_REQUIRED:
        return "Repository execution completed with attention required."
    if status is GameTapeStatus.FAILED:
        return "Repository execution failed."
    raise AssertionError(
        f"Non-terminal Repository status reached terminal ledger projection: {status.value}"
    )


def _step_terminal_message(status: GameTapeStatus, execution: PlanStepExecution) -> str:
    if status is GameTapeStatus.PASSED:
        return "Step passed."
    if status is GameTapeStatus.SKIPPED:
        return getattr(execution, "reason", None) or "Step was explicitly skipped."
    if status is not GameTapeStatus.FAILED:
        raise AssertionError(
            f"Non-terminal Step status reached terminal ledger projection: {status.value}"
        )
    returncode = getattr(execution, "returncode", 1)
    if returncode == PLAN_TIMEOUT_RETURN_CODE:
        return "Step timed out."
    if returncode == PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE:
        return "Step executable could not be run."
    if returncode == PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE:
        return "Step executable was not found."
    return f"Step failed with exit code {returncode}."


def _event_sort_key(event: _PendingEvent) -> tuple[int, int, int, str]:
    return (
        event.authority_sequence,
        _event_rank(event),
        event.repository_index,
        event.step_id or "",
    )


def _event_rank(event: _PendingEvent) -> int:
    stage = _STEP_EVENT_STAGE.get(event.event_type)
    if stage is not None:
        return 1_000 + (event.step_order or 0) * 10 + stage
    return _EVENT_PRIORITY[event.event_type]


def _materialize(fleet_run_id: str, event: _PendingEvent, sequence: int) -> GameTapeEvent:
    repository = event.repository
    step_id = event.step_id
    step_identity = (
        step_execution_id(fleet_run_id, repository.repository_id, step_id)
        if repository is not None and step_id is not None
        else None
    )
    payload = {
        "fleet_run_id": fleet_run_id,
        "event_type": event.event_type.value,
        "authority_sequence": event.authority_sequence,
        "repository_execution_id": event.repository_execution_id,
        "step_execution_id": step_identity,
        "step_attempt_id": event.step_attempt_id,
        "physical_execution_id": event.physical_execution_id,
    }
    return GameTapeEvent(
        sequence=sequence,
        authority_sequence=event.authority_sequence,
        event_id=stable_id("game-tape-event", payload),
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        status=event.status,
        repository_execution_id=event.repository_execution_id,
        repository_id=repository.repository_id if repository is not None else None,
        repository_name=repository.name if repository is not None else None,
        step_execution_id=step_identity,
        step_id=step_id,
        step_order=event.step_order,
        step_attempt_id=event.step_attempt_id,
        attempt_number=event.attempt_number,
        physical_execution_id=event.physical_execution_id,
        pid=event.pid,
        process_identity=event.process_identity,
        returncode=event.returncode,
        source=event.source,
        message=event.message,
    )
